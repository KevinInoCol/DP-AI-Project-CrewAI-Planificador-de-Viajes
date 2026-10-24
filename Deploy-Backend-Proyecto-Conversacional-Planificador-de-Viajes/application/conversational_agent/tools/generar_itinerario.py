"""
Tool que delega en el crew de CrewAI para generar el itinerario.

Es el puente entre las dos mitades del sistema: el agente conversacional
(LangChain) dialoga y reune el brief; cuando lo tiene completo invoca esta tool,
que lanza a los 6 agentes de CrewAI (cultura, gastronomia, logistica,
planificacion, agenda y redaccion).

Dos cosas que parecen detalles y no lo son:

1. La tool NO le devuelve el itinerario al LLM, solo un acuse de recibo corto.
   El markdown completo se deja en un "slot" que el endpoint lee aparte y manda
   al frontend tal cual. Asi el frontend sigue pintando sus tarjetas por dia con
   el markdown REAL del crew, y no con la reescritura del LLM. De paso ahorra
   miles de tokens por turno: el itinerario no entra al historial.

2. Es `async def` a proposito. El endpoint corre con .ainvoke() y el crew con
   kickoff_async(); una tool sincrona aqui bloquearia el event loop de FastAPI
   durante los minutos que tarda el crew, congelando TODAS las demas requests.
"""

from datetime import date
from pathlib import Path

from langchain.tools import tool

from ...crew import TravelCrew

# El crew escribe aqui el itinerario final (output_file de task_redaccion_final).
ARCHIVO_ITINERARIO = "itinerary.md"


def limpiar_salida_llm(texto: str) -> str:
    """Quita artefactos tipograficos que a veces deja el LLM en el markdown."""
    return texto.replace("∗", "").replace("ˊ", "")


def construir_brief(
    destino: str,
    fechas: str,
    viajeros: str,
    presupuesto: str,
    intereses: str,
) -> str:
    """Arma la peticion en lenguaje natural que espera el crew (`trip_request`)."""
    return (
        f"Viaje a {destino}. "
        f"Fechas: {fechas}. "
        f"Viajeros: {viajeros}. "
        f"Presupuesto: {presupuesto}. "
        f"Intereses: {intereses}."
    )


async def generar_itinerario(brief: str) -> dict:
    """Ejecuta el crew y devuelve el itinerario. Imperativa: testeable sin LLM.

    Returns:
        dict con `ok`, y segun el caso `itinerario` / `descarga` o `error`.
    """
    inputs = {
        "trip_request": brief,
        # El agente de agenda la usa para resolver fechas relativas.
        "fecha_actual": date.today().isoformat(),
    }

    print(f"🚀 Lanzando el crew para: {brief}")
    try:
        resultado = await TravelCrew().crew().kickoff_async(inputs=inputs)
    except Exception as e:
        print(f"❌ El crew fallo: {e}")
        return {"ok": False, "error": str(e)}

    itinerario = limpiar_salida_llm(resultado.raw)

    # El archivo trae el documento completo; result.raw es la ultima tarea.
    # Si por lo que sea no se escribio, caemos al raw en vez de quedarnos sin nada.
    archivo = Path(ARCHIVO_ITINERARIO)
    try:
        descarga = archivo.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"⚠️  No se encontro '{ARCHIVO_ITINERARIO}'; uso la salida del crew.")
        descarga = itinerario

    print("✅ Crew finalizado.")
    return {"ok": True, "itinerario": itinerario, "descarga": descarga}


def get_generar_itinerario_tool(slot: dict):
    """Factory: devuelve la tool con un `slot` de este turno cerrado en clausura.

    El slot es un dict vacio que crea el endpoint antes de invocar al agente.
    La tool deposita ahi el itinerario completo, y el endpoint lo recoge despues
    para mandarlo al frontend. El LLM nunca ve el slot ni el markdown entero.
    """

    @tool
    async def generar_itinerario_tool(
        destino: str,
        fechas: str,
        viajeros: str,
        presupuesto: str,
        intereses: str,
    ) -> str:
        """Genera el itinerario de viaje completo con el equipo de especialistas.

        Investiga el destino en internet, arma el plan dia por dia y crea un
        evento por dia en el Google Calendar del usuario. Tarda varios minutos.

        Invocala SOLO cuando tengas los cinco datos y el usuario haya confirmado.
        Si falta alguno, sigue preguntando en vez de invocarla.

        Args:
            destino: ciudad, region o pais. Ej: 'Cusco, Peru'.
            fechas: fechas exactas o mes + duracion. Ej: '11 al 15 de julio 2026'.
            viajeros: cuantos y de que tipo. Ej: 'una pareja', 'familia con 2 ninos'.
            presupuesto: monto con moneda o rango. Ej: 'USD 800', 'moderado'.
            intereses: que busca el viajero. Ej: 'historia inca y gastronomia'.
        """
        brief = construir_brief(destino, fechas, viajeros, presupuesto, intereses)
        resultado = await generar_itinerario(brief)

        if not resultado["ok"]:
            return (
                "El equipo no pudo completar el itinerario por un error tecnico. "
                "Disculpate en una frase y ofrece reintentar. "
                f"Detalle interno (no se lo muestres al usuario): {resultado['error']}"
            )

        # Aqui esta el truco: el markdown completo va al slot, no al LLM.
        slot["itinerario"] = resultado["itinerario"]
        slot["descarga"] = resultado["descarga"]
        slot["brief"] = brief

        return (
            f"Itinerario de {destino} generado y ya visible en pantalla para el "
            "usuario, con un evento por dia creado en su Google Calendar. "
            "NO lo repitas ni lo resumas: el usuario lo esta viendo completo. "
            "Confirma en una o dos frases y ofrece ajustarlo."
        )

    return generar_itinerario_tool
