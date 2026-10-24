"""
Configuracion del buffer de mensajes (debounce). Lee las MESSAGE_BUFFER_* y
REDIS_* del .env. Config pura: aqui no hay logica.
"""

import codecs
import os

from dotenv import load_dotenv

load_dotenv()

# --- Conexion a Redis ---------------------------------------------------------
# Opcion A: URL unica (rediss:// para TLS). Si esta, gana sobre las granulares.
REDIS_URL = os.getenv("REDIS_URL", "").strip()
# Opcion B: granulares. REDIS_DB es el INDICE numerico (0-15), no el nombre que
# muestra el panel de Redis Cloud (en planes Free/Fixed solo existe el 0).
REDIS_HOST = os.getenv("REDIS_HOST", "").strip()
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_USERNAME = os.getenv("REDIS_USERNAME") or None
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
REDIS_TLS = os.getenv("REDIS_TLS", "false").lower() == "true"

# --- Buffer / debounce --------------------------------------------------------
# false -> /chat responde mensaje por mensaje, como antes.
BUFFER_ENABLED = os.getenv("MESSAGE_BUFFER_ENABLED", "true").lower() == "true"
# Silencio que se espera antes de juntar los mensajes y llamar al agente.
BUFFER_WINDOW_SECONDS = float(os.getenv("MESSAGE_BUFFER_WINDOW_SECONDS", "15"))
# TTL de las claves del buffer. Tiene que ser mayor que la ventana.
BUFFER_TTL_SECONDS = int(os.getenv("MESSAGE_BUFFER_TTL_SECONDS", "300"))
# En el .env "\n" llega como texto literal (barra + n): lo decodificamos.
BUFFER_SEPARATOR = codecs.decode(
    os.getenv("MESSAGE_BUFFER_SEPARATOR", "\\n"), "unicode_escape"
)
# Tope del candado por conversacion. Cubre el turno mas largo (el que lanza el
# crew, varios minutos); si el proceso muere con el candado puesto, caduca solo.
BUFFER_LOCK_TTL_SECONDS = int(os.getenv("MESSAGE_BUFFER_LOCK_TTL_SECONDS", "900"))

# Prefijo de las claves en Redis, para no chocar con otras apps en el mismo DB.
KEY_PREFIX = "planificador:buffer"
