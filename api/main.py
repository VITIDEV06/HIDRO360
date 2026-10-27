"""
API HTTP local (FastAPI): capa fina sobre services.workflow (sin lógica de negocio propia).
El backend valida estados, capacidad, responsables Y PERMISOS; la interfaz no es la única barrera.

Iniciar:  uvicorn api.main:app --port 8000      Documentación interactiva: /docs

AUTENTICACIÓN Y ROLES
  - Administración (todo salvo lo listado abajo): `Authorization: Bearer <token de sesión>` (de POST /auth/login,
    rol administrador) o el token de servicio `X-API-Token` (= HIDRO360_API_TOKEN; alias obsoleto AGUA_CLARA_API_TOKEN; actor «api-operador»,
    rol administrador, origen «servicio»). Sin credenciales → 401; sesión con rol `usuario` → 403.
    Si HIDRO360_API_TOKEN no está configurado, el token de servicio no existe: NO hay acceso administrativo
    implícito por ser localhost (el login por sesión es la vía).
  - Dispositivos: `GET /estado` y `POST /dispositivos/confirmaciones` con `X-API-Token` = token de servicio o de
    dispositivo (HIDRO360_DEVICE_TOKEN; alias obsoleto AGUA_CLARA_DEVICE_TOKEN). Sin ningún token configurado solo se aceptan peticiones de la propia
    máquina. Un token de dispositivo NO sirve en endpoints administrativos.
  - Terminales por vehículo (ESP32-S3): `X-Device-Token` (token propio de la cisterna, solo se guarda su SHA-256) en
    /dispositivo/mision, /dispositivo/eventos y /dispositivo/latido. La identidad (cisterna_id) sale del token, nunca del
    cuerpo. Límite de tasa y bloqueo suave por IP tras intentos fallidos. Un token de vehículo no sirve en rutas admin.
  - Públicos (sin credenciales, con límite de tasa por origen): /salud, /publico/*, /auth/login (solo AUTH=local).
  - HIDRO360_AUTH=supabase: `Authorization: Bearer <JWT de Supabase>`; el rol sale de la tabla `usuarios`.
  - SIN ESTADO LOCAL: sesiones, límites y bloqueos viven en la base; la API puede ejecutarse en varias instancias.
  - CORS: solo los orígenes de HIDRO360_CORS_ORIGINS (ninguno por defecto). Los errores no incluyen trazas ni detalles internos.
  - El actor autenticado prevalece sobre el `responsable` del cuerpo (opcional): no se puede firmar como otro.
"""

import hmac
import ipaddress
import json
import os
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

import config
from data import database as db
from models.schemas import (AccionDecision, Asignacion, Cisterna, ConfirmacionDispositivo, Dispositivo,
                            EstadoActual, Mision, Parada, PropuestaPlan, RegistroEvento, Reporte, SCHEMA_VERSION)
from services import audit, auth, limites, tracing, workflow
from services.auth import Actor, ErrorAutenticacion, ErrorAutorizacion, Rol
from services.limites import ErrorLimite
from services.workflow import ErrorDeNegocio

config.exigir_persistencia_produccion()  # falla al arrancar si producción no tiene DATABASE_URL

app = FastAPI(title="HIDRO360 - API", version=SCHEMA_VERSION,
              docs_url=None if config.ENTORNO() == "prod" else "/docs",
              redoc_url=None, openapi_url=None if config.ENTORNO() == "prod" else "/openapi.json")
LOCAL = {"127.0.0.1", "::1", "localhost", "testclient"}
ACTOR_SERVICIO = Actor(usuario="api-operador", rol=Rol.ADMIN, origen="servicio")

# Versión del protocolo terminal <-> servidor (GET /dispositivo/mision y /dispositivo/latido la devuelven). El firmware
# compara este entero con el que espera y avisa si son incompatibles. Súbalo ante un cambio NO retrocompatible.
PROTOCOLO_TERMINAL = 1

# Límites (TODOS persistidos en la base vía services/limites.py; ningún contador vive en el proceso).
_LOGIN_MAX, _LOGIN_VENTANA = 10, 60                      # intentos de login por origen
_DISP_MAX, _DISP_VENTANA = 240, 60                       # peticiones de terminal por origen
_DISP_FALLOS_MAX, _DISP_FALLOS_VENTANA = 10, 300         # fallos de token de terminal => bloqueo
_CRED_FALLOS_MAX, _CRED_FALLOS_VENTANA = 20, 300         # credenciales admin inválidas por origen

