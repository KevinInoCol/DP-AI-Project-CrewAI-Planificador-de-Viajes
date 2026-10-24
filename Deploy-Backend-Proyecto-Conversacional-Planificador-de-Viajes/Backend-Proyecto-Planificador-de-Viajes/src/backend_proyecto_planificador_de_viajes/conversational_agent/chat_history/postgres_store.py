"""
Checkpointer de LangGraph sobre Postgres: la memoria de conversacion del agente.

Guarda snapshots del estado del grafo por `thread_id`, que es lo que permite que
el agente recuerde el hilo entre requests HTTP y entre reinicios de uvicorn.

Dos decisiones que no son obvias:

- Usamos AsyncPostgresSaver, no PostgresSaver. El endpoint /chat es async y el
  crew corre con kickoff_async, asi que el grafo se ejecuta con .ainvoke(), y en
  ese modo LangGraph llama a los metodos async del checkpointer (aget_tuple,
  aput...). Con el saver sincrono revienta.

- Usamos un pool con `check`, no una conexion suelta. AsyncPostgresSaver sobre
  una unica conexion funciona hasta que el proveedor la cierra por inactividad;
  psycopg no reconecta y a partir de ahi TODA invocacion falla con
  "the connection is closed" hasta reiniciar el proceso.

Este modulo NO es legible con SQL: los mensajes viven en BYTEA. Para leer una
conversacion con un SELECT esta conversation_log.py, que es la otra mitad.
"""

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
# Escotilla opcional: si esta puesta, gana sobre las cinco de arriba. Util al
# desplegar, donde el proveedor suele dar la cadena ya montada.
DATABASE_URL = os.getenv("DATABASE_URL")

# Tamano del pool. El agente conversacional es de baja concurrencia (un turno
# por usuario cada varios segundos); 10 conexiones sobran.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
# Si la base no responde, fallar en 5s en vez de colgar el arranque ~30s.
CONNECT_TIMEOUT = 5

# Singletons de modulo: se abren UNA vez en el arranque (lifespan de FastAPI).
_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None


def build_database_url() -> str:
    """Arma la cadena de conexion a partir de las variables del .env.

    La contrasena va con quote_plus porque un `@`, `#`, `/` o `:` literal parte
    la URL por donde no toca y el error que sale ("could not translate host
    name") no se parece en nada a la causa real.
    """
    if DATABASE_URL:
        return DATABASE_URL

    faltantes = [
        nombre
        for nombre, valor in (
            ("DB_USER", DB_USER),
            ("DB_PASSWORD", DB_PASSWORD),
            ("DB_HOST", DB_HOST),
            ("DB_NAME", DB_NAME),
        )
        if not valor
    ]
    if faltantes:
        raise RuntimeError(
            "Faltan variables de Postgres en el .env: "
            + ", ".join(faltantes)
            + ". El agente conversacional guarda el historial en Postgres."
        )

    return (
        f"postgresql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )


def _kwargs_de_conexion() -> dict:
    """Parametros que necesita cada conexion del pool.

    prepare_threshold=0 es obligatorio detras de un pooler en modo transaccion
    (Supabase 6543, PgBouncer): sin el, psycopg reutiliza prepared statements
    que el pooler ya rota y salta "prepared statement already exists".
    """
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "connect_timeout": CONNECT_TIMEOUT,
    }


async def abrir_memoria() -> AsyncPostgresSaver:
    """Abre el pool y deja el checkpointer listo. Idempotente.

    Crea las tablas del checkpointer (checkpoints, checkpoint_blobs,
    checkpoint_writes, checkpoint_migrations) la primera vez que corre.
    """
    global _pool, _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    _pool = AsyncConnectionPool(
        conninfo=build_database_url(),
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        kwargs=_kwargs_de_conexion(),
        timeout=CONNECT_TIMEOUT,
        # Valida la conexion ANTES de entregarla: esto es lo que evita el
        # "connection is closed" silencioso tras un rato sin trafico.
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    await _pool.open(wait=True, timeout=CONNECT_TIMEOUT)

    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()

    print("🧠 Memoria conversacional lista (Postgres).")
    return _checkpointer


def get_checkpointer() -> AsyncPostgresSaver:
    """Devuelve el checkpointer ya abierto. Falla si nadie llamo a abrir_memoria()."""
    if _checkpointer is None:
        raise RuntimeError(
            "La memoria no esta inicializada. Llama a abrir_memoria() en el "
            "arranque de la app (lifespan de FastAPI)."
        )
    return _checkpointer


def get_pool() -> AsyncConnectionPool | None:
    """Pool compartido, para que conversation_log.py no abra el suyo."""
    return _pool


async def cerrar_memoria() -> None:
    """Cierra el pool al apagar la app."""
    global _pool, _checkpointer

    if _pool is not None:
        await _pool.close()
    _pool = None
    _checkpointer = None
