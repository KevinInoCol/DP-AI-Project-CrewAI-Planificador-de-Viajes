"""
Respaldo con Gemini cuando OpenAI se queda sin credito.

Regla unica para todo el sistema: se intenta SIEMPRE OpenAI primero, y solo si
OpenAI responde "sin credito" (429 `insufficient_quota`) se repite la MISMA
llamada con Gemini. Cualquier otro error (timeout, prompt mal formado, rate
limit normal...) sube tal cual: no queremos que Gemini tape bugs.

Se aplica en tres sitios:

- Agente conversacional: `FallbackPorCredito`, un middleware de LangChain.
  (El `ModelFallbackMiddleware` oficial salta con CUALQUIER error, por eso no.)
- Resumen del historial: `con_respaldo_por_credito()`, un Runnable que el
  SummarizationMiddleware usa en lugar de su modelo.
- Crew de CrewAI: `LLMConRespaldo`, un BaseLLM que envuelve al LLM de OpenAI.

Sin GEMINI_API_KEY todo funciona como antes, sin respaldo.
"""

import os
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from crewai import LLM
from crewai.llms.base_llm import BaseLLM, call_stop_override
from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import PrivateAttr

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("MODEL")

MODELO_GEMINI = "gemini-3.5-flash"


def es_sin_credito(error: BaseException) -> bool:
    """True si el error (o alguno de su cadena) es OpenAI sin credito.

    CrewAI re-lanza los errores de OpenAI envueltos, asi que recorremos
    __cause__ / __context__ hasta dar con el original.
    """
    actual: BaseException | None = error
    vistos = set()
    while actual is not None and id(actual) not in vistos:
        vistos.add(id(actual))
        if isinstance(actual, openai.RateLimitError) and (
            actual.code == "insufficient_quota" or "insufficient_quota" in str(actual)
        ):
            return True
        actual = actual.__cause__ or actual.__context__
    return False


def _avisar(donde: str) -> None:
    print(f"💸 OpenAI sin credito: {donde} pasa a Gemini ({MODELO_GEMINI}).")


# ---------------------------------------------------------------------------
# LangChain (agente conversacional)
# ---------------------------------------------------------------------------


def gemini_langchain() -> ChatGoogleGenerativeAI | None:
    """Modelo de respaldo para LangChain, o None si no hay GEMINI_API_KEY.

    Sin temperature a proposito: Gemini 3 esta afinado para su valor por defecto
    y Google desaconseja bajarlo.
    """
    if not GEMINI_API_KEY:
        return None
    return ChatGoogleGenerativeAI(model=MODELO_GEMINI, google_api_key=GEMINI_API_KEY)


class FallbackPorCredito(AgentMiddleware):
    """Repite la llamada al modelo con Gemini si OpenAI no tiene credito."""

    def __init__(self, respaldo: BaseChatModel) -> None:
        super().__init__()
        self.respaldo = respaldo

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        try:
            return handler(request)
        except Exception as e:
            if not es_sin_credito(e):
                raise
        _avisar("el agente conversacional")
        return handler(request.override(model=self.respaldo))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            return await handler(request)
        except Exception as e:
            if not es_sin_credito(e):
                raise
        _avisar("el agente conversacional")
        return await handler(request.override(model=self.respaldo))


def con_respaldo_por_credito(principal: Runnable, respaldo: Runnable) -> Runnable:
    """Runnable que llama a `principal` y, si no hay credito, a `respaldo`."""

    def invocar(entrada: Any, config) -> Any:
        try:
            return principal.invoke(entrada, config)
        except Exception as e:
            if not es_sin_credito(e):
                raise
        _avisar("el resumen del historial")
        return respaldo.invoke(entrada, config)

    async def ainvocar(entrada: Any, config) -> Any:
        try:
            return await principal.ainvoke(entrada, config)
        except Exception as e:
            if not es_sin_credito(e):
                raise
        _avisar("el resumen del historial")
        return await respaldo.ainvoke(entrada, config)

    return RunnableLambda(invocar, afunc=ainvocar, name="resumen_con_respaldo")


# ---------------------------------------------------------------------------
# CrewAI (crew)
# ---------------------------------------------------------------------------


class LLMConRespaldo(BaseLLM):
    """LLM de CrewAI que delega en OpenAI y, sin credito, en Gemini.

    Se hace pasar por el LLM principal (mismo model/provider) para que CrewAI
    decida function calling, ventana de contexto, etc. como si fuera OpenAI.
    """

    _principal: BaseLLM = PrivateAttr()
    _respaldo: BaseLLM = PrivateAttr()

    def __init__(self, principal: BaseLLM, respaldo: BaseLLM) -> None:
        super().__init__(
            model=principal.model,
            provider=principal.provider,
            temperature=principal.temperature,
            stop=principal.stop,
        )
        self._principal = principal
        self._respaldo = respaldo

    def _parar_en(self, llm: BaseLLM):
        # El executor de CrewAI fija sus stop words sobre ESTE objeto (via
        # contextvar). Las reenviamos al LLM que de verdad hace la llamada.
        return call_stop_override(llm, self.stop_sequences or None)

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> Any:
        args = (messages, tools, callbacks, available_functions, from_task, from_agent, response_model)
        try:
            with self._parar_en(self._principal):
                return self._principal.call(*args)
        except Exception as e:
            if not es_sin_credito(e):
                raise
        _avisar("el crew")
        with self._parar_en(self._respaldo):
            return self._respaldo.call(*args)

    async def acall(self, messages, tools=None, callbacks=None, available_functions=None,
                    from_task=None, from_agent=None, response_model=None) -> Any:
        args = (messages, tools, callbacks, available_functions, from_task, from_agent, response_model)
        try:
            with self._parar_en(self._principal):
                return await self._principal.acall(*args)
        except Exception as e:
            if not es_sin_credito(e):
                raise
        _avisar("el crew")
        with self._parar_en(self._respaldo):
            return await self._respaldo.acall(*args)

    def supports_function_calling(self) -> bool:
        return self._principal.supports_function_calling()

    def supports_multimodal(self) -> bool:
        return self._principal.supports_multimodal()

    def supports_stop_words(self) -> bool:
        return self._principal.supports_stop_words()

    def get_context_window_size(self) -> int:
        # La de OpenAI: Gemini tiene ~1M, asi que lo que quepa en OpenAI cabe.
        return self._principal.get_context_window_size()

    def get_token_usage_summary(self):
        resumen = self._principal.get_token_usage_summary()
        resumen.add_usage_metrics(self._respaldo.get_token_usage_summary())
        return resumen


def llm_del_crew() -> BaseLLM | None:
    """LLM para los agentes del crew. None = que CrewAI use su default (MODEL).

    Solo envolvemos si hay GEMINI_API_KEY y MODEL; si no, el crew queda como siempre.
    """
    if not (GEMINI_API_KEY and MODEL):
        return None
    principal = LLM(model=MODEL)
    respaldo = LLM(model=f"gemini/{MODELO_GEMINI}", api_key=GEMINI_API_KEY)
    return LLMConRespaldo(principal, respaldo)