# Tamaño máximo de cuerpo aceptado (3 fotos de 3 MB en base64 caben con holgura). Se comprueba Content-Length Y los
# bytes realmente recibidos (cuerpos sin Content-Length o con uno falso).
MAX_CUERPO_BYTES = 16 * 1024 * 1024


def reiniciar_limites() -> None:
    """Pruebas: borra contadores de tasa, bloqueos y sesiones de la base activa."""
    auth.reiniciar_estado()


def _flag(nombre: str) -> bool:
    return os.environ.get(nombre, "").strip().lower() in ("1", "true", "yes", "si", "sí")


def _confiar_proxy() -> bool:
    """Solo se leen cabeceras de proxy (X-Forwarded-For) si estamos en producción Y se declara un proxy de confianza
    (HIDRO360_TRUST_PROXY=1, o Vercel, que pone VERCEL=1 y reescribe X-Forwarded-For). Fuera de ese caso el cliente
    puede falsificarlas, así que se ignoran y el origen es la dirección del socket."""
    return config.ENTORNO() == "prod" and (_flag("HIDRO360_TRUST_PROXY") or os.environ.get("VERCEL") == "1")


def _ip(request: Request) -> str:
    """Origen del cliente para límites. Con proxy de confianza: la entrada de X-Forwarded-For que añadió el
    último proxy de confianza (HIDRO360_PROXY_SALTOS desde la derecha, 1 por defecto: en Vercel es la IP real, porque
    la plataforma sobrescribe la cabecera); si no, x-real-ip. Valores que no sean IP se descartan."""
    directo = request.client.host if request.client else "desconocido"
    if not _confiar_proxy():
        return directo
    try:
        saltos = max(1, int(os.environ.get("HIDRO360_PROXY_SALTOS", "1")))
    except ValueError:
        saltos = 1
    candidatos: List[str] = []
    xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    if len(xff) >= saltos:
        candidatos.append(xff[-saltos])
    candidatos.append(request.headers.get("x-real-ip", "").strip())
    for c in candidatos:
        try:
            return str(ipaddress.ip_address(c))
        except ValueError:
            continue
    return directo


# ------------------------------------------------------------ autenticación (dependencias)
def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def _token_servicio_valido(token: Optional[str]) -> bool:
    esperado = config.API_TOKEN()
    return bool(esperado and token and hmac.compare_digest(token, esperado))


def _credencial_invalida(request: Request, mensaje: str) -> ErrorAutenticacion:
    """Registra un fallo de credenciales por origen. Pasado el umbral la respuesta pasa a 429 (una credencial VÁLIDA
    nunca se bloquea: este camino solo se ejecuta con credenciales inválidas)."""
    clave = "cred:" + _ip(request)
    if limites.bloqueado(clave):
        raise ErrorLimite("Demasiados intentos fallidos. Espere unos minutos.")
    limites.fallo(clave, _CRED_FALLOS_MAX, _CRED_FALLOS_VENTANA)
    return ErrorAutenticacion(mensaje)


def credencial(request: Request, authorization: Optional[str] = Header(None),
               x_api_token: Optional[str] = Header(None)) -> Optional[Actor]:
    """Actor de la petición o None si no trae credenciales. Credenciales presentes pero inválidas → 401.
    HIDRO360_AUTH=local: Bearer = sesión propia. HIDRO360_AUTH=supabase: Bearer = JWT de Supabase (ver
    services/auth_supabase.py; sin perfil o desactivado → 403). El token de servicio X-API-Token vale en ambos modos."""
    bearer = _bearer(authorization)
    if bearer:
        if config.AUTH_BACKEND() == "supabase":
            from services import auth_supabase
            try:
                return auth_supabase.actor_de_jwt(bearer)
            except ErrorAutenticacion as e:
                raise _credencial_invalida(request, str(e))
        a = auth.actor_de_token(bearer)
        if a is None:
            raise _credencial_invalida(request, "Sesión inválida o expirada.")
        return a
    if x_api_token:
        if _token_servicio_valido(x_api_token):
            return ACTOR_SERVICIO
        raise _credencial_invalida(request, "Credenciales inválidas.")
    return None


def autenticado(actor: Optional[Actor] = Depends(credencial)) -> Actor:
    if actor is None:
        raise ErrorAutenticacion("Se requieren credenciales.")
    return actor


def admin(actor: Actor = Depends(autenticado)) -> Actor:
    if actor.rol != Rol.ADMIN:
        raise ErrorAutorizacion("Esta operación requiere permisos de administrador.")
    return actor


