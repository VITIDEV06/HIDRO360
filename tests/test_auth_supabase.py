"""
Autenticación con Supabase Auth (HIDRO360_AUTH=supabase): verificación de JWT en el backend.

ETIQUETA: SIMULADO. Las claves EC/RSA se generan en la prueba y el JWKS lo sirve un httpx.MockTransport.
NO se ha probado contra un proyecto Supabase real (no hay credenciales en el desarrollo).
"""

import base64
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

import config
from api.main import app
from data import database as db
from services import audit, auth, auth_supabase, workflow
from services.auth import ErrorAutenticacion, ErrorAutorizacion, Rol

URL = "https://proyecto-prueba.supabase.co"
ISS = URL + "/auth/v1"
UID = "11111111-1111-1111-1111-111111111111"
EMAIL = "ana@example.com"


class JWKS:
    """Servidor de JWKS simulado: cuenta las peticiones y permite rotar claves."""

    def __init__(self):
        self.claves = {}
        self.peticiones = 0
        self.estado = 200

    def anadir(self, kid, pub, alg):
        jwk = json.loads(ECAlgorithm.to_jwk(pub) if alg == "ES256" else RSAAlgorithm.to_jwk(pub))
        self.claves[kid] = {**jwk, "kid": kid, "alg": alg, "use": "sig"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/.well-known/jwks.json" and request.url.host.endswith("supabase.co")
        self.peticiones += 1
        if self.estado != 200:
            return httpx.Response(self.estado)
        return httpx.Response(200, json={"keys": list(self.claves.values())})


@pytest.fixture
def jwks(monkeypatch):
    monkeypatch.setenv("HIDRO360_AUTH", "supabase")
    monkeypatch.setenv("SUPABASE_URL", URL)
    srv = JWKS()
    monkeypatch.setattr(auth_supabase, "_transport", httpx.MockTransport(srv))
    auth_supabase.reiniciar_cache()
    yield srv
    auth_supabase.reiniciar_cache()


@pytest.fixture
def ec_key(jwks):
    priv = ec.generate_private_key(ec.SECP256R1())
    jwks.anadir("k-ec-1", priv.public_key(), "ES256")
    return priv


def _claims(**extra):
    ahora = int(time.time())
    base = {"sub": UID, "email": EMAIL, "aud": "authenticated", "iss": ISS, "role": "authenticated",
            "iat": ahora, "exp": ahora + 3600}
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


def _jwt(priv, kid="k-ec-1", alg="ES256", **extra):
    return jwt.encode(_claims(**extra), priv, algorithm=alg, headers={"kid": kid})


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _perfil(rol=Rol.ADMIN, uid=None):
    auth_supabase.crear_perfil(EMAIL, rol, uid)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- token válido
def test_token_valido_asimetrico_da_el_actor_con_el_rol_de_nuestra_tabla(ec_key):
    _perfil(Rol.ADMIN)
    a = auth_supabase.actor_de_jwt(_jwt(ec_key))
    assert (a.usuario, a.rol, a.origen) == (EMAIL, Rol.ADMIN, "supabase")


def test_primer_acceso_vincula_el_uid_y_despues_manda_el_uid(ec_key):
    _perfil(Rol.ADMIN)
    assert auth_supabase._buscar_perfil(UID, None) is None
    auth_supabase.actor_de_jwt(_jwt(ec_key))
    assert db.obtener("usuarios", EMAIL)["supabase_uid"] == UID
    # otro sub con el mismo correo: el correo ya está vinculado a OTRA cuenta de Supabase
    with pytest.raises(ErrorAutorizacion):
        auth_supabase.actor_de_jwt(_jwt(ec_key, sub="22222222-2222-2222-2222-222222222222"))
    # cambia el correo en Supabase pero el sub es el mismo: sigue entrando
    assert auth_supabase.actor_de_jwt(_jwt(ec_key, email="otro@example.com")).usuario == EMAIL


def test_token_valido_rsa_y_hs256_legado(jwks, monkeypatch):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks.anadir("k-rsa", priv.public_key(), "RS256")
    _perfil(Rol.ADMIN)
    assert auth_supabase.actor_de_jwt(_jwt(priv, "k-rsa", "RS256")).rol == Rol.ADMIN
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "secreto-legado-de-prueba-0123456789abcdef")
    legado = jwt.encode(_claims(), "secreto-legado-de-prueba-0123456789abcdef", algorithm="HS256")
    assert auth_supabase.actor_de_jwt(legado).usuario == EMAIL


# --------------------------------------------------------------------------- tokens inválidos
@pytest.mark.parametrize("extra", [
    {"exp": int(time.time()) - 3600},                                   # expirado
    {"nbf": int(time.time()) + 3600},                                   # todavía no válido
    {"aud": "otro"},                                                    # audiencia errónea
    {"iss": "https://atacante.supabase.co/auth/v1"},                    # emisor erróneo
    {"aud": None}, {"exp": None}, {"sub": None},                        # claims obligatorios ausentes
    {"role": "service_role"}, {"role": "anon"},                         # no son personas
])
def test_claims_invalidos_se_rechazan(ec_key, extra):
    _perfil(Rol.ADMIN)
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(_jwt(ec_key, **extra))


