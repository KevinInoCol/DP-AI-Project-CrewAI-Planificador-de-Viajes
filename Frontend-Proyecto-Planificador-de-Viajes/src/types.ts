// Filtros rápidos del planificador (chips Dónde / Cuándo / Quién / Presupuesto)
export interface TripFilters {
  destino: string; // "Italia", "Cusco"...
  cuando: string; // "Octubre, 7 días"
  quien: string; // "Pareja", "Familia"...
  presupuesto: string; // "Económico", "Medio", "Alto"
}

export const EMPTY_FILTERS: TripFilters = {
  destino: "",
  cuando: "",
  quien: "",
  presupuesto: "",
};

/**
 * Combina el texto libre del usuario con los filtros seleccionados en una
 * sola petición en lenguaje natural para el backend.
 */
export function composePrompt(freeText: string, f: TripFilters): string {
  const parts: string[] = [];
  const text = freeText.trim();
  if (text) parts.push(text);

  const extras: string[] = [];
  if (f.destino) extras.push(`Destino: ${f.destino}`);
  if (f.cuando) extras.push(`Cuándo/duración: ${f.cuando}`);
  if (f.quien) extras.push(`Viajeros: ${f.quien}`);
  if (f.presupuesto) extras.push(`Presupuesto: ${f.presupuesto}`);

  if (extras.length) {
    parts.push(
      (text ? "\n\n(Datos que ya elegí: " : "Quiero planificar un viaje. ") +
        extras.join(", ") +
        (text ? ")" : ".")
    );
  }
  return parts.join("");
}

// Respuesta del backend (endpoint /plan-trip)
export interface PlanTripResponse {
  chat_response: string;
  download_content?: string | null;
  download_filename?: string | null;
}

/**
 * Respuesta del backend (endpoint /chat).
 *
 * `reply` es lo que dice el agente conversacional.
 * `itinerary` solo llega en el turno en que el agente lanzó el crew, y trae el
 * markdown real del crew — no la versión reescrita por el LLM — para que se
 * pinte en tarjetas por día.
 * `merged` llega en true cuando el backend agrupó este mensaje con otros
 * posteriores (debounce): entonces `reply` es null y la respuesta a todo el
 * bloque llega en la request del último mensaje.
 */
export interface ChatResponse {
  reply: string | null;
  itinerary?: PlanTripResponse | null;
  merged?: boolean;
}

export type Role = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  // Contenido markdown descargable (itinerario completo), si aplica
  downloadContent?: string | null;
  downloadFilename?: string | null;
  // Marca de error para estilizar distinto
  isError?: boolean;
}

// --- Itinerario parseado para la vista visual ---
export interface ItineraryDay {
  index: number; // 1, 2, 3...
  title: string; // "Día 1 – Lunes 15 de junio"
  subtitle?: string; // segunda línea en negrita bajo el título
  bodyMarkdown: string; // el resto del contenido del día (markdown)
}

export interface ParsedItinerary {
  title?: string; // título principal del documento (# ...)
  intro?: string; // párrafos introductorios antes del primer día
  days: ItineraryDay[];
}
