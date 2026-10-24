"""
Log plano de la conversacion: una fila por mensaje, legible con un SELECT.

Es la otra mitad de la memoria, y no sustituye al checkpointer ni al reves:

- El checkpointer (postgres_store.py) guarda el estado del grafo en BYTEA. Sirve
  para que el agente continue la conversacion, pero no hay SQL que devuelva un
  historial legible, y cuando el resumen se dispara el texto original SE PIERDE.
- Esta tabla es el unico registro auditable de que se dijo realmente, y la unica
  forma de sacar reportes (cuantos usuarios llegaron a generar itinerario, en
  cuantos turnos, que destinos piden).

Escritura fire-and-forget a proposito: un fallo registrando telemetria nunca
debe impedir que el usuario reciba su respuesta.

La tabla la crea migraciones_SQL/001_conversation_log.sql.
"""

import json

from .postgres_store import get_pool

TABLA = "conversation_log"


async def log_mensaje(
    thread_id: str,
    rol: str,
    contenido: str,
    tools_usadas: list[str] | None = None,
) -> None:
    """Registra un mensaje. Nunca lanza.

    Args:
        thread_id: mismo identificador que usa el checkpointer.
        rol: 'user' o 'assistant'.
        contenido: texto del mensaje.
        tools_usadas: nombres de las tools invocadas en ese turno. Es lo que
            permite auditar despues por que el agente lanzo el crew.
    """
    pool = get_pool()
    if pool is None:
        return

    try:
        async with pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {TABLA} (thread_id, rol, contenido, tools_usadas)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    thread_id,
                    rol,
                    contenido,
                    json.dumps(tools_usadas or []),
                ),
            )
    except Exception as e:
        print(f"⚠️  No se pudo registrar el mensaje en {TABLA}: {e}")


async def leer_conversacion(thread_id: str, limite: int = 100) -> list[dict]:
    """Devuelve la conversacion en orden cronologico. Para QA y soporte."""
    pool = get_pool()
    if pool is None:
        return []

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT rol, contenido, tools_usadas, creado_en
                FROM {TABLA}
                WHERE thread_id = %s
                ORDER BY creado_en ASC
                LIMIT %s
                """,
                (thread_id, limite),
            )
            filas = await cur.fetchall()
    except Exception as e:
        print(f"⚠️  No se pudo leer {TABLA}: {e}")
        return []

    return [
        {
            "rol": rol,
            "contenido": contenido,
            "tools_usadas": tools_usadas,
            "creado_en": creado_en.isoformat(),
        }
        for rol, contenido, tools_usadas, creado_en in filas
    ]
