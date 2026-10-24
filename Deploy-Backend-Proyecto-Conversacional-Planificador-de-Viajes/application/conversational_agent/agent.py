"""
Orquestador del agente conversacional: ensambla LLM + prompt + memoria + tools.

Este archivo solo orquesta. Si algo de aqui empieza a crecer, pertenece a otra
carpeta: el modelo a model_config/, el prompt a prompt/, la persistencia a
chat_history/ y las acciones a tools/.

Reparto de trabajo con CrewAI:
  - Este agente DIALOGA. Responde en segundos y no sabe nada de viajes que no
    le haya dicho el usuario.
  - El crew INVESTIGA Y PLANIFICA. Se lanza solo cuando el brief esta completo,
    a traves de generar_itinerario_tool.

Los recursos pesados (LLM, prompt, pool de Postgres) se inicializan UNA vez con
init_resources(). El agente se reconstruye por mensaje, que es barato, y es lo
que permite cerrar el `slot` de ese turno en la clausura de la tool.
"""

import os
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_openai import ChatOpenAI

from ..observabilidad import langfuse_callbacks, traza
from ..respaldo_llm import FallbackPorCredito, con_respaldo_por_credito, gemini_langchain
from .chat_history import abrir_memoria, get_checkpointer, log_mensaje
from .tools import get_generar_itinerario_tool

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

DIRECTORIO = Path(__file__).parent
RUTA_MODEL_CONFIG = DIRECTORIO / "model_config" / "model.yaml"
RUTA_SYSTEM_PROMPT = DIRECTORIO / "prompt" / "system_prompt.yaml"

NOMBRE_BOT = "Aria"

# El default de SummarizationMiddleware esta redactado para agentes de codigo
# (pide secciones ARTIFACTS y rutas de archivos). En un agente de viajes produce
# resumenes inutiles, asi que escribimos el nuestro.
# OJO: el middleware hace .format(messages=...), asi que {messages} es el UNICO
# placeholder permitido. Cualquier otra llave revienta con KeyError en produccion.
PROMPT_DE_RESUMEN = """Resume esta conversacion de planificacion de viaje.

Conserva, en espanol y en frases cortas:
- Los datos del viaje ya confirmados: destino, fechas, viajeros, presupuesto, intereses.
- Lo que el usuario descarto explicitamente y por que.
- Si ya se genero un itinerario, para que destino y fechas.
- Peticiones de ajuste pendientes.

Omite la charla y las cortesias. No inventes datos que no aparezcan.

Conversacion:
{messages}"""

# Singletons de modulo. Los deja init_resources().
_llm: ChatOpenAI | None = None
_system_prompt: str | None = None
_config_memoria: dict | None = None
# Gemini, solo si hay GEMINI_API_KEY. Entra cuando OpenAI se queda sin credito.
_respaldo = None


def _cargar_yaml(ruta: Path) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render_system_prompt() -> str:
    """Carga el prompt del YAML e inyecta los placeholders declarados.

    Con .replace() y no .format() para que cualquier llave {} literal del prompt
    no lance KeyError.
    """
    cfg = _cargar_yaml(RUTA_SYSTEM_PROMPT)
    return (
        cfg["system_prompt"]
        .replace("{bot_name}", NOMBRE_BOT)
        .replace("{fecha_actual}", date.today().isoformat())
    )


async def init_resources() -> None:
    """Inicializa lo caro. Se llama UNA vez, en el arranque de la app."""
    global _llm, _system_prompt, _config_memoria, _respaldo

    if not OPENAI_API_KEY:
        raise RuntimeError("Falta OPENAI_API_KEY en el .env.")

    cfg = _cargar_yaml(RUTA_MODEL_CONFIG)
    llm_cfg = cfg["llm"]
    _config_memoria = cfg["memory"]

    _llm = ChatOpenAI(
        model=llm_cfg["model"],
        temperature=llm_cfg["temperature"],
        timeout=llm_cfg["timeout"],
    )
    _system_prompt = _render_system_prompt()
    _respaldo = gemini_langchain()

    await abrir_memoria()
    respaldo = f", respaldo {_respaldo.model}" if _respaldo else ", sin respaldo"
    print(f"💬 Agente conversacional listo ({llm_cfg['model']}{respaldo}).")


def build_agent(slot: dict):
    """Construye el agente para UN turno, con el slot cerrado en la clausura.

    Barato a proposito: lo unico que cambia entre turnos es el slot donde la tool
    deposita el itinerario.
    """
    if _llm is None or _system_prompt is None or _config_memoria is None:
        raise RuntimeError(
            "El agente no esta inicializado. Llama a init_resources() en el "
            "arranque de la app (lifespan de FastAPI)."
        )

    resumen = SummarizationMiddleware(
        model=_config_memoria["summary_model"],
        trigger=("tokens", _config_memoria["token_limit"]),
        keep=("messages", _config_memoria["keep_messages"]),
        summary_prompt=PROMPT_DE_RESUMEN,
    )
    middleware = [resumen]

    if _respaldo is not None:
        # El resumen llama a su propio modelo (gpt-4.1-mini) por fuera del
        # middleware de fallback, asi que necesita su respaldo aparte.
        # `_summary_model` es privado, pero es justo lo que el middleware invoca
        # (en el original es `self.model.with_retry()`, y aqui lo respetamos).
        resumen._summary_model = con_respaldo_por_credito(
            resumen.model, _respaldo
        ).with_retry()
        middleware.append(FallbackPorCredito(_respaldo))

    return create_agent(
        model=_llm,
        tools=[get_generar_itinerario_tool(slot)],
        system_prompt=_system_prompt,
        middleware=middleware,
        checkpointer=get_checkpointer(),
    )


async def responder(thread_id: str, mensaje: str) -> dict:
    """Procesa un turno de conversacion.

    Args:
        thread_id: identidad del hilo. Mismo valor = misma memoria.
        mensaje: lo que escribio el usuario.

    Returns:
        dict con `reply` siempre, y `itinerario`/`descarga` solo en el turno en
        que el crew corrio.
    """
    # Registramos el mensaje entrante ANTES de invocar: si el turno falla, al
    # menos queda constancia de que pregunto el usuario.
    await log_mensaje(thread_id, "user", mensaje)

    slot: dict = {}
    agente = build_agent(slot)

    # Una traza de Langfuse por turno, agrupadas en una sesion por thread_id.
    # Si el agente lanza el crew, sus spans cuelgan de esta misma traza.
    with traza("chat-turn", mensaje, session_id=thread_id, tags=["chat"]) as observacion:
        resultado = await agente.ainvoke(
            {"messages": [{"role": "user", "content": mensaje}]},
            config={
                "configurable": {"thread_id": thread_id},
                "callbacks": langfuse_callbacks(),
            },
        )

        mensajes = resultado["messages"]
        # .text es una PROPIEDAD en langchain-core 1.x (llamarla como metodo esta
        # deprecado). Concatena los bloques de texto ignorando los de tool_call.
        reply = mensajes[-1].text
        observacion.update(output=reply)

    tools_usadas = [
        tc["name"]
        for m in mensajes
        for tc in (getattr(m, "tool_calls", None) or [])
    ]
    await log_mensaje(thread_id, "assistant", reply, tools_usadas)

    respuesta = {"reply": reply}
    if "itinerario" in slot:
        respuesta["itinerario"] = slot["itinerario"]
        respuesta["descarga"] = slot["descarga"]
    return respuesta
