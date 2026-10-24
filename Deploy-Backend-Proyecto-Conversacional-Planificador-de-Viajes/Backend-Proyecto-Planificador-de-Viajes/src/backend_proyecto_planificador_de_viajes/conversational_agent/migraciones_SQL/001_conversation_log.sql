CREATE TABLE IF NOT EXISTS conversation_log (
    id           BIGSERIAL PRIMARY KEY,
    thread_id    TEXT        NOT NULL,
    rol          TEXT        NOT NULL CHECK (rol IN ('user', 'assistant')),
    contenido    TEXT        NOT NULL,
    tools_usadas JSONB       NOT NULL DEFAULT '[]'::jsonb,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_log_thread
    ON conversation_log (thread_id, creado_en);

ALTER TABLE conversation_log ENABLE ROW LEVEL SECURITY;
