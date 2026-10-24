import { useEffect, useMemo, useRef, useState } from "react";
import { Plane, Compass } from "lucide-react";
import Sidebar from "./components/Sidebar";
import TripFilters from "./components/TripFilters";
import ChatInput from "./components/ChatInput";
import ChatMessageView from "./components/ChatMessageView";
import AgentProgress from "./components/AgentProgress";
import TypingIndicator from "./components/TypingIndicator";
import DestImage from "./components/DestImage";
import { chat } from "./lib/api";
import { destinationImage } from "./lib/images";
import { EMPTY_FILTERS, composePrompt } from "./types";
import type { ChatMessage, TripFilters as Filters } from "./types";

const USER_NAME = "Kevin";

// Segundos de espera a partir de los cuales asumimos que el agente lanzó el
// crew y cambiamos el indicador de "escribiendo" a la línea de tiempo. Un turno
// conversacional normal responde muy por debajo de esto.
const SEGUNDOS_HASTA_CREW = 6;

// Ventana de debounce del backend (MESSAGE_BUFFER_WINDOW_SECONDS): el backend
// espera este silencio antes de responder, para juntar mensajes seguidos. Se
// suma a SEGUNDOS_HASTA_CREW para no confundir esa espera con el crew.
const SEGUNDOS_DEBOUNCE = Number(
  import.meta.env.VITE_MESSAGE_BUFFER_WINDOW_SECONDS ?? 15
);

// Tarjetas de inspiración de la pantalla de bienvenida
const INSPIRATION: { title: string; query: string; prompt: string }[] = [
  {
    title: "Costa de Italia en pareja",
    query: "italy amalfi coast",
    prompt:
      "Un viaje de 10 días por la costa de Italia para una pareja, enfocado en comida y cultura.",
  },
  {
    title: "Naturaleza en Costa Rica",
    query: "costa rica rainforest",
    prompt:
      "Una aventura de 2 semanas en Costa Rica para amantes de la naturaleza con presupuesto moderado.",
  },
  {
    title: "3 días en Nueva York",
    query: "new york city",
    prompt: "¿Qué puedo hacer en 3 días en Nueva York con un presupuesto de $500?",
  },
  {
    title: "Cusco e Inti Raymi",
    query: "cusco peru",
    prompt:
      "4 días en Cusco en junio para vivir el Inti Raymi, historia inca y gastronomía.",
  },
];

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

