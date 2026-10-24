"""
Cliente asincrono de Redis (singleton) para el buffer de mensajes.

Se conecta con REDIS_URL si esta, o con las variables granulares. Lo abre
`abrir_redis()` en el arranque de la app (lifespan) y lo cierra `cerrar_redis()`.
"""

import redis.asyncio as redis

from .config import (
    REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    REDIS_TLS,
    REDIS_URL,
    REDIS_USERNAME,
)

_cliente: redis.Redis | None = None


def _crear_cliente() -> redis.Redis:
    # decode_responses: los mensajes vuelven como str y no como bytes.
    if REDIS_URL:
        return redis.from_url(REDIS_URL, decode_responses=True)
    if not REDIS_HOST:
        raise RuntimeError("Falta REDIS_URL (o REDIS_HOST) en el .env.")
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        username=REDIS_USERNAME,
        password=REDIS_PASSWORD,
        ssl=REDIS_TLS,
        decode_responses=True,
    )


async def abrir_redis() -> None:
    """Crea el cliente y comprueba la conexion con un PING. Lanza si falla."""
    global _cliente
    cliente = _crear_cliente()
    try:
        await cliente.ping()
    except Exception:
        await cliente.aclose()
        raise
    _cliente = cliente


def get_redis() -> redis.Redis:
    if _cliente is None:
        raise RuntimeError("Redis no esta abierto. Llama a abrir_redis() en el arranque.")
    return _cliente


async def cerrar_redis() -> None:
    global _cliente
    if _cliente is not None:
        await _cliente.aclose()
        _cliente = None
