"""
Identidad con Supabase Auth, autorización en NUESTRA base (HIDRO360_AUTH=supabase).

Decisión de diseño: Supabase dice QUIÉN es la persona (JWT firmado); nuestra tabla `usuarios` dice QUÉ puede hacer.

- El backend verifica el JWT de acceso: firma, `exp`, `nbf`, `iss` ({SUPABASE_URL}/auth/v1) y `aud` ("authenticated").
  * Proyectos con claves asimétricas (ES256 / RS256 / EdDSA): clave pública desde el JWKS
    ({SUPABASE_URL}/auth/v1/.well-known/jwks.json), elegida por `kid`. Caché corta (JWKS_TTL_S) y, si llega un `kid`
    desconocido, una recarga forzada con enfriamiento (JWKS_ENFRIAMIENTO_S) para soportar rotación sin permitir que
    tokens falsos provoquen una avalancha de peticiones.
  * Proyectos legados (HS256): secreto SUPABASE_JWT_SECRET.
  * Se rechaza `alg=none`, cualquier otro algoritmo y la confusión de algoritmos: el algoritmo de la cabecera decide
    QUÉ tipo de clave se usa (HS256 solo con el secreto; asimétrico solo con claves del JWKS), nunca al revés, y
    se pasa a PyJWT una lista de un único algoritmo.
- El ROL sale de la tabla local `usuarios` (campo `rol`). Los claims editables por el cliente (`user_metadata`) y
  `app_metadata` NO se leen jamás para autorizar.
- Vínculo: el perfil local guarda `supabase_uid` (= claim `sub`). Un perfil creado con `crear_perfil` queda
  «pendiente» (solo email); en el primer acceso se vincula por email y desde entonces manda el `sub`. Si el email
  ya está vinculado a otro `sub` se deniega. Recomendación: DESACTIVAR el registro abierto en Supabase
  (Authentication → Providers → Email → «Allow new users to sign up» = off) y dar de alta cuentas desde el panel de
  Supabase; así nadie puede registrar el email de un administrador antes que él.
- Sin perfil o con perfil desactivado → 403 (la sesión de Supabase puede seguir vigente, pero aquí no vale nada):
  revocación inmediata sin esperar a que caduque el JWT.
- Ciudadanos: siguen sin cuenta (código de seguimiento); esto solo aplica a la administración.

Estado de módulo: únicamente la caché del JWKS (clave pública, sin secretos). Es inocua entre instancias: cada una la
rellena sola y, si está vacía o vieja, se vuelve a pedir. NO probado contra un proyecto Supabase real.
"""

import re
import time
from typing import Any, Dict, Optional

import config
from data import database as db
from services import tracing
from services.auth import Actor, ErrorAutenticacion, ErrorAutorizacion, Rol

JWKS_TTL_S = 600
JWKS_ENFRIAMIENTO_S = 30
ALG_ASIMETRICOS = ("ES256", "RS256", "EdDSA")
ALG_SIMETRICOS = ("HS256",)
HOLGURA_RELOJ_S = 30
_RE_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}")

# Caché del JWKS: {"claves": {kid: jwk}, "cargado": epoch, "intento": epoch}. Clave pública; inocua entre instancias.
_jwks_cache: Dict[str, Any] = {}
# Transporte HTTP inyectable (solo pruebas): httpx.MockTransport. None en producción.
_transport: Optional[Any] = None


def reiniciar_cache() -> None:
    _jwks_cache.clear()


def _issuer() -> str:
    return config.SUPABASE_URL() + "/auth/v1"


def _cargar_jwks(forzar: bool = False) -> Dict[str, Any]:
    import httpx
    ahora = time.time()
    c = _jwks_cache
    if c and not forzar and ahora - c.get("cargado", 0) < JWKS_TTL_S:
        return c["claves"]
    if c and forzar and ahora - c.get("forzado", 0) < JWKS_ENFRIAMIENTO_S:
        return c["claves"]
    url = _issuer() + "/.well-known/jwks.json"
    if forzar:
        c["forzado"] = ahora
    try:
        with httpx.Client(timeout=5.0, transport=_transport) as cli:
            r = cli.get(url)
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        claves = {k["kid"]: k for k in r.json().get("keys", []) if k.get("kid")}
    except Exception as e:
        tracing.emitir("auth", "jwks", "error", None, {"error": type(e).__name__})
        if c.get("claves"):         # mejor una caché vieja que dejar a todos fuera por un fallo de red puntual
            return c["claves"]
        raise ErrorAutenticacion("No se pudo verificar la identidad. Intente de nuevo.")
    c["claves"], c["cargado"] = claves, ahora
    return claves