def test_firma_de_otra_clave_se_rechaza(ec_key):
    _perfil(Rol.ADMIN)
    otra = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(_jwt(otra))                          # mismo kid, firma ajena


def test_alg_none_se_rechaza(ec_key):
    _perfil(Rol.ADMIN)
    sin_firma = _b64({"alg": "none", "typ": "JWT", "kid": "k-ec-1"}) + "." + _b64(_claims()) + "."
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(sin_firma)


def test_confusion_de_algoritmos_se_rechaza(jwks, ec_key, monkeypatch):
    """Firmar con HS256 usando como secreto la clave PÚBLICA (o un secreto cualquiera) no debe valer."""
    _perfil(Rol.ADMIN)
    pub_pem = ec_key.public_key().public_bytes(
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).Encoding.PEM,
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).PublicFormat.SubjectPublicKeyInfo)
    falso = jwt.encode(_claims(), "x" * 40, algorithm="HS256", headers={"kid": "k-ec-1"})
    with pytest.raises(ErrorAutenticacion):                             # HS256 sin secreto configurado
        auth_supabase.actor_de_jwt(falso)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "secreto-real-de-prueba-0123456789abcdef")
    with pytest.raises(ErrorAutenticacion):                             # HS256 con otro secreto
        auth_supabase.actor_de_jwt(falso)
    # el kid del JWKS no se usa nunca para verificar un token HS256 ni al revés
    cuerpo = _b64({"alg": "HS256", "typ": "JWT", "kid": "k-ec-1"}) + "." + _b64(_claims())
    import hashlib
    import hmac
    firma = base64.urlsafe_b64encode(hmac.new(pub_pem, cuerpo.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(cuerpo + "." + firma)
    for alg in ("HS384", "PS256", "ES512"):                             # algoritmos fuera de la lista blanca
        cab = _b64({"alg": alg, "typ": "JWT", "kid": "k-ec-1"})
        with pytest.raises(ErrorAutenticacion):
            auth_supabase.actor_de_jwt(f"{cab}.{_b64(_claims())}.AAAA")


@pytest.mark.parametrize("basura", ["", "abc", "a.b", "a.b.c.d", "x" * 9000, "e30.e30.e30"])
def test_basura_se_rechaza(ec_key, basura):
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(basura)


def test_kid_desconocido_o_ausente(ec_key):
    _perfil(Rol.ADMIN)
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(_jwt(ec_key, kid="no-existe"))
    sin_kid = jwt.encode(_claims(), ec_key, algorithm="ES256")
    with pytest.raises(ErrorAutenticacion):
        auth_supabase.actor_de_jwt(sin_kid)


# --------------------------------------------------------------------------- autorización local
def test_usuario_sin_perfil_o_desactivado_recibe_403(ec_key):
    with pytest.raises(ErrorAutorizacion):                              # sin perfil
        auth_supabase.actor_de_jwt(_jwt(ec_key))
    _perfil(Rol.ADMIN, UID)
    t = _jwt(ec_key)
    assert auth_supabase.actor_de_jwt(t).rol == Rol.ADMIN
    d = db.obtener("usuarios", EMAIL)
    db.actualizar("usuarios", EMAIL, {**d, "activo": False})            # revocación inmediata con el JWT vigente
    with pytest.raises(ErrorAutorizacion):
        auth_supabase.actor_de_jwt(t)


def test_no_se_puede_elevar_el_rol_con_claims_editables(ec_key):
    _perfil(Rol.USUARIO)
    t = _jwt(ec_key, user_metadata={"rol": "administrador", "role": "admin"},
             app_metadata={"rol": "administrador", "role": "administrador"})
    assert auth_supabase.actor_de_jwt(t).rol == Rol.USUARIO


# --------------------------------------------------------------------------- JWKS: caché y rotación
def test_jwks_se_cachea_y_se_recarga_ante_un_kid_nuevo(jwks, ec_key):
    _perfil(Rol.ADMIN, UID)
    for _ in range(5):
        auth_supabase.actor_de_jwt(_jwt(ec_key))
    assert jwks.peticiones == 1
    nueva = ec.generate_private_key(ec.SECP256R1())                     # rotación en Supabase
    jwks.anadir("k-ec-2", nueva.public_key(), "ES256")
    assert auth_supabase.actor_de_jwt(_jwt(nueva, "k-ec-2")).usuario == EMAIL
    assert jwks.peticiones == 2
    # tokens con kid inventado no provocan una avalancha: enfriamiento entre recargas forzadas
    for i in range(5):
        with pytest.raises(ErrorAutenticacion):
            auth_supabase.actor_de_jwt(_jwt(nueva, f"falso-{i}"))
    assert jwks.peticiones == 2


def test_si_el_jwks_cae_se_usa_la_cache_vieja_o_se_falla_cerrado(jwks, ec_key):
    _perfil(Rol.ADMIN)
    jwks.estado = 500
    with pytest.raises(ErrorAutenticacion):                             # sin caché: falla cerrado
        auth_supabase.actor_de_jwt(_jwt(ec_key))
    jwks.estado = 200
    auth_supabase.actor_de_jwt(_jwt(ec_key))
    jwks.estado = 500
    auth_supabase._jwks_cache["cargado"] = 0                             # caché vencida y servidor caído
    assert auth_supabase.actor_de_jwt(_jwt(ec_key)).usuario == EMAIL


# --------------------------------------------------------------------------- API
def test_api_acepta_jwt_y_rechaza_sesiones_locales_y_login(ec_key, monkeypatch):
    _perfil(Rol.ADMIN, UID)
    c = TestClient(app)
    t = _jwt(ec_key)
    r = c.get("/api/v1/auth/yo", headers=_h(t))
    assert r.status_code == 200 and r.json() == {"usuario": EMAIL, "rol": "administrador", "origen": "supabase"}
    assert c.get("/api/v1/reportes", headers=_h(t)).status_code == 200
    assert c.post("/api/v1/auth/login", json={"usuario": "x", "clave": "y"}).status_code == 410
    # una sesión local (creada en modo local) no vale como JWT
    monkeypatch.setenv("HIDRO360_AUTH", "local")
    auth.crear_usuario("jefa", "Clave-Segura-1", Rol.ADMIN)
    local = auth.iniciar_sesion("jefa", "Clave-Segura-1")
    monkeypatch.setenv("HIDRO360_AUTH", "supabase")
    assert c.get("/api/v1/reportes", headers=_h(local)).status_code == 401
    assert c.get("/api/v1/reportes").status_code == 401
    assert c.get("/api/v1/reportes", headers=_h("a.b.c")).status_code == 401


def test_api_403_sin_perfil_o_con_rol_usuario_y_admin_ve_usuarios(ec_key):
    c = TestClient(app)
    assert c.get("/api/v1/reportes", headers=_h(_jwt(ec_key))).status_code == 403            # sin perfil
    _perfil(Rol.USUARIO, UID)
    assert c.get("/api/v1/reportes", headers=_h(_jwt(ec_key))).status_code == 403            # rol usuario
    d = db.obtener("usuarios", EMAIL)
    db.actualizar("usuarios", EMAIL, {**d, "rol": "administrador"})
    r = c.get("/api/v1/usuarios", headers=_h(_jwt(ec_key)))
    assert r.status_code == 200 and r.json()[0]["usuario"] == EMAIL and r.json()[0]["vinculado"] is True


def test_el_token_de_servicio_sigue_valiendo_en_modo_supabase(ec_key, monkeypatch):
    monkeypatch.setenv("HIDRO360_API_TOKEN", "op-test")
    assert TestClient(app).get("/api/v1/reportes", headers={"X-API-Token": "op-test"}).status_code == 200


def test_la_auditoria_registra_al_administrador_de_supabase(ec_key):
    _perfil(Rol.ADMIN, UID)
    c = TestClient(app)
    rid = workflow.listar_reportes()[0].id
    r = c.post(f"/api/v1/reportes/{rid}/rechazar", headers=_h(_jwt(ec_key)), json={"motivo": "prueba", "responsable": "falso"})
    assert r.status_code == 200 and audit.listar("reporte", rid)[-1].responsable == EMAIL


def test_alta_de_perfiles_por_workflow_en_modo_supabase(ec_key):
    u = workflow.crear_usuario_admin("Nuevo@Example.com", "administrador")
    assert u["usuario"] == "nuevo@example.com" and u["origen"] == "supabase" and u["vinculado"] is False
    with pytest.raises(workflow.ErrorDeNegocio):
        workflow.crear_usuario_admin("nuevo@example.com", "administrador")           # duplicado
    with pytest.raises(workflow.ErrorDeNegocio):
        workflow.crear_usuario_admin("sin-arroba", "administrador")                   # no es un correo
    with pytest.raises(workflow.ErrorDeNegocio):
        workflow.crear_usuario_admin("otro@example.com", "administrador", "Clave-1234-x")   # la clave vive en Supabase


def test_script_crear_admin_vincula_perfil_supabase(monkeypatch, capsys):
    from scripts import crear_admin
    monkeypatch.setenv("HIDRO360_AUTH", "supabase")
    monkeypatch.setattr("sys.argv", ["crear_admin", "--supabase-email", "Jefa@Example.com"])
    assert crear_admin.main() == 0
    d = db.obtener("usuarios", "jefa@example.com")
    assert d["rol"] == "administrador" and d["activo"] is True and "hash" not in d
    monkeypatch.setattr("sys.argv", ["crear_admin", "--supabase-email", "jefa@example.com"])
    assert crear_admin.main() == 1                                                     # no reemplaza
    assert "Ya existe" in capsys.readouterr().out
