# Deploy — Backend del Planificador de Viajes

Carpeta de despliegue del backend conversacional (FastAPI + LangChain + CrewAI).
Lo que entra en la imagen es **siempre** la carpeta `application/`.

```
Deploy-Backend-Proyecto-Conversacional-Planificador-de-Viajes/
├── application/          # el paquete Python (copia de src/backend_proyecto_planificador_de_viajes)
├── requirements.txt      # dependencias fijadas, exportadas del uv.lock del backend
├── Dockerfile.prod       # imagen: uvicorn application.main:app en el puerto 4000
├── .dockerignore         # .env y basura fuera de la imagen
├── .env / .env.example   # solo para probar en local; NO entran en la imagen
├── cloudbuild.yaml, service.yaml, gcr-service-policy.yaml   # despliegue en Cloud Run
```

## Endpoints

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/chat` | Agente conversacional. Body: `{"message": "...", "thread_id": "..."}` |
| `POST` | `/plan-trip` | Crew directo, sin conversación. Body: `{"prompt": "..."}` |
| `GET` | `/` | Salud: `chat_disponible`, `debounce_activo` |

## Sincronizar con el backend de desarrollo

`application/` es una copia. Tras cambiar el backend, desde esta carpeta:

```bash
rsync -a --delete --exclude __pycache__ --exclude .DS_Store \
  ../Backend-Proyecto-Planificador-de-Viajes/src/backend_proyecto_planificador_de_viajes/ application/

# Si cambiaron dependencias:
(cd ../Backend-Proyecto-Planificador-de-Viajes && uv export --frozen --no-hashes \
  --no-emit-project --no-dev --format requirements-txt -o ../Deploy-Backend-Proyecto-Conversacional-Planificador-de-Viajes/requirements.txt)
```

## Probar la imagen en local

```bash
docker build -f Dockerfile.prod -t planificador-backend:local .
docker run --rm -p 4000:4000 --env-file .env planificador-backend:local
curl http://localhost:4000/
```

Para Cloud Run la imagen tiene que ser `linux/amd64`:
`docker buildx build --platform linux/amd64 -f Dockerfile.prod ...`

## Variables de entorno en producción

Las mismas que `.env.example`, con estas diferencias:

- `GOOGLE_APPLICATION_CREDENTIALS`: **no se define**. En Cloud Run, Vertex usa la
  cuenta de servicio del servicio, que necesita el rol *Vertex AI User*
  (`roles/aiplatform.user`) en `GOOGLE_CLOUD_PROJECT`.
- `CORS_ORIGINS`: el dominio del frontend, p. ej. `https://planificador-viajes-frontend.vercel.app`.
- Secretos (`OPENAI_API_KEY`, `DB_PASSWORD`, `LANGFUSE_SECRET_KEY`, `REDIS_URL`...)
  mejor desde Secret Manager que como texto plano.
- En el `.env`, nada de comentarios al final de una línea: `docker --env-file` los
  toma como parte del valor.
