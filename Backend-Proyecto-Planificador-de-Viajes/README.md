# Backend — Planificador de Viajes IA (CrewAI + FastAPI)

Backend del planificador de viajes conversacional. Expone una **API FastAPI** con
dos capas que se reparten el trabajo:

- Un **agente conversacional (LangChain 1.x)** que dialoga con el usuario y reúne
  los datos del viaje: destino, fechas, viajeros, presupuesto e intereses.
- Un **crew de 6 agentes (CrewAI)** que solo entra en escena cuando el brief está
  completo: investiga el destino, arma el itinerario día por día y lo agenda en
  Google Calendar.

El agente conversacional invoca al crew como una **tool**. El usuario ya no tiene
que escribir la petición perfecta de una sola vez: la construye conversando.

## Requisitos

- Python 3.10–3.12
- [uv](https://docs.astral.sh/uv/) para gestionar dependencias
- Claves en el archivo `.env` (ver `.env.example`):
  - `OPENAI_API_KEY` — modelo LLM.
  - `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` — *opcional*,
    respaldo con Gemini 3.5 Flash vía **Vertex AI** si OpenAI se queda sin crédito.
  - `TAVILY_API_KEY` — búsqueda en internet de los agentes ([gratis en tavily.com](https://tavily.com)).
  - `CREWAI_PLATFORM_INTEGRATION_TOKEN` — integración de Google Calendar (Settings → Integrations en app.crewai.com).
  - `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` — Postgres donde el
    agente conversacional guarda el historial. La contraseña se URL-encodea
    sola, así que va tal cual aunque tenga símbolos.
  - `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` — *opcional*,
    trazas en [Langfuse](https://langfuse.com). Sin ellas, la app funciona igual sin tracing.
- Un **Postgres** accesible (Supabase, Neon, Docker local…). Sin él, `/chat`
  responde `503` y solo funciona `/plan-trip`.

## Puesta en marcha

```bash
cp .env.example .env      # rellena tus claves
uv sync                   # crea el .venv e instala dependencias
uv run uvicorn backend_proyecto_planificador_de_viajes.main:app \
  --app-dir src --host 127.0.0.1 --port 8005 --reload
```

El servidor queda en `http://localhost:8005`.

### Base de datos (solo la primera vez)

El agente conversacional necesita dos cosas en Postgres: las tablas del
checkpointer de LangGraph (las crea él solo al arrancar) y la tabla del log
plano (esa la creas tú).

1. **Antes del primer arranque:** aplica `001_conversation_log.sql`.
2. **Arranca el backend una vez** — `AsyncPostgresSaver.setup()` crea las tablas
   del checkpointer.
3. **Después:** aplica `002_rls_checkpointer.sql`.

Los dos archivos están en
`src/backend_proyecto_planificador_de_viajes/conversational_agent/migraciones_SQL/`
y se pegan tal cual en el SQL Editor de Supabase (o con `psql`). Ver
`conversational_agent/migraciones_SQL/README.md` para el porqué del orden.

## API

| Método | Ruta         | Descripción |
|--------|--------------|-------------|
| `GET`  | `/`          | Healthcheck. Incluye `chat_disponible`. |
| `POST` | `/chat`      | Un turno de conversación. **Es el que usa el frontend.** |
| `POST` | `/plan-trip` | Dispara el crew directamente, sin conversación. |

### `POST /chat`

```json
{ "message": "quiero ir a Cusco en julio", "thread_id": "uuid-del-hilo" }
```

`thread_id` es la identidad de la conversación: mientras no cambie, el agente
recuerda todo el hilo (incluso entre reinicios del backend).

```json
{
  "reply": "…lo que responde el agente…",
  "itinerary": null
}
```

`itinerary` llega en `null` en los turnos de charla. Solo en el turno en que el
agente decide lanzar el crew trae el itinerario, con la misma forma que devuelve
`/plan-trip`:

```json
{
  "reply": "Listo, ya tienes tu itinerario. ¿Ajustamos algo?",
  "itinerary": {
    "chat_response": "…itinerario en markdown…",
    "download_content": "…documento completo en markdown…",
    "download_filename": "itinerary.md"
  }
}
```

> Ese turno tarda **varios minutos**: la tool corre el crew dentro del bucle del
> agente. Los turnos de charla responden en segundos.

El markdown de `itinerary` es el que produjo el crew, **no** una reescritura del
LLM conversacional. Es deliberado: así el frontend lo pinta en sus tarjetas por
día, y el itinerario completo nunca entra en el historial del chat (ahorra miles
de tokens por turno).

### `POST /plan-trip`

```json
{
  "chat_response": "…itinerario en markdown…",
  "download_content": "…documento completo en markdown…",
  "download_filename": "itinerary.md"
}
```

## El crew (6 agentes, proceso secuencial)

Definido en `src/backend_proyecto_planificador_de_viajes/crew.py`, con los roles y
tareas en `config/agents.yaml` y `config/tasks.yaml`.

| # | Agente | Herramienta | Función |
|---|--------|-------------|---------|
| 1 | Experto cultural | Búsqueda (Tavily) | Atracciones, museos, sitios históricos. |
| 2 | Gourmet local | Búsqueda (Tavily) | Restaurantes y gastronomía típica. |
| 3 | Logística | Búsqueda (Tavily) | Vuelos, hoteles, transporte, presupuesto. |
| 4 | Planificador de itinerario | — | Organiza todo en un plan día por día. |
| 5 | Gestor de agenda | Google Calendar | Crea un evento por cada día del viaje. |
| 6 | Redactor de viajes | — | Escribe el documento final (`itinerary.md`). |

Flujo: `cultura → gastronomía → logística → itinerario → agenda → redacción`.

## Herramientas de búsqueda

- **`tools/busqueda_internet_tool.py`** — herramienta **activa**, basada en **Tavily**
  (mejor para agentes de IA: contenido extraído + respuesta sintetizada).
- **`tools/custom_tool.py`** — alternativa con **DuckDuckGo** (`ddgs`), gratis y sin
  API key. Para volver a ella, edita el import y `self.search_tool` en `crew.py`.

## El agente conversacional

Vive en `conversational_agent/` y sigue la convención modular del proyecto: cada
cosa en su carpeta, y `agent.py` solo orquesta.

| Si cambias… | Tocas |
|---|---|
| Modelo, temperatura, umbrales de memoria | `model_config/model.yaml` |
| Persona, tono, qué datos pide, cuándo lanza el crew | `prompt/system_prompt.yaml` |
| Backend de memoria | `chat_history/` |
| Lo que el agente puede hacer | `tools/` |
| Cómo se ensambla todo | `agent.py` |

### Memoria: dos piezas, no una

- **`chat_history/postgres_store.py`** — checkpointer de LangGraph
  (`AsyncPostgresSaver`). Es lo que hace que el agente recuerde el hilo. Guarda
  el estado en `BYTEA`: **no** se lee con SQL.
- **`chat_history/conversation_log.py`** — tabla plana `conversation_log`, una
  fila por mensaje. Para auditoría, QA y reportes. Es el único registro legible
  de lo que se dijo, y sobrevive al resumen del historial (que destruye el texto
  original).

Es `AsyncPostgresSaver` y no `PostgresSaver` porque el grafo corre con
`.ainvoke()`; con el saver síncrono, LangGraph falla.

### La tool que lanza el crew

`tools/generar_itinerario.py`. Dos detalles deliberados:

1. **No le devuelve el itinerario al LLM**, solo un acuse de recibo. El markdown
   completo se deposita en un `slot` que el endpoint lee aparte. Así el frontend
   recibe el markdown real del crew y el historial no engorda.
2. **Es `async`**. El crew tarda minutos; una tool síncrona bloquearía el event
   loop de FastAPI y congelaría todas las demás requests.

## Estructura

```
src/backend_proyecto_planificador_de_viajes/
├── main.py                  # API FastAPI (/chat y /plan-trip)
├── crew.py                  # Definición del crew (agentes + tareas)
├── observabilidad.py        # Langfuse: trazas del agente y del crew
├── respaldo_llm.py          # Respaldo con Gemini si OpenAI se queda sin crédito
├── config/
│   ├── agents.yaml          # Roles, objetivos y backstories
│   └── tasks.yaml           # Descripciones y outputs esperados
├── tools/                   # Tools DEL CREW
│   ├── busqueda_internet_tool.py   # Búsqueda con Tavily (activa)
│   └── custom_tool.py              # Búsqueda con DuckDuckGo (alternativa)
└── conversational_agent/    # Agente conversacional (LangChain)
    ├── agent.py                    # Orquestador
    ├── model_config/model.yaml     # LLM y umbrales de memoria
    ├── prompt/system_prompt.yaml   # System prompt (formato XML-tag)
    ├── chat_history/
    │   ├── postgres_store.py       # Checkpointer (AsyncPostgresSaver)
    │   └── conversation_log.py     # Log plano legible con SQL
    ├── tools/
    │   └── generar_itinerario.py   # El crew, como tool del agente
    └── migraciones_SQL/            # DDL numerado e idempotente
```

## Notas

- El endpoint usa `kickoff_async` (requerido dentro del event loop de FastAPI).
- El tracing interactivo de CrewAI está desactivado (`tracing=False`) para que no
  bloquee las requests en el servidor.
- El itinerario final se guarda en `itinerary.md` (vía `output_file` de la tarea de
  redacción) y se devuelve en `download_content`.

### Observabilidad con Langfuse

`observabilidad.py` traza las dos mitades del sistema en el mismo proyecto de Langfuse:

- **Agente conversacional**: `CallbackHandler` de Langfuse en el `config` de cada
  `.ainvoke()`. Cada turno de `/chat` es una traza `chat-turn`, y todas las de un
  mismo `thread_id` quedan agrupadas en una **sesión**.
- **Crew**: `CrewAIInstrumentor` de OpenInference con `use_event_listener=True`,
  que crea spans de crew, agente, tarea, tool y de cada llamada al LLM (con tokens).
  Cuando el agente lanza el crew, esos spans cuelgan de la traza del turno.
  `/plan-trip` produce su propia traza `plan-trip`.

No se instrumenta el SDK de OpenAI a propósito: el agente usa `ChatOpenAI`, que va
por ese SDK, y cada generación saldría duplicada.

### Respaldo con Gemini si OpenAI se queda sin crédito

Con `GOOGLE_CLOUD_PROJECT` en el `.env`, `respaldo_llm.py` hace que cada llamada vaya
primero a OpenAI y, **solo** si OpenAI responde `429 insufficient_quota`, se repita
esa misma llamada con `gemini-3.5-flash`. Cualquier otro error sube tal cual, para
que Gemini no tape bugs.

- **Agente conversacional**: middleware `FallbackPorCredito`. No se usa el
  `ModelFallbackMiddleware` oficial porque salta con *cualquier* error.
- **Resumen del historial**: el `SummarizationMiddleware` llama a su modelo por
  fuera del middleware anterior, así que lleva su propio respaldo.
- **Crew**: `LLMConRespaldo`, un `BaseLLM` de CrewAI compartido por los 6 agentes.

Cuando entra el respaldo, la consola muestra `💸 OpenAI sin credito: ... pasa a Gemini`.

**Gemini va por Vertex AI** (no por la API key de AI Studio, cuyo plan gratuito
permite solo 20 peticiones al día, insuficiente para un crew). Requisitos:

- Proyecto de GCP con facturación y la API `aiplatform.googleapis.com` habilitada.
- Credenciales con permiso `aiplatform.endpoints.predict` (rol *Vertex AI User*),
  vía `GOOGLE_APPLICATION_CREDENTIALS` o `gcloud auth application-default login`.
- `gemini-3.5-flash` se sirve desde `GOOGLE_CLOUD_LOCATION=global`.
- No definir `GEMINI_API_KEY` ni `GOOGLE_API_KEY`: CrewAI las prefiere a Vertex.