def _autenticar_dispositivo(request: Request, token: Optional[str], tokens: List[str]) -> str:
    tokens = [t for t in tokens if t]
    if tokens:
        if token and any(hmac.compare_digest(token, t) for t in tokens):
            return "ok"
        raise HTTPException(401, "Token inválido o ausente (cabecera X-API-Token).")
    if _ip(request) not in LOCAL:
        raise HTTPException(403, "Sin tokens configurados, la API solo acepta peticiones locales.")
    return "local"


def dispositivo_u_operador(request: Request, x_api_token: Optional[str] = Header(None)) -> str:
    return _autenticar_dispositivo(request, x_api_token, [config.API_TOKEN(), config.DEVICE_TOKEN()])


def terminal(request: Request, x_device_token: Optional[str] = Header(None)) -> Dispositivo:
    """Autentica la terminal de un vehículo por su token propio. Devuelve el Dispositivo (la cisterna sale de aquí).
    Límite general y bloqueo tras fallos de token por origen, ambos en la base (valen entre instancias)."""
    ip = _ip(request)
    if limites.bloqueado("disp:" + ip):
        raise ErrorLimite("Demasiados intentos fallidos. Espere unos minutos.")
    limites.limitar("dispositivo:" + ip, _DISP_MAX, _DISP_VENTANA, "Demasiadas solicitudes.")
    d = workflow.autenticar_dispositivo(x_device_token)
    if d is None:
        limites.fallo("disp:" + ip, _DISP_FALLOS_MAX, _DISP_FALLOS_VENTANA)
        raise ErrorAutenticacion("Token de dispositivo inválido o ausente (cabecera X-Device-Token).")
    return d


def _ejecutar(actor: Optional[Actor], fn: Callable, *a, **k):
    """Ejecuta la función de negocio con el actor del contexto (el backend vuelve a comprobar el rol)."""
    with auth.como(actor):
        return fn(*a, **k)


def _resp(body_responsable: Optional[str], actor: Actor) -> str:
    return body_responsable or actor.usuario


