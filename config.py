"""
Configuración de HIDRO360.
Todo es local: sin internet, sin claves, sin hardware.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _cargar_dotenv(ruta: Path) -> None:
    """Carga KEY=VALUE de .env SIN pisar variables ya definidas en el entorno (sin dependencias).
    .env está en .gitignore: ahí van GEMINI_API_KEY y los tokens, nunca en el código."""
    # Las pruebas NUNCA leen .env (podria apuntar a una base o a claves reales): se exportan a mano en el
    # entorno del proceso cuando se quiere una prueba real. HIDRO360_ENV=test cubre los subprocesos de prueba.
    if "pytest" in sys.modules or os.environ.get("HIDRO360_ENV", "").strip().lower() == "test":
        return
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valor = valor.strip().strip('"').strip("'")
        if clave.strip() and valor:
            os.environ.setdefault(clave.strip(), valor)


_cargar_dotenv(BASE_DIR / ".env")


def _env_compat(nombre: str, antiguo: str, defecto: str = "") -> str:
    """Lee HIDRO360_<X>; si no existe (o está vacía) cae a la variable antigua AGUA_CLARA_<X>, obsoleta pero
    aceptada por compatibilidad. Si ambas existen gana HIDRO360_*."""
    return os.environ.get(nombre, "").strip() or os.environ.get(antiguo, "").strip() or defecto


# Base de datos SQLite local (HIDRO360_DB; alias obsoleto AGUA_CLARA_DB, p. ej. para pruebas)
DB_PATH = Path(_env_compat("HIDRO360_DB", "AGUA_CLARA_DB") or BASE_DIR / "agua_clara.db")

APP_NAME = "HIDRO360"
EQUIPO = "TEAM VIFY AI"
APP_VERSION = "0.6.0"
VERSION_REGLAS = "reglas-2026.10.b"   # cambia si cambian pesos, topes o reglas deterministas

# Parámetros del cálculo de prioridad (deterministas y visibles en la interfaz)
PESO_HORAS = 0.40
PESO_POBLACION = 0.30
PESO_VULNERABILIDAD = 0.30
HORAS_TOPE = 96.0           # a partir de 96 h sin agua el factor llega a su máximo
PERSONAS_TOPE = 300         # a partir de 300 personas afectadas el factor llega a su máximo
VULNERABILIDAD_VALORES = {"baja": 0.25, "media": 0.5, "alta": 0.75, "crítica": 1.0}

# Parámetros del planificador
LITROS_POR_PERSONA = 15     # litros de emergencia por persona (valor ficticio de demo)
CATEGORIAS = [(70, "CRÍTICA"), (50, "ALTA"), (30, "MEDIA"), (0, "BAJA")]

# Duplicados: umbral conservador de sugerencia. Nunca se fusiona automáticamente.
UMBRAL_DUPLICADO = 0.55

# Vulnerabilidad efectiva (la confirma la persona en la revisión; ver services/prioritization.py)
VULN_SERVICIO_ESENCIAL = 1.0     # hospital/centro de salud/escuela confirmado en un reporte del sector
VULN_PERSONAS_VULNERABLES = 0.75  # adultos mayores, niños pequeños, enfermos mencionados y confirmados

# Trazas técnicas JSONL (distintas de la auditoría de negocio)
# Fotografías subidas: fuera del código y de Git. En la Raspberry: /var/lib/hidro360/media.
MEDIA_DIR = Path(_env_compat("HIDRO360_MEDIA_DIR", "AGUA_CLARA_MEDIA_DIR") or BASE_DIR / "media")
MAX_FOTOS_POR_REPORTE = 3
# Tope por foto. Local/Raspberry: 3 MiB. En Vercel el cuerpo de una peticion no puede superar 4,5 MB y las fotos viajan
# en base64 (+33 %): para 3 fotos use HIDRO360_MAX_BYTES_FOTO=1048576 (3 x 1 MiB ~ 4,2 MB). Ver docs/DESPLIEGUE.md.
MAX_BYTES_FOTO = int(os.environ.get("HIDRO360_MAX_BYTES_FOTO", "").strip() or 3 * 1024 * 1024)
_traza = _env_compat("HIDRO360_TRACE", "AGUA_CLARA_TRACE")
TRACE_PATH = Path(_traza) if _traza and _traza.lower() != "stdout" else BASE_DIR / "logs" / "trazas.jsonl"

# Envío duplicado accidental: mismo texto en menos de N segundos => se devuelve el reporte ya creado
IDEMPOTENCIA_SEGUNDOS = 30

# Duplicados: ventana en la que un reporte nuevo sigue "pareciendo el mismo incidente"
VENTANA_DUPLICADO_HORAS = 72

# Sectores sin reportes en este lapso se consideran "sin reportes recientes"
VENTANA_REPORTES_RECIENTES_HORAS = 72


def _env(nombre: str, defecto: str = "") -> str:
    return os.environ.get(nombre, defecto).strip()


# --- IA (Gemini opcional) -------------------------------------------------------------------
# AI_PROVIDER: "auto" (por defecto) usa Gemini SOLO si hay GEMINI_API_KEY; "gemini" lo fuerza;
# "local" desactiva todo envío externo (reglas deterministas). Si Gemini falla se usa el modo local
# y queda registrado; nunca se presenta el resultado local como si fuera de Gemini.
GEMINI_MODELO_POR_DEFECTO = "gemini-3.5-flash-lite"  # según ai.google.dev/gemini-api/docs/models (verificar; cambia con GEMINI_MODEL)


def ai_provider() -> str:
    """Proveedor efectivo: 'gemini' o 'local'."""
    p = _env("AI_PROVIDER", "auto").lower()
    if p == "auto":
        return "gemini" if GEMINI_API_KEY() else "local"
    return "gemini" if p == "gemini" else "local"


GEMINI_API_KEY = lambda: _env("GEMINI_API_KEY")  # noqa: E731
GEMINI_MODEL = lambda: _env("GEMINI_MODEL", GEMINI_MODELO_POR_DEFECTO)  # noqa: E731
AI_TIMEOUT_SECONDS = lambda: float(_env("AI_TIMEOUT_SECONDS", "15"))  # noqa: E731
AI_MAX_REINTENTOS = lambda: int(_env("AI_MAX_REINTENTOS", "1"))  # noqa: E731  (solo errores transitorios)

# Autenticación de la API (opcional pero recomendada fuera de localhost).
# Sin token configurado, la API solo acepta peticiones de escritura desde 127.0.0.1.
# HIDRO360_* gana; AGUA_CLARA_* sigue funcionando como alias obsoleto. Se leen en cada llamada (las pruebas los cambian).
API_TOKEN = lambda: _env_compat("HIDRO360_API_TOKEN", "AGUA_CLARA_API_TOKEN")        # operadores (escritura)  # noqa: E731
DEVICE_TOKEN = lambda: _env_compat("HIDRO360_DEVICE_TOKEN", "AGUA_CLARA_DEVICE_TOKEN")  # dispositivo heredado (lectura de /estado y confirmaciones)  # noqa: E731


# --- Perfil y persistencia -------------------------------------------------------------------
# HIDRO360_ENV: dev (por defecto) | test | prod. En test no se lee .env (ver _cargar_dotenv).
ENTORNO = lambda: _env("HIDRO360_ENV", "dev").lower()  # noqa: E731
# DATABASE_URL: cadena PostgreSQL (Supabase: pooler o conexion directa). Vacia => SQLite local en DB_PATH.
# Es un secreto (lleva la contrasena): solo en el entorno del servidor, nunca en el repositorio ni en el navegador.
DATABASE_URL = lambda: _env("DATABASE_URL")  # noqa: E731


def modo_postgres() -> bool:
    """HIDRO360_BD=sqlite fuerza SQLite aunque DATABASE_URL exista (demostración local: nunca toca Supabase).
    HIDRO360_BD=postgres exige DATABASE_URL. Sin variable: PostgreSQL si hay DATABASE_URL."""
    modo = _env("HIDRO360_BD", "").lower()
    if modo == "sqlite":
        return False
    return bool(DATABASE_URL())


# --- Servicios gestionados (Supabase) --------------------------------------------------------
# AUTH: "local" (cuentas propias en la base: scrypt + sesiones persistidas) | "supabase" (JWT de Supabase Auth,
# verificado en el backend; el rol sale de la tabla de usuarios, nunca de claims editables por el cliente).
AUTH_BACKEND = lambda: _env("HIDRO360_AUTH", "local").lower()  # noqa: E731
# STORAGE: "local" (carpeta MEDIA_DIR) | "supabase" (bucket PRIVADO; acceso solo desde el backend).
STORAGE_BACKEND = lambda: _env("HIDRO360_STORAGE", "local").lower()  # noqa: E731
SUPABASE_URL = lambda: _env("SUPABASE_URL").rstrip("/")  # noqa: E731
SUPABASE_ANON_KEY = lambda: _env("SUPABASE_ANON_KEY")  # publica por diseno (RLS la limita)  # noqa: E731
SUPABASE_SERVICE_ROLE_KEY = lambda: _env("SUPABASE_SERVICE_ROLE_KEY")  # PRIVILEGIADA: solo backend  # noqa: E731
SUPABASE_JWT_SECRET = lambda: _env("SUPABASE_JWT_SECRET")  # solo proyectos con secreto simetrico (HS256)  # noqa: E731
SUPABASE_STORAGE_BUCKET = lambda: _env("SUPABASE_STORAGE_BUCKET", "reportes-fotos")  # noqa: E731
# Bucket PRIVADO para notas de voz / audios (mismo mecanismo que las fotos, distinto bucket).
SUPABASE_AUDIO_BUCKET = lambda: _env("SUPABASE_AUDIO_BUCKET", "reportes-audios")  # noqa: E731
# Origenes permitidos por CORS (lista separada por comas). Vacio => sin CORS (mismo origen / dispositivos).
CORS_ORIGINS = lambda: [o.strip() for o in _env("HIDRO360_CORS_ORIGINS").split(",") if o.strip()]  # noqa: E731
# Trazas: HIDRO360_TRACE=stdout escribe JSON por linea en la salida estandar (entornos sin disco persistente).
TRACE_A_STDOUT = lambda: _env_compat("HIDRO360_TRACE", "AGUA_CLARA_TRACE").lower() == "stdout"  # noqa: E731


# --- Notas de voz / audio (adjuntos) ---------------------------------------------------------
# Tope por audio. En Vercel el cuerpo de peticion del webhook lo fija Telegram (los archivos se DESCARGAN aparte,
# por eso puede ser mayor que las fotos del portal). 10 MiB cubre notas de voz y audios habituales de Telegram.
MAX_BYTES_AUDIO = int(os.environ.get("HIDRO360_MAX_BYTES_AUDIO", "").strip() or 10 * 1024 * 1024)
MAX_AUDIOS_POR_REPORTE = 2
# Adaptador de transcripcion: "simulada" (sin red ni claves; no transcribe, lo declara) es el unico implementado.
# Para conectar un proveedor real cree una implementacion en services/transcripcion.py y usela aqui.
TRANSCRIPCION_BACKEND = lambda: _env("HIDRO360_TRANSCRIPCION", "simulada").lower()  # noqa: E731


# --- Telegram (canal de entrada de reportes) -------------------------------------------------
# El bot se configura SOLO por variables de entorno; sin bot_token el webhook responde 503 y no se procesa nada.
TELEGRAM_BOT_TOKEN = lambda: _env("TELEGRAM_BOT_TOKEN")            # [SECRETO] solo servidor  # noqa: E731
TELEGRAM_WEBHOOK_SECRET = lambda: _env("TELEGRAM_WEBHOOK_SECRET")  # [SECRETO] cabecera del webhook  # noqa: E731
# Ruta del webhook (configurable). Telegram la conoce al registrar el webhook con setWebhook.
TELEGRAM_WEBHOOK_PATH = lambda: _env("TELEGRAM_WEBHOOK_PATH", "/api/v1/telegram/webhook")  # noqa: E731
TELEGRAM_API_BASE = lambda: _env("TELEGRAM_API_BASE", "https://api.telegram.org")  # noqa: E731 (pruebas)
TELEGRAM_MAX_MENSAJES = int(os.environ.get("HIDRO360_TELEGRAM_MAX_MENSAJES", "").strip() or 20)   # por ventana
TELEGRAM_VENTANA_S = int(os.environ.get("HIDRO360_TELEGRAM_VENTANA_S", "").strip() or 60)
TELEGRAM_MAX_TEXTO = 4000          # igual que el limite de crear_reporte
# URL publica de la app (para mensajes/enlaces; no es un secreto). Vacia en local.
PUBLIC_APP_URL = lambda: _env("PUBLIC_APP_URL").rstrip("/")  # noqa: E731


def exigir_persistencia_produccion() -> None:
    """En Vercel o con HIDRO360_ENV=prod la base DEBE ser PostgreSQL/Supabase: el disco de una función es efímero y
    un SQLite silencioso perdería reportes, sesiones y misiones. Se falla al arrancar en vez de degradar."""
    en_produccion = bool(os.environ.get("VERCEL")) or ENTORNO() == "prod"
    if en_produccion and not modo_postgres():
        raise RuntimeError("Falta DATABASE_URL: en producción HIDRO360 necesita PostgreSQL/Supabase; "
                           "no arranca con SQLite temporal.")

# URL pública de la interfaz Flet (servicio aparte del backend). Solo se muestra en GET / de la API.
UI_URL = lambda: _env("HIDRO360_UI_URL").rstrip("/")  # noqa: E731
