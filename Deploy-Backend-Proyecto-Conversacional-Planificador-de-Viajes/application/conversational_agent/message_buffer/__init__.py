"""Buffer de mensajes con Redis (debounce): varios mensajes seguidos, una respuesta."""

from .buffer import encolar_mensaje
from .config import BUFFER_ENABLED, BUFFER_WINDOW_SECONDS
from .redis_client import abrir_redis, cerrar_redis

__all__ = [
    "BUFFER_ENABLED",
    "BUFFER_WINDOW_SECONDS",
    "abrir_redis",
    "cerrar_redis",
    "encolar_mensaje",
]