// Identidad del hilo de conversación. El backend la usa como thread_id del
// checkpointer, así que mientras no cambie, el agente recuerda todo el hilo.
const nuevoThreadId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `thread-${Date.now()}-${Math.random().toString(16).slice(2)}`;

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Requests a /chat en vuelo. Con el debounce el usuario puede escribir varios
  // mensajes seguidos sin esperar, así que puede haber más de una.
  const [pendientes, setPendientes] = useState(0);
  // Momento del último envío: cada mensaje nuevo reinicia la ventana del backend.
  const [ultimoEnvio, setUltimoEnvio] = useState(0);
  const loading = pendientes > 0;
  const [esperaLarga, setEsperaLarga] = useState(false);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [threadId, setThreadId] = useState(nuevoThreadId);
  const scrollRef = useRef<HTMLDivElement>(null);

  const hasConversation = messages.length > 0;
  const hasAnyFilter = useMemo(
    () => Object.values(filters).some(Boolean),
    [filters]
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, loading]);

  // Si el turno se alarga, es que el crew está corriendo: pasamos de los
  // puntitos a la línea de tiempo de los agentes.
  useEffect(() => {
    if (!loading) {
      setEsperaLarga(false);
      return;
    }
    setEsperaLarga(false);
    const t = setTimeout(
      () => setEsperaLarga(true),
      (SEGUNDOS_DEBOUNCE + SEGUNDOS_HASTA_CREW) * 1000
    );
    return () => clearTimeout(t);
  }, [loading, ultimoEnvio]);

  const resetChat = () => {
    if (loading) return;
    setMessages([]);
    setFilters(EMPTY_FILTERS);
    // Hilo nuevo = memoria nueva en el backend.
    setThreadId(nuevoThreadId());
  };

  const handleSend = async (freeText: string) => {
    const prompt = composePrompt(freeText, filters);
    if (!prompt.trim()) return;

    const userMsg: ChatMessage = {
      id: nextId(),
      role: "user",
      content: prompt,
    };
    setMessages((prev) => [...prev, userMsg]);
    setPendientes((n) => n + 1);
    setUltimoEnvio(Date.now());

    try {
      const res = await chat(prompt, threadId);

      // Agrupado con mensajes posteriores: la respuesta llega en otra request.
      if (res.merged) return;

      const nuevos: ChatMessage[] = [
        {
          id: nextId(),
          role: "assistant",
          content:
            res.reply || "Lo siento, no pude generar una respuesta esta vez.",
        },
      ];

      // El itinerario va como mensaje aparte: ChatMessageView lo detecta por el
      // markdown y lo pinta en tarjetas por día en vez de como burbuja.
      if (res.itinerary) {
        nuevos.push({
          id: nextId(),
          role: "assistant",
          content: res.itinerary.chat_response,
          downloadContent: res.itinerary.download_content,
          downloadFilename: res.itinerary.download_filename,
        });
      }

      setMessages((prev) => [...prev, ...nuevos]);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      const isAbort = detail.toLowerCase().includes("abort");
      const sinAgente = detail.includes("503") || detail.includes("Postgres");

      let content: string;
      if (isAbort) {
        content =
          "**La solicitud tardó demasiado y se canceló.** El equipo de agentes puede tardar varios minutos; intenta de nuevo o simplifica tu petición.";
      } else if (sinAgente) {
        content = `**El agente conversacional no está disponible.**\n\nRevisa las variables \`DB_*\` en el \`.env\` del backend y que las migraciones de \`conversational_agent/migraciones_SQL/\` estén aplicadas.\n\n*Detalle: ${detail}*`;
      } else {
        content = `**No pude comunicarme con el asistente.**\n\nVerifica que el backend esté corriendo en \`http://localhost:8005\`.\n\n*Detalle: ${detail}*`;
      }

      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "assistant", isError: true, content },
      ]);
    } finally {
      setPendientes((n) => n - 1);
    }
  };

  return (
    <div className="flex h-full">
      <Sidebar onNewChat={resetChat} userName={USER_NAME} />

      <main className="flex min-w-0 flex-1 flex-col">
        {/* Top bar con chips de filtros */}
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white/80 px-4 py-3 backdrop-blur-xl">
          <div className="flex items-center gap-2 md:hidden">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700">
              <Plane className="h-4 w-4 text-white" />
            </div>
          </div>
          <div className="flex-1 overflow-x-auto">
            <TripFilters value={filters} onChange={setFilters} />
          </div>
        </header>

        {/* Área de scroll */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {!hasConversation ? (
              <div className="animate-float-in mx-auto max-w-2xl pt-6 text-center">
                <div className="mx-auto mb-5 grid h-16 w-16 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 shadow-lg shadow-brand-500/30">
                  <Compass className="h-8 w-8 text-white" />
                </div>
                <h2 className="text-2xl font-bold text-slate-900 sm:text-3xl">
                  ¿Adónde vamos hoy, {USER_NAME}?
                </h2>
                <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
                  Cuéntame la idea, aunque esté a medias. Vamos conversando los
                  detalles y, cuando el plan esté claro, mi equipo arma el
                  itinerario día por día.
                </p>

                <div className="mt-8 grid grid-cols-2 gap-3 text-left sm:grid-cols-4">
                  {INSPIRATION.map((item) => (
                    <button
                      key={item.title}
                      type="button"
                      onClick={() => handleSend(item.prompt)}
                      className="group overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition-all hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-md"
                    >
                      <DestImage
                        src={destinationImage(item.query, {
                          w: 400,
                          h: 300,
                          seed: item.title,
                        })}
                        alt={item.title}
                        className="h-24 w-full"
                      />
                      <span className="block p-3 text-xs font-semibold leading-snug text-slate-700 group-hover:text-brand-700">
                        {item.title}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {messages.map((m) => (
                  <ChatMessageView
                    key={m.id}
                    message={m}
                    destinationHint={filters.destino}
                  />
                ))}
                {loading && (
                  <div className="flex gap-3">
                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-white">
                      <Compass className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      {esperaLarga ? <AgentProgress /> : <TypingIndicator />}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Composer */}
        <div className="border-t border-slate-200 bg-white/80 backdrop-blur-xl">
          <div className="mx-auto max-w-3xl px-4 py-3">
            <ChatInput
              onSend={handleSend}
              canSendEmpty={hasAnyFilter}
            />
            <p className="mt-1.5 px-1 text-center text-[11px] text-slate-400">
              El asistente puede cometer errores. Verifica precios y
              disponibilidad. · Enter para enviar
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
