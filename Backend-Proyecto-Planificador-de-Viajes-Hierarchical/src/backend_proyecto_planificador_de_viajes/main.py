# src/travel_crew_backend/main.py

from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import JSONResponse
from datetime import date
import os

# Usamos una importación relativa porque 'crew.py' está en el mismo directorio.
from .crew import TravelCrew
from .observabilidad import cerrar_observabilidad, init_observabilidad, traza


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El instrumentador de CrewAI tiene que estar puesto antes de que corra el
    # primer crew. Sin claves de Langfuse no hace nada.
    init_observabilidad()
    yield
    cerrar_observabilidad()


# Inicializar la aplicación FastAPI
app = FastAPI(
    title="API del Asistente de Viajes",
    description="Una API para planificar itinerarios de viaje personalizados usando un equipo de agentes de IA (CrewAI).",
    version="1.0.0",
    lifespan=lifespan,
)

class TripRequest(BaseModel):
    prompt: str

# --- Función de Limpieza ---
# Esta función eliminará los artefactos comunes del LLM
def clean_llm_output(text: str) -> str:
    cleaned_text = text.replace("∗", "").replace("ˊ", "")
    # Puedes añadir más reemplazos si encuentras otros artefactos
    return cleaned_text

@app.post("/plan-trip") #Primer Endpoint.
async def plan_trip_endpoint(request: TripRequest):
    """
    Recibe una petición de viaje y devuelve un itinerario generado por el Crew de IA,
    listo para mostrarse en el chat y para ser descargado.
    """
    try:
        inputs = {
            'trip_request': request.prompt,
            # Fecha de hoy: el agente de agenda la usa para resolver fechas relativas
            'fecha_actual': date.today().isoformat(),
        }
        
        print(f"🚀 Ejecutando el crew para la petición: {request.prompt}")
        travel_crew = TravelCrew()
        # crewai 1.14+: dentro de un endpoint async hay que usar kickoff_async,
        # no kickoff (síncrono), o lanza "invoked synchronously from within a running event loop".
        # Una traza de Langfuse por peticion; el manager y los especialistas cuelgan de ella.
        with traza("plan-trip-hierarchical", request.prompt, tags=["plan-trip", "hierarchical"]) as observacion:
            result = await travel_crew.crew().kickoff_async(inputs=inputs)
            observacion.update(output=result.raw)
        print(f"✅ Crew finalizado. Procesando resultado.")

        # 1. Obtener el resultado final y limpiarlo para el chat
        final_chat_response = clean_llm_output(result.raw)

        # 2. Leer el contenido del archivo .md para la descarga
        download_content = ""
        filename = "itinerary.md"
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                download_content = f.read()
        except FileNotFoundError:
            print(f"⚠️  Advertencia: No se encontró el archivo '{filename}'. La descarga no estará disponible.")
            download_content = final_chat_response # Como fallback, usamos la respuesta del chat

        # 3. Construir la respuesta JSON estructurada
        structured_response = {
            "chat_response": final_chat_response,
            "download_content": download_content,
            "download_filename": filename
        }
        
        return structured_response

    except Exception as e:
        print(f"❌ Error durante la ejecución del crew: {e}")
        return JSONResponse(
            status_code=500,
            content={"message": "Ocurrió un error interno al procesar tu solicitud.", "details": str(e)}
        )

@app.get("/") #Segundo Endpoint.
def read_root():
    return {"status": "El servidor del Asistente de Viajes IA está funcionando."}