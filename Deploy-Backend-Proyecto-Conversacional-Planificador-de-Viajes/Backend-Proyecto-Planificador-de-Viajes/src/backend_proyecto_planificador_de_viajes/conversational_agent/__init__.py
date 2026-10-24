"""Agente conversacional (LangChain) que antecede al crew de CrewAI."""

from .agent import build_agent, init_resources, responder
from .chat_history import cerrar_memoria, leer_conversacion

__all__ = [
    "build_agent",
    "cerrar_memoria",
    "init_resources",
    "leer_conversacion",
    "responder",
]