# -------------------------------------------------------------------------- manejadores
@app.exception_handler(ErrorAutenticacion)
def _error_autenticacion(request: Request, e: ErrorAutenticacion):
    return JSONResponse(status_code=401, content={"detail": str(e)}, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(ErrorAutorizacion)
def _error_autorizacion(request: Request, e: ErrorAutorizacion):
    return JSONResponse(status_code=403, content={"detail": str(e)})


@app.exception_handler(ErrorLimite)
def _error_limite(request: Request, e: ErrorLimite):
    return JSONResponse(status_code=429, content={"detail": str(e)}, headers={"Retry-After": "60"})


@app.exception_handler(ErrorDeNegocio)
def _error_negocio(request: Request, e: ErrorDeNegocio):
    msg = str(e)
    codigo = 404 if "no existe" in msg.lower() else 409 if "Transición" in msg or "ya está" in msg else 422
    return JSONResponse(status_code=codigo, content={"detail": msg})


@app.exception_handler(RequestValidationError)
def _error_validacion(request: Request, e: RequestValidationError):
    """Mismo formato de FastAPI (detail = lista) pero SIN el valor recibido (`input`): un 422 en /auth/login no debe
    devolver la contraseña que se envió."""
    detalle = [{"loc": list(x.get("loc", [])), "msg": x.get("msg", ""), "type": x.get("type", "")} for x in e.errors()]
    return JSONResponse(status_code=422, content={"detail": detalle})


@app.exception_handler(Exception)
def _error_interno(request: Request, e: Exception):
    return JSONResponse(status_code=500, content={"detail": "Error interno."})


class _CuerpoGrande(BaseException):
    pass


class CapaSeguridad:
    """Middleware ASGI puro: (1) tamaño máximo de cuerpo, también sin Content-Length (se cuentan los bytes reales);
    (2) CORS por lista blanca exacta (config.CORS_ORIGINS(), ninguno por defecto; el comodín se ignora y nunca se
    envían credenciales de cookie: la autenticación va en cabeceras); (3) cabeceras de seguridad."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cab = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origen = cab.get("origin")
        permitido = bool(origen) and origen != "*" and origen in config.CORS_ORIGINS()
        if scope["method"] == "OPTIONS" and origen and "access-control-request-method" in cab:   # preflight
            extra = {}
            if permitido:
                extra = {"access-control-allow-origin": origen, "vary": "Origin",
                         "access-control-allow-methods": "GET, POST, PUT, DELETE",
                         "access-control-allow-headers": "Authorization, Content-Type, X-API-Token",
                         "access-control-max-age": "600"}
            await self._responder(send, 204 if permitido else 403, b"", extra)
            return
        try:
            if int(cab.get("content-length") or 0) > MAX_CUERPO_BYTES:
                raise ValueError
        except ValueError:
            await self._responder(send, 413, b'{"detail":"El env\\u00edo es demasiado grande."}', {})
            return
        recibido = 0
        iniciada = False

        async def recibir():
            nonlocal recibido
            msg = await receive()
            if msg["type"] == "http.request":
                recibido += len(msg.get("body", b""))
                if recibido > MAX_CUERPO_BYTES:
                    raise _CuerpoGrande()
            return msg

        async def enviar(msg):
            nonlocal iniciada
            if msg["type"] == "http.response.start":
                iniciada = True
                h = list(msg.get("headers", []))
                presentes = {k.decode("latin-1").lower() for k, _ in h}
                for k, v in self._cabeceras(permitido, origen).items():
                    if k not in presentes:
                        h.append((k.encode("latin-1"), v.encode("latin-1")))
                msg = {**msg, "headers": h}
            await send(msg)

        try:
            await self.app(scope, recibir, enviar)
        except _CuerpoGrande:
            if iniciada:
                raise
            await self._responder(send, 413, b'{"detail":"El env\\u00edo es demasiado grande."}', {})

    @staticmethod
    def _cabeceras(permitido: bool, origen: Optional[str]) -> Dict[str, str]:
        h = {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY",
             "content-security-policy": "frame-ancestors 'none'", "cache-control": "no-store",
             "permissions-policy": "geolocation=(), camera=(), microphone=()"}
        if config.ENTORNO() == "prod":
            h["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        if permitido:
            h["access-control-allow-origin"] = origen or ""
            h["vary"] = "Origin"
        return h

    async def _responder(self, send, estado: int, cuerpo: bytes, extra: Dict[str, str]):
        cab = {"content-type": "application/json", "content-length": str(len(cuerpo)),
               **self._cabeceras(False, None), **extra}
        await send({"type": "http.response.start", "status": estado,
                    "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in cab.items()]})
        await send({"type": "http.response.body", "body": cuerpo})


app.add_middleware(CapaSeguridad)


# ---------------------------------------------------------------- modelos de entrada
class _Cuerpo(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Credenciales(BaseModel):
    usuario: str = Field(min_length=1, max_length=80)
    clave: str = Field(min_length=1, max_length=200)


class FotoEntrada(BaseModel):
    """El nombre del cliente NO se usa para guardar el archivo; el tipo se decide por la firma binaria."""
    model_config = ConfigDict(extra="forbid")
    nombre: str = Field("", max_length=120)
    base64: str = Field(min_length=1, max_length=config.MAX_BYTES_FOTO * 4 // 3 + 200)


class ReportePublico(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texto: str = Field(min_length=1, max_length=4000)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    fotos: List[FotoEntrada] = Field(default_factory=list, max_length=config.MAX_FOTOS_POR_REPORTE)


class ReporteNuevo(_Cuerpo):
    texto: str = Field(min_length=1, max_length=4000)
    responsable: Optional[str] = Field(None, min_length=1)
    canal: str = "api"
    clave_idempotencia: Optional[str] = Field(None, max_length=80)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    fotos: List[FotoEntrada] = Field(default_factory=list, max_length=config.MAX_FOTOS_POR_REPORTE)


class Correccion(_Cuerpo):
    responsable: Optional[str] = Field(None, min_length=1)
    sector: str
    horas_sin_agua: Optional[float] = None
    personas_afectadas: Optional[int] = None
    severidad: Optional[str] = None
    calle: Optional[str] = None
    servicio_esencial: Optional[str] = None
    vulnerabilidades: Optional[List[str]] = None
    tipo_incidencia: Optional[str] = None
    referencia_ubicacion: Optional[str] = None


class Rechazo(_Cuerpo):
    responsable: Optional[str] = Field(None, min_length=1)
    motivo: str = Field(min_length=1)


class DecisionDuplicado(_Cuerpo):
    otro_id: int
    es_duplicado: bool
    responsable: Optional[str] = Field(None, min_length=1)
    motivo: str = ""


class PlanNuevo(_Cuerpo):
    responsable: Optional[str] = Field(None, min_length=1)
    metodo: str = "flujo"


class DecisionPlan(_Cuerpo):
    accion: AccionDecision
    responsable: Optional[str] = Field(None, min_length=1)
    motivo: str = ""


class EdicionPlan(_Cuerpo):
    responsable: Optional[str] = Field(None, min_length=1)
    motivo: str = Field(min_length=1)
    asignaciones: List[Asignacion]


class DispositivoNuevo(_Cuerpo):
    cisterna_id: str = Field(min_length=1, max_length=40)
    nombre: str = Field("", max_length=80)
    responsable: Optional[str] = Field(None, min_length=1)


class AccionDispositivo(_Cuerpo):
    responsable: Optional[str] = Field(None, min_length=1)


class MisionNueva(_Cuerpo):
    cisterna_id: str = Field(min_length=1, max_length=40)
    paradas: List[Parada] = Field(min_length=1, max_length=30)
    plan_id: Optional[int] = None
    recomendacion_id: Optional[int] = None
    motivo: str = Field("", max_length=300)
    responsable: Optional[str] = Field(None, min_length=1)


class MisionDesdePlan(_Cuerpo):
    cisterna_id: str = Field(min_length=1, max_length=40)
    responsable: Optional[str] = Field(None, min_length=1)


class CancelacionMision(_Cuerpo):
    motivo: str = Field(min_length=1, max_length=300)
    responsable: Optional[str] = Field(None, min_length=1)


class UsuarioNuevo(_Cuerpo):
    """Local: `usuario` = nombre y `clave` obligatoria. Supabase: `usuario` = correo, sin clave."""
    usuario: str = Field(min_length=3, max_length=120)
    rol: str = "usuario"
    clave: Optional[str] = Field(None, max_length=200)
    responsable: Optional[str] = Field(None, min_length=1)


class CambioRol(_Cuerpo):
    rol: str = Field(min_length=1, max_length=20)
    responsable: Optional[str] = Field(None, min_length=1)


class BorradoFoto(_Cuerpo):
    motivo: str = Field(min_length=1, max_length=300)
    responsable: Optional[str] = Field(None, min_length=1)


class LoteEventos(BaseModel):
    """Cada evento se valida por separado en el backend: uno defectuoso no bloquea la cola de la terminal."""
    eventos: List[Dict[str, Any]] = Field(max_length=workflow.MAX_EVENTOS_LOTE)


class Latido(_Cuerpo):
    firmware: str = Field("", max_length=40)
    wifi_rssi: Optional[int] = Field(None, ge=-150, le=0)
    pendientes: int = Field(0, ge=0, le=100000)
    uptime_s: Optional[int] = Field(None, ge=0)


# ------------------------------------------------------------------------ rutas
def _doble(metodo: str, ruta: str, alias: str, **kw):
    """Registra la misma ruta con nombre en español y su alias de la guía (/reports, /plan...)."""
    def deco(fn):
        for r in (ruta, alias):
            if r:
                app.add_api_route("/api/v1" + r, fn, methods=[metodo], **kw)
        return fn
    return deco


@app.get("/")
def raiz():
    """Identifica el servicio y dirige a la interfaz. Sin documentación interna ni datos de configuración."""
    interfaz = config.UI_URL()
    return {"servicio": "HIDRO360 · API", "equipo": config.EQUIPO,
            "interfaz": interfaz or "No configurada: defina HIDRO360_UI_URL en el servidor.",
            "salud": "/api/v1/salud"}


@app.get("/api/v1/salud")
def salud():
    """Sin secretos ni URLs. Comprueba la base de verdad: si no responde devuelve 503 y NO declara persistencia
    funcional (antes informaba «postgres» solo por leer la configuración)."""
    funcional = db.comprobar()
    cuerpo = {"ok": funcional, "schema_version": SCHEMA_VERSION, "version": config.APP_VERSION,
              "proveedor_ia": config.ai_provider(),
              "persistencia": ("postgres" if config.modo_postgres() else "sqlite") if funcional else "no_disponible",
              "autenticacion": "supabase" if config.AUTH_BACKEND() == "supabase" else "local",
              "almacenamiento_fotos": "supabase" if config.STORAGE_BACKEND() == "supabase" else "local",
              "protocolo_terminal": PROTOCOLO_TERMINAL}
    return JSONResponse(cuerpo, status_code=200 if funcional else 503)


# --- autenticación
@app.post("/api/v1/auth/login")
def login(c: Credenciales, request: Request):
    """Solo con HIDRO360_AUTH=local. Con HIDRO360_AUTH=supabase el login local queda DESHABILITADO (410): la
    persona inicia sesión en Supabase Auth y envía su JWT como `Authorization: Bearer`."""
    if config.AUTH_BACKEND() == "supabase":
        raise HTTPException(410, "El inicio de sesión local está deshabilitado: use Supabase Auth.")
    limites.limitar("loginip:" + _ip(request), _LOGIN_MAX, _LOGIN_VENTANA,
                    "Demasiados intentos. Espere un minuto e inténtelo de nuevo.")
    token = auth.iniciar_sesion(c.usuario, c.clave)  # ErrorAutenticacion genérico → 401
    a = auth.actor_de_token(token)
    return {"token": token, "rol": a.rol.value, "expira_horas": auth.SESION_HORAS}


@app.post("/api/v1/auth/logout")
def logout(authorization: Optional[str] = Header(None), actor: Actor = Depends(autenticado)):
    """Local: revoca la sesión. Supabase: el JWT es sin estado; cierre la sesión en el cliente de Supabase (y
    desactive la cuenta si necesita revocación inmediata)."""
    if config.AUTH_BACKEND() != "supabase":
        auth.cerrar_sesion(_bearer(authorization))
    return {"ok": True}


@app.get("/api/v1/auth/yo")
def yo(actor: Actor = Depends(autenticado)):
    return {"usuario": actor.usuario, "rol": actor.rol.value, "origen": actor.origen}


# --- portal público (sin cuenta): no devuelve ningún dato interno
@app.post("/api/v1/publico/reportes", status_code=201)
def publico_crear(body: ReportePublico, request: Request):
    r = _ejecutar(None, workflow.crear_reporte, body.texto, "ciudadano", canal="portal", origen=_ip(request),
                  lat=body.lat, lng=body.lng, fotos=[f.model_dump() for f in body.fotos])
    return {"codigo_seguimiento": r.codigo_seguimiento,
            "mensaje": "Recibimos su reporte. Guarde este código para consultar su estado. "
                       "Un equipo lo revisará; no se promete hora de atención."}


@app.get("/api/v1/publico/reportes/{codigo}")
def publico_consultar(codigo: str, request: Request):
    return _ejecutar(None, workflow.consultar_reporte_publico, codigo, _ip(request))


@app.get("/api/v1/publico/avisos")
def publico_avisos():
    return _ejecutar(None, workflow.avisos_publicos)


# --- dispositivos (token de dispositivo o de servicio)
@app.get("/api/v1/estado", response_model=EstadoActual)
def estado(_: str = Depends(dispositivo_u_operador)):
    """Lectura para dispositivos: plan aprobado, plan pendiente, top de prioridades, cisternas.
    Sin borradores de comunicación ni textos de ciudadanos."""
    return _ejecutar(None, workflow.estado_actual)


@app.post("/api/v1/dispositivos/confirmaciones", status_code=202)
def confirmacion(c: ConfirmacionDispositivo, _: str = Depends(dispositivo_u_operador)):
    """Un dispositivo confirma recepción/ejecución. Solo se audita; no modifica el plan."""
    _ejecutar(None, workflow.registrar_confirmacion_dispositivo, c)
    return {"aceptada": True}


# --- administración (rol administrador)
@_doble("GET", "/reportes", "/reports", response_model=List[Reporte])
def listar_reportes(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_reportes)


@_doble("POST", "/reportes", "/reports", response_model=Reporte, status_code=201)
def crear_reporte(body: ReporteNuevo, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.crear_reporte, body.texto, _resp(body.responsable, actor), canal=body.canal,
                     clave_idempotencia=body.clave_idempotencia, lat=body.lat, lng=body.lng,
                     fotos=[f.model_dump() for f in body.fotos])


@_doble("GET", "/reportes/{reporte_id}", "/reports/{reporte_id}", response_model=Reporte)
def obtener_reporte(reporte_id: int, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.obtener_reporte, reporte_id)


@_doble("POST", "/reportes/{reporte_id}/confirmar", "/reports/{reporte_id}/confirm", response_model=Reporte)
def confirmar(reporte_id: int, body: Correccion, actor: Actor = Depends(admin)):
    extra: Dict[str, Any] = {k: getattr(body, k) for k in
                             ("servicio_esencial", "vulnerabilidades", "tipo_incidencia", "referencia_ubicacion")
                             if k in body.model_fields_set}
    return _ejecutar(actor, workflow.corregir_y_confirmar, reporte_id, _resp(body.responsable, actor), body.sector,
                     body.horas_sin_agua, body.personas_afectadas, body.severidad, body.calle, **extra)


@_doble("POST", "/reportes/{reporte_id}/rechazar", "/reports/{reporte_id}/reject", response_model=Reporte)
def rechazar(reporte_id: int, body: Rechazo, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.rechazar_reporte, reporte_id, _resp(body.responsable, actor), body.motivo)


@_doble("GET", "/reportes/{reporte_id}/duplicados", "/reports/{reporte_id}/duplicates")
def duplicados(reporte_id: int, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.sugerir_duplicados, reporte_id)


@_doble("POST", "/reportes/{reporte_id}/duplicado", "/reports/{reporte_id}/duplicate", response_model=Reporte)
def decidir_duplicado(reporte_id: int, body: DecisionDuplicado, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.resolver_duplicado, reporte_id, body.otro_id, body.es_duplicado,
                     _resp(body.responsable, actor), body.motivo)


@app.get("/api/v1/prioridades")
def prioridades(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.calcular_prioridades)


@app.get("/api/v1/sectores/visibilidad")
def visibilidad(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.visibilidad_sectores)


@app.get("/api/v1/cisternas", response_model=List[Cisterna])
def cisternas(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_cisternas)


@_doble("POST", "/planes", "/plan", response_model=PropuestaPlan, status_code=201)
def generar_plan(body: PlanNuevo, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.generar_plan, _resp(body.responsable, actor), body.metodo)


@app.get("/api/v1/planes", response_model=List[PropuestaPlan])
def planes(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_planes)


@app.get("/api/v1/planes/{plan_id}", response_model=PropuestaPlan)
def plan(plan_id: int, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.obtener_plan, plan_id)


@app.put("/api/v1/planes/{plan_id}/asignaciones", response_model=PropuestaPlan)
def editar_plan(plan_id: int, body: EdicionPlan, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.editar_plan, plan_id, body.asignaciones, _resp(body.responsable, actor),
                     body.motivo)


@_doble("POST", "/planes/{plan_id}/decision", "/plan/{plan_id}/decision", response_model=PropuestaPlan)
def decidir(plan_id: int, body: DecisionPlan, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.decidir_plan, plan_id, body.accion, _resp(body.responsable, actor), body.motivo)


@_doble("GET", "/auditoria", "/audit")
def auditoria(entidad: Optional[str] = None, entidad_id: Optional[str] = None, actor: Actor = Depends(admin)):
    return {"integra": audit.verificar_integridad(), "entradas": audit.listar(entidad, entidad_id)}


@app.get("/api/v1/trazas")
def trazas(run_id: Optional[str] = None, agent: Optional[str] = None, status: Optional[str] = None,
           correlation_id: Optional[str] = None, actor: Actor = Depends(admin)):
    return {"integridad": tracing.verificar_integridad(),
            "eventos": tracing.leer(run_id=run_id, agent=agent, status=status, correlation_id=correlation_id)}


# --- fotos (solo administración; el portal público no las lee)
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@app.get("/api/v1/reportes/{reporte_id}/fotos/{foto_id}")
def foto(reporte_id: int, foto_id: str, actor: Actor = Depends(admin)):
    contenido, mime = _ejecutar(actor, workflow.obtener_foto, reporte_id, foto_id)
    return Response(content=contenido, media_type=mime, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="foto-{reporte_id}.{_EXT.get(mime, "bin")}"',
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.post("/api/v1/reportes/{reporte_id}/fotos/{foto_id}/eliminar", response_model=Reporte)
def eliminar_foto(reporte_id: int, foto_id: str, body: BorradoFoto, actor: Actor = Depends(admin)):
    """Borrado controlado (archivo + metadatos + referencia), con motivo y auditoría."""
    return _ejecutar(actor, workflow.eliminar_foto, reporte_id, foto_id, body.motivo, body.responsable or "")


# --- usuarios (administración)
@app.get("/api/v1/usuarios")
def listar_usuarios(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_usuarios_admin)


@app.post("/api/v1/usuarios", status_code=201)
def crear_usuario(body: UsuarioNuevo, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.crear_usuario_admin, body.usuario, body.rol, body.clave, body.responsable or "")


@app.post("/api/v1/usuarios/{usuario}/desactivar")
def desactivar_usuario(usuario: str, body: AccionDispositivo = AccionDispositivo(), actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.desactivar_usuario, usuario, body.responsable or "")


@app.post("/api/v1/usuarios/{usuario}/reactivar")
def reactivar_usuario(usuario: str, body: AccionDispositivo = AccionDispositivo(), actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.reactivar_usuario, usuario, body.responsable or "")


@app.post("/api/v1/usuarios/{usuario}/rol")
def cambiar_rol(usuario: str, body: CambioRol, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.cambiar_rol_usuario, usuario, body.rol, body.responsable or "")


# --- dispositivos por vehículo (administración)
def _dispositivo_json(d: Dispositivo) -> Dict[str, Any]:
    return d.model_dump(mode="json")


@app.post("/api/v1/dispositivos", status_code=201)
def registrar_dispositivo(body: DispositivoNuevo, actor: Actor = Depends(admin)):
    """El token en claro se devuelve UNA vez; después solo existe su hash."""
    d, token = _ejecutar(actor, workflow.registrar_dispositivo, body.cisterna_id, body.nombre, body.responsable or "")
    return {"dispositivo": _dispositivo_json(d), "token": token,
            "aviso": "Guarde el token ahora: no se volverá a mostrar."}


@app.get("/api/v1/dispositivos", response_model=List[Dispositivo])
def listar_dispositivos(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_dispositivos)


@app.post("/api/v1/dispositivos/{cisterna_id}/rotar-token")
def rotar_token(cisterna_id: str, body: AccionDispositivo = AccionDispositivo(), actor: Actor = Depends(admin)):
    d, token = _ejecutar(actor, workflow.rotar_token, cisterna_id, body.responsable or "")
    return {"dispositivo": _dispositivo_json(d), "token": token,
            "aviso": "El token anterior dejó de funcionar. Guarde este: no se volverá a mostrar."}


@app.post("/api/v1/dispositivos/{cisterna_id}/desactivar", response_model=Dispositivo)
def desactivar_dispositivo(cisterna_id: str, body: AccionDispositivo = AccionDispositivo(),
                           actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.desactivar_dispositivo, cisterna_id, body.responsable or "")


# --- misiones, eventos y mapa (administración)
@app.post("/api/v1/misiones", response_model=Mision, status_code=201)
def crear_mision(body: MisionNueva, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.crear_mision, body.cisterna_id, body.paradas, _resp(body.responsable, actor),
                     body.plan_id, body.recomendacion_id, body.motivo)


@app.post("/api/v1/planes/{plan_id}/misiones", response_model=Mision, status_code=201)
def crear_mision_desde_plan(plan_id: int, body: MisionDesdePlan, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.crear_mision_desde_plan, plan_id, body.cisterna_id, _resp(body.responsable, actor))


@app.get("/api/v1/misiones", response_model=List[Mision])
def listar_misiones(estado: Optional[str] = None, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_misiones, estado)


@app.get("/api/v1/misiones/{mision_id}", response_model=Mision)
def obtener_mision(mision_id: int, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.obtener_mision, mision_id)


@app.post("/api/v1/misiones/{mision_id}/cancelar", response_model=Mision)
def cancelar_mision(mision_id: int, body: CancelacionMision, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.cancelar_mision, mision_id, _resp(body.responsable, actor), body.motivo)


@app.get("/api/v1/eventos", response_model=List[RegistroEvento])
def listar_eventos(mision_id: Optional[int] = None, cisterna_id: Optional[str] = None, actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.listar_eventos, mision_id, cisterna_id)


@app.get("/api/v1/mapa")
def mapa(actor: Actor = Depends(admin)):
    return _ejecutar(actor, workflow.datos_mapa)


# --- terminal del vehículo (X-Device-Token propio; la cisterna sale del token)
@app.get("/api/v1/dispositivo/mision")
def terminal_mision(d: Dispositivo = Depends(terminal)):
    res = _ejecutar(None, workflow.dispositivo_mision_activa, d.cisterna_id)
    return {**res, "protocolo": PROTOCOLO_TERMINAL}


@app.post("/api/v1/dispositivo/eventos")
def terminal_eventos(body: LoteEventos, d: Dispositivo = Depends(terminal)):
    return {"resultados": _ejecutar(None, workflow.dispositivo_recibir_eventos, d.cisterna_id, body.eventos)}


@app.post("/api/v1/dispositivo/latido")
def terminal_latido(body: Latido, d: Dispositivo = Depends(terminal)):
    res = _ejecutar(None, workflow.dispositivo_latido, d.cisterna_id, body.firmware, body.wifi_rssi,
                    body.pendientes, body.uptime_s)
    return {**res, "protocolo": PROTOCOLO_TERMINAL}


# --- rutas de IA (api/rutas_ia.py las define; se montan aquí con la dependencia de administrador)
try:
    from api import rutas_ia as _rutas_ia
    app.include_router(_rutas_ia.crear_router(admin), prefix="/api/v1")
except ImportError:  # pragma: no cover - el módulo de IA aún no está disponible
    pass

# --- webhook de Telegram (entrada ciudadana). Se autentica con su propio secreto, no con sesión.
from api import telegram_webhook as _telegram  # noqa: E402

app.include_router(_telegram.router, prefix="/api/v1")
