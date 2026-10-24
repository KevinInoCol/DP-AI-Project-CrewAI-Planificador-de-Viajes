"""
API del Asistente de Viajes: capa conversacional (LangChain) + crew (CrewAI).

Dos endpoints con proposito distinto:

- POST /chat       El agente conversacional. Dialoga, reune el brief del viaje y,
                   cuando lo tiene completo, invoca el crew a traves de una tool.
                   El turno tarda segundos... salvo el turno que lanza el crew,
                   que tarda minutos. Es el camino que usa el frontend.

- POST /plan-trip  Dispara el crew directamente con un prompt, sin conversacion.
                   Se mantiene por compatibilidad y para probar el crew aislado.

Arrancar:
    uv run uvicorn backend_proyecto_planificador_de_viajes.main:app \
        --app-dir src --host 127.0.0.1 --port 8005 --reload
"""

import os
from contextlib import asynccontextmanager
from datetime import date

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse

from .conversational_agent import cerrar_memoria, init_resources, responder
from .conversational_agent.message_buffer import (
    BUFFER_ENABLED,
    BUFFER_WINDOW_SECONDS,
    abrir_redis,
    cerrar_redis,
    encolar_mensaje,
)
from .conversational_agent.tools import limpiar_salida_llm
from .crew import TravelCrew
from .observabilidad import cerrar_observabilidad, init_observabilidad, traza

load_dotenv()

ARCHIVO_ITINERARIO = "itinerary.md"

# Origenes que pueden llamar a la API desde el navegador (el frontend en Vercel,
# por ejemplo), separados por comas. Vacio = sin CORS: basta en local, donde el
# frontend llega por el proxy de Vite y el navegador no ve otro origen.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# Se pone a False si el agente conversacional no pudo arrancar (falta
# DATABASE_URL, por ejemplo). /plan-trip sigue funcionando igual.
_chat_disponible = False
_chat_error = ""
# Debounce con Redis. Se pone a False si esta apagado en el .env o si Redis no
# responde al arrancar: en ese caso /chat contesta mensaje por mensaje.
_buffer_activo = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa el agente conversacional al arrancar y lo cierra al apagar.

    Un fallo aqui NO tumba la app: /plan-trip no depende del agente, y es mejor
    servirlo que dejar el backend entero caido por una variable de entorno.
    """
    global _chat_disponible, _chat_error, _buffer_activo
    # Primero el tracing: el instrumentador de CrewAI tiene que estar puesto
    # antes de que corra el primer crew. Sin claves de Langfuse no hace nada.
    init_observabilidad()

    try:
        await init_resources()
        _chat_disponible = True
    except Exception as e:
        _chat_error = str(e)
        print(f"⚠️  Agente conversacional NO disponible: {e}")
        print("   /plan-trip sigue operativo. /chat devolvera 503.")

    if BUFFER_ENABLED:
        try:
            await abrir_redis()
            _buffer_activo = True
            print(f"🧺 Debounce activo: ventana de {BUFFER_WINDOW_SECONDS:g}s (Redis).")
        except Exception as e:
            print(f"⚠️  Debounce desactivado, Redis no responde: {e}")
            print("   /chat contestara mensaje por mensaje.")

    yield

    await cerrar_redis()
    await cerrar_memoria()
    cerrar_observabilidad()


app = FastAPI(
    title="API del Asistente de Viajes",
    description=(
        "Agente conversacional (LangChain) que dialoga con el usuario y delega "
        "en un equipo de agentes (CrewAI) cuando toca generar el itinerario."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )


class TripRequest(BaseModel):
    prompt: str


class ChatRequest(BaseModel):
    message: str
    # Identidad del hilo: mismo valor = misma memoria. Lo genera el frontend y
    # lo mantiene mientras dure la conversacion.
    thread_id: str


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """Un turno de conversacion.

    Devuelve `reply`. Devuelve ademas `itinerary` solo en el turno en que el
    agente decidio lanzar el crew: ese campo trae el markdown REAL del crew, no
    la version que redactaria el LLM, para que el frontend lo pinte en sus
    tarjetas por dia.

    Con el debounce activo, los mensajes seguidos de un mismo hilo se agrupan:
    solo la ULTIMA request del bloque trae la respuesta (a todo el bloque); las
    anteriores devuelven `merged: true` con `reply: null`.
    """
    if not _chat_disponible:
        return JSONResponse(
            status_code=503,
            content={
                "message": "El agente conversacional no esta disponible.",
                "details": _chat_error,
            },
        )

    try:
        if _buffer_activo:
            resultado = await encolar_mensaje(
                request.thread_id, request.message, responder
            )
            if resultado is None:
                return {"reply": None, "itinerary": None, "merged": True}
        else:
            resultado = await responder(request.thread_id, request.message)
    except Exception as e:
        print(f"❌ Error en el turno de chat: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Ocurrio un error procesando tu mensaje.",
                "details": str(e),
            },
        )

    respuesta = {"reply": resultado["reply"], "itinerary": None, "merged": False}

    if "itinerario" in resultado:
        respuesta["itinerary"] = {
            "chat_response": resultado["itinerario"],
            "download_content": resultado["descarga"],
            "download_filename": ARCHIVO_ITINERARIO,
        }

    return respuesta


@app.post("/plan-trip")
async def plan_trip_endpoint(request: TripRequest):
    """Ejecuta el crew directamente, sin pasar por la conversacion."""
    try:
        inputs = {
            "trip_request": request.prompt,
            # Fecha de hoy: el agente de agenda la usa para fechas relativas.
            "fecha_actual": date.today().isoformat(),
        }

        print(f"🚀 Ejecutando el crew para la peticion: {request.prompt}")
        travel_crew = TravelCrew()
        # crewai 1.14+: dentro de un endpoint async hay que usar kickoff_async,
        # no kickoff, o lanza "invoked synchronously from within a running event loop".
        with traza("plan-trip", request.prompt, tags=["plan-trip"]) as observacion:
            result = await travel_crew.crew().kickoff_async(inputs=inputs)
            observacion.update(output=result.raw)
        print("✅ Crew finalizado. Procesando resultado.")

        final_chat_response = limpiar_salida_llm(result.raw)

        try:
            with open(ARCHIVO_ITINERARIO, encoding="utf-8") as f:
                download_content = f.read()
        except FileNotFoundError:
            print(f"⚠️  No se encontro '{ARCHIVO_ITINERARIO}'. Uso la respuesta del chat.")
            download_content = final_chat_response

        return {
            "chat_response": final_chat_response,
            "download_content": download_content,
            "download_filename": ARCHIVO_ITINERARIO,
        }

    except Exception as e:
        print(f"❌ Error durante la ejecucion del crew: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Ocurrio un error interno al procesar tu solicitud.",
                "details": str(e),
            },
        )


@app.get("/")
def read_root():
    return {
        "status": "El servidor del Asistente de Viajes IA esta funcionando.",
        "chat_disponible": _chat_disponible,
        "debounce_activo": _buffer_activo,
    }
