# Paso 1
gcloud init

# Paso 2: Crear el GAR (Repositorio de Artifact Registry)
gcloud artifacts repositories create <NOMBRE_PARA_TU_REPOSITORIO> --repository-format docker --project <NOMBRE_DE_TU_PROYECTO> --location us-central1

# Paso 3: Creamos la imagen y Pusheamos al GAR.
gcloud builds submit --config=cloudbuild.yaml --substitutions=_VERSION=v1 --project <NOMBRE_DE_TU_PROYECTO>

# Paso 4: Desplegamos el servicio en Cloud Run con la imagen del GAR
gcloud run services replace service.yaml --region us-central1 --project <NOMBRE_DE_TU_PROYECTO>

# Paso 5: Modificar los accesos al Cloud Run.
gcloud run services set-iam-policy <NOMBRE_DEL_SERVICIO> gcr-service-policy.yaml --region us-central1 --project <NOMBRE_DE_TU_PROYECTO>