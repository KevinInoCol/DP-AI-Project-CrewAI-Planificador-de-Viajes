"""
Respaldo con Gemini (via Vertex AI) cuando OpenAI se queda sin credito.

Regla unica para todo el sistema: se intenta SIEMPRE OpenAI primero, y solo si
OpenAI responde "sin credito" (429 `insufficient_quota`) se repite la MISMA
llamada con Gemini. Cualquier otro error (timeout, prompt mal formado, rate
limit normal...) sube tal cual: no queremos que Gemini tape bugs.

Se aplica con `LLMConRespaldo`, un BaseLLM de CrewAI que envuelve al LLM de
OpenAI y que comparten el manager y los 6 especialistas del crew.

Gemini se consume por Vertex AI (Agent Platform) en el proyecto de GCP de
GOOGLE_CLOUD_PROJECT, autenticando con Application Default Credentials: las de
GOOGLE_APPLICATION_CREDENTIALS si esta definida, o las de `gcloud auth
application-default login`. Sin GOOGLE_CLOUD_PROJECT todo funciona como antes,
sin respaldo.
"""

import os
from typing import Any

import openai
from crewai import LLM
from crewai.llms.base_llm import BaseLLM, call_stop_override
from dotenv import load_dotenv
from pydantic import PrivateAttr

load_dotenv()

GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
# Los modelos Gemini 3 en Vertex se sirven desde el endpoint global.
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
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
    """LLM para el manager y los agentes del crew. None = que CrewAI use su default (MODEL).

    Solo envolvemos si hay GOOGLE_CLOUD_PROJECT y MODEL; si no, el crew queda
    como siempre. OJO: no debe haber GEMINI_API_KEY/GOOGLE_API_KEY en el entorno,
    porque CrewAI las prefiere a Vertex y saldria por la API de AI Studio.
    """
    if not (GOOGLE_CLOUD_PROJECT and MODEL):
        return None
    principal = LLM(model=MODEL)
    respaldo = LLM(
        model=f"gemini/{MODELO_GEMINI}",
        use_vertexai=True,
        project=GOOGLE_CLOUD_PROJECT,
        location=GOOGLE_CLOUD_LOCATION,
    )
    return LLMConRespaldo(principal, respaldo)
