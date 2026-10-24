"""
Observabilidad del crew con Langfuse.

Instrumentador de OpenInference para CrewAI, que exporta spans OTel a Langfuse.
Con `use_event_listener=True` se cuelga del bus de eventos de CrewAI y crea spans
de crew, manager, agentes, delegaciones (tools), tareas Y de cada llamada al LLM
(con tokens), sin importar el proveedor nativo que use CrewAI (OpenAI o Gemini).

Sin LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY la app arranca igual: el tracing
queda apagado y todo lo demas funciona.
"""

import os
from contextlib import contextmanager

from dotenv import load_dotenv
from langfuse import get_client, propagate_attributes
from openinference.instrumentation.crewai import CrewAIInstrumentor

load_dotenv()

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

_activa = False


def init_observabilidad() -> bool:
    """Inicializa Langfuse e instrumenta CrewAI. Idempotente.

    Tiene que correr ANTES de ejecutar el primer crew, por eso main.py la llama
    al principio del lifespan.
    """
    global _activa
    if _activa:
        return True

    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        print("ℹ️  Langfuse desactivado: faltan LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY.")
        return False

    # get_client() lee las LANGFUSE_* del entorno y registra el TracerProvider
    # global de OTel, que es el que usara el instrumentador de CrewAI.
    langfuse = get_client()
    try:
        autenticado = langfuse.auth_check()
    except Exception as e:
        print(f"⚠️  No se pudo contactar con Langfuse ({e}). Tracing apagado.")
        return False
    if not autenticado:
        print(f"⚠️  Langfuse no acepto las claves contra {LANGFUSE_BASE_URL}. Tracing apagado.")
        return False

    CrewAIInstrumentor().instrument(skip_dep_check=True, use_event_listener=True)

    _activa = True
    print(f"🔭 Langfuse activo ({LANGFUSE_BASE_URL}).")
    return True


class _ObservacionNula:
    """Sustituto de la observacion cuando Langfuse esta apagado."""

    def update(self, **kwargs) -> None:
        pass


@contextmanager
def traza(nombre: str, entrada, session_id: str | None = None, tags: list[str] | None = None):
    """Abre la observacion raiz de una request y propaga session_id y tags.

    Todo lo que corra dentro (el crew, via OpenInference) cuelga de esta traza. Devuelve la observacion para que el
    llamador registre la salida con `.update(output=...)`.
    """
    if not _activa:
        yield _ObservacionNula()
        return

    langfuse = get_client()
    with langfuse.start_as_current_observation(
        as_type="span", name=nombre, input=entrada
    ) as observacion:
        with propagate_attributes(trace_name=nombre, session_id=session_id, tags=tags):
            yield observacion


def cerrar_observabilidad() -> None:
    """Envia lo que quede en cola. Se llama al apagar la app."""
    if _activa:
        get_client().shutdown()
