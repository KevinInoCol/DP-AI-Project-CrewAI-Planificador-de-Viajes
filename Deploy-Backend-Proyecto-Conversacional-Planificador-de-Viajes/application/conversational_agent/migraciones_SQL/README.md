# Migraciones

SQL puro e idempotente. Se pegan tal cual en el editor SQL de tu Postgres
(Supabase: SQL Editor) en este orden.

| # | Archivo | Que hace | Cuando |
|---|---|---|---|
| 001 | `001_conversation_log.sql` | Crea `conversation_log`, el log plano legible con SQL. | **Antes** del primer arranque. |
| 002 | `002_rls_checkpointer.sql` | Activa RLS en las 4 tablas del checkpointer. | **Despues** del primer arranque. |

## Por que 002 va despues

Las tablas `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` y
`checkpoint_migrations` no las creas tu: las crea `AsyncPostgresSaver.setup()`
la primera vez que arranca el backend. Antes de eso, 002 falla con
`relation "checkpoints" does not exist`.

## Por que activar RLS

En Supabase, una tabla del schema `public` sin RLS se expone por PostgREST: con
la anon key se leen **todas** las conversaciones de **todos** los usuarios. Las
tablas del checkpointer nacen asi, con el badge `UNRESTRICTED`.

Activar RLS sin politicas no rompe el agente: se conecta con el rol dueno de las
tablas, que ignora RLS. Pero cierra la puerta de PostgREST.

## Nota sobre el puerto (Supabase)

Usa el **5432** (pooler en session mode) en tu `DATABASE_URL`.
El 6543 es transaction mode; funciona porque el pool ya va con
`prepare_threshold=0`, pero el 5432 es el camino sin sorpresas.
