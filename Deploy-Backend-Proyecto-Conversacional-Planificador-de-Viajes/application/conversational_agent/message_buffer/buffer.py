"""
Debounce por secuencia: junta los mensajes seguidos de un hilo en UNA inferencia.

Cada POST /chat llama a `encolar_mensaje()`, que:

1. Mete el mensaje en una lista de Redis (RPUSH) e incrementa el contador de
   secuencia del hilo (INCR). El mensaje se queda con su numero: `mi_seq`.
2. Espera la ventana de silencio (BUFFER_WINDOW_SECONDS).
3. Si al despertar su secuencia ya no es la ultima, llego un mensaje mas nuevo:
   devuelve None (el endpoint responde `merged: true`) y la request mas nueva se
   encarga. Asi gana siempre el ultimo y nunca hay respuestas duplicadas.
4. Si es la ultima, toma el candado del hilo, consume TODOS los mensajes
   acumulados, los une con el separador y llama al agente una sola vez.

Diferencias con la skill de debounce (pensada para webhooks), ambas a proposito:

- No hay tarea en segundo plano: el frontend espera el `reply` en la misma
  respuesta HTTP, asi que la espera ocurre dentro de la propia request.
- Los mensajes se CONSUMEN al empezar (LRANGE + DEL atomicos), no al terminar.
  El turno que lanza el crew tarda minutos, mucho mas que la ventana; si
  limpiaramos al final, los mensajes que llegan mientras tanto se responderian
  dos veces. La skill contempla esta variante para agentes lentos.

El candado por hilo evita que dos bloques del mismo hilo corran a la vez sobre
el checkpointer de LangGraph (se pisarian la memoria). El segundo bloque espera
a que termine el primero.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .config import (
    BUFFER_LOCK_TTL_SECONDS,
    BUFFER_SEPARATOR,
    BUFFER_TTL_SECONDS,
    BUFFER_WINDOW_SECONDS,
    KEY_PREFIX,
)
from .redis_client import get_redis

T = TypeVar("T")

# Libera el candado solo si sigue siendo nuestro (si caduco y otro lo tomo, no
# se lo quitamos).
_LIBERAR_CANDADO = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_ESPERA_CANDADO_SEGUNDOS = 0.5


def _claves(conversation_id: str) -> tuple[str, str, str]:
    return (
        f"{KEY_PREFIX}:msgs:{conversation_id}",
        f"{KEY_PREFIX}:seq:{conversation_id}",
        f"{KEY_PREFIX}:lock:{conversation_id}",
    )


async def _tomar_candado(lock_key: str) -> str:
    """Espera hasta tener el candado del hilo. Devuelve el token para liberarlo."""
    r = get_redis()
    token = uuid.uuid4().hex
    intentos = int(BUFFER_LOCK_TTL_SECONDS / _ESPERA_CANDADO_SEGUNDOS)
    for _ in range(intentos):
        if await r.set(lock_key, token, nx=True, ex=BUFFER_LOCK_TTL_SECONDS):
            return token
        await asyncio.sleep(_ESPERA_CANDADO_SEGUNDOS)
    raise TimeoutError("El hilo sigue ocupado con un turno anterior.")


async def encolar_mensaje(
    conversation_id: str,
    mensaje: str,
    procesar: Callable[[str, str], Awaitable[T]],
) -> T | None:
    """Encola el mensaje y, si es el ultimo tras la ventana, procesa el bloque.

    Args:
        conversation_id: identidad del hilo (el thread_id del frontend).
        mensaje: lo que escribio el usuario en esta request.
        procesar: callback async `procesar(conversation_id, mensaje_concatenado)`
            que invoca al agente y devuelve su resultado.

    Returns:
        El resultado de `procesar`, o None si este mensaje quedo agrupado en el
        bloque de una request posterior (esa es la que trae la respuesta).
    """
    r = get_redis()
    msgs_key, seq_key, lock_key = _claves(conversation_id)

    async with r.pipeline(transaction=True) as p:
        p.rpush(msgs_key, mensaje)
        p.incr(seq_key)
        p.expire(msgs_key, BUFFER_TTL_SECONDS)
        p.expire(seq_key, BUFFER_TTL_SECONDS)
        _, mi_seq, _, _ = await p.execute()

    await asyncio.sleep(BUFFER_WINDOW_SECONDS)

    seq_actual = await r.get(seq_key)
    if seq_actual is None or int(seq_actual) != mi_seq:
        return None  # llego un mensaje mas nuevo; esa request se encarga

    token = await _tomar_candado(lock_key)
    try:
        # Consumir atomicamente: lo que se lee aqui ya no lo vera nadie mas.
        async with r.pipeline(transaction=True) as p:
            p.lrange(msgs_key, 0, -1)
            p.delete(msgs_key)
            mensajes, _ = await p.execute()

        if not mensajes:
            # Un bloque anterior que esperaba el candado se los llevo todos.
            return None

        if len(mensajes) > 1:
            print(f"🧺 Debounce: {len(mensajes)} mensajes agrupados en el hilo {conversation_id}.")
        return await procesar(conversation_id, BUFFER_SEPARATOR.join(mensajes))
    finally:
        await r.eval(_LIBERAR_CANDADO, 1, lock_key, token)
        # Limpieza: si no llego nada nuevo, la secuencia ya no hace falta.
        seq_final = await r.get(seq_key)
        if seq_final is not None and int(seq_final) == mi_seq:
            await r.delete(seq_key)