def _clave_publica(kid: str):
    import jwt
    claves = _cargar_jwks()
    if kid not in claves:
        claves = _cargar_jwks(forzar=True)     # rotación de claves
    if kid not in claves:
        raise ErrorAutenticacion("Token inválido.")
    try:
        return jwt.PyJWK(claves[kid]).key
    except Exception:
        raise ErrorAutenticacion("Token inválido.")


def verificar_jwt(token: str) -> Dict[str, Any]:
    """Devuelve los claims si el JWT es válido; si no, ErrorAutenticacion (mensaje genérico, sin detalles)."""
    import jwt
    if not token or len(token) > 8192 or token.count(".") != 2:
        raise ErrorAutenticacion("Token inválido.")
    if not config.SUPABASE_URL():
        raise ErrorAutenticacion("Autenticación con Supabase no configurada.")
    try:
        cab = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise ErrorAutenticacion("Token inválido.")
    alg = cab.get("alg")
    if alg in ALG_SIMETRICOS:
        secreto = config.SUPABASE_JWT_SECRET()
        if not secreto:
            raise ErrorAutenticacion("Token inválido.")
        clave = secreto
    elif alg in ALG_ASIMETRICOS:
        kid = cab.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ErrorAutenticacion("Token inválido.")
        clave = _clave_publica(kid)
    else:                                        # none, HS384/512, PS*, etc.
        raise ErrorAutenticacion("Token inválido.")
    try:
        claims = jwt.decode(token, clave, algorithms=[alg], audience="authenticated", issuer=_issuer(),
                            leeway=HOLGURA_RELOJ_S,
                            options={"require": ["exp", "sub", "aud", "iss"], "verify_signature": True})
    except jwt.ExpiredSignatureError:
        raise ErrorAutenticacion("Sesión expirada.")
    except jwt.PyJWTError:
        raise ErrorAutenticacion("Token inválido.")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ErrorAutenticacion("Token inválido.")
    if claims.get("role") not in (None, "authenticated"):       # p. ej. un JWT anon/service_role no es una persona
        raise ErrorAutenticacion("Token inválido.")
    return claims


# --------------------------------------------------------------------------------- perfiles
def _perfiles():
    return db.listar("usuarios")


def _normalizar_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _RE_EMAIL.fullmatch(e):
        raise ValueError("Correo inválido.")
    return e


def crear_perfil(email: str, rol: Rol = Rol.ADMIN, supabase_uid: Optional[str] = None) -> dict:
    """Alta controlada: crea el perfil local (rol + estado) vinculado a una cuenta de Supabase. No crea la cuenta en
    Supabase (hágalo en el panel con el registro abierto desactivado). Falla si ya existe."""
    from datetime import datetime
    e = _normalizar_email(email)
    if db.obtener("usuarios", e) is not None:
        raise ValueError("Ya existe un perfil con ese correo.")
    if supabase_uid and any(p.get("supabase_uid") == supabase_uid for p in _perfiles()):
        raise ValueError("Esa cuenta de Supabase ya está vinculada a otro perfil.")
    doc = {"usuario": e, "email": e, "rol": Rol(rol).value, "activo": True, "supabase_uid": supabase_uid or None,
           "kdf": None, "creado": datetime.now().isoformat(timespec="seconds")}
    db.insertar("usuarios", doc, clave=e)
    tracing.emitir("auth", "crear_perfil_supabase", "ok", {"usuario": e, "rol": doc["rol"]})
    return doc


def _buscar_perfil(sub: str, email: Optional[str]) -> Optional[dict]:
    perfiles = _perfiles()
    for p in perfiles:
        if p.get("supabase_uid") == sub:
            return p
    if email:
        e = email.strip().lower()
        for p in perfiles:
            if (p.get("email") or "").lower() == e:
                if p.get("supabase_uid") and p["supabase_uid"] != sub:
                    return None           # el correo está vinculado a OTRA cuenta de Supabase
                return p
    return None


def actor_de_jwt(token: Optional[str]) -> Actor:
    """JWT → Actor con el rol de NUESTRA tabla. 401 si el token no vale; 403 si no hay perfil o está desactivado."""
    claims = verificar_jwt(token or "")
    sub, email = claims["sub"], claims.get("email")
    perfil = _buscar_perfil(sub, email if isinstance(email, str) else None)
    if perfil is None or not perfil.get("activo", True):
        tracing.emitir("auth", "jwt", "error", {"sub": sub[:8]}, {"motivo": "sin_perfil_o_inactivo"})
        raise ErrorAutorizacion("Su cuenta no tiene acceso a la administración.")
    if not perfil.get("supabase_uid"):                       # primer acceso: vincular por correo
        perfil = {**perfil, "supabase_uid": sub}
        db.actualizar("usuarios", perfil["usuario"], perfil)
        tracing.emitir("auth", "vincular_supabase", "ok", {"usuario": perfil["usuario"]})
    return Actor(usuario=perfil["usuario"], rol=Rol(perfil["rol"]), origen="supabase")
