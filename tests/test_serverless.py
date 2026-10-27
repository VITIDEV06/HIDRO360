"""
Ejecución SIN ESTADO LOCAL (funciones serverless / varias instancias) y endurecimiento de la API.

Una «instancia» nueva se simula vaciando TODO el estado de módulo (cachés, ContextVars) entre llamadas: lo que debe
sobrevivir (sesiones, bloqueos, límites de tasa, idempotencia) tiene que vivir en la base. Probado en SQLite local;
el mismo contrato (registrar_evento_limite, sesiones, etc.) lo implementa el motor PostgreSQL, NO probado aquí
contra un Supabase real.
"""

import inspect
import json

import pytest
from fastapi.testclient import TestClient

import config
from api import main as api_main
from api.main import app
from data import database as db
from services import auth, auth_supabase, fotos, limites, tracing, workflow
from services.auth import Rol

CLAVE = "Clave-Segura-1"
ADMIN = {"X-API-Token": "op-test"}


def nueva_instancia():
    """Simula el arranque en frío de otra función serverless: descarta cachés y contexto del proceso anterior."""
    auth_supabase.reiniciar_cache()
    auth.actor_actual.set(None)
    tracing._run_id.set(None)
    tracing._correlacion.set(None)
    return TestClient(app)


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("HIDRO360_API_TOKEN", "op-test")


# ---------------------------------------------------------------- no queda estado de negocio en memoria
MODULOS = [auth, auth_supabase, limites, workflow, api_main, fotos, tracing]
PERMITIDOS = {"auth_supabase._jwks_cache"}      # JWKS: clave pública, inocua (se vuelve a pedir si falta)


def test_ningun_modulo_guarda_estado_mutable_de_negocio():
    """Cualquier dict/list/set de módulo que no sea una CONSTANTE (nombre en mayúsculas) es estado que se rompería
    entre instancias: debe vivir en la base."""
    sospechosos = []
    for m in MODULOS:
        for nombre, valor in vars(m).items():
            if nombre.startswith("__") or not isinstance(valor, (dict, list, set, bytearray)):
                continue
            if nombre.lstrip("_").isupper() or f"{m.__name__.split('.')[-1]}.{nombre}" in PERMITIDOS:
                continue
            sospechosos.append(f"{m.__name__}.{nombre}")
    assert sospechosos == []


def test_las_constantes_de_tasa_no_son_contadores():
    fuente = inspect.getsource(api_main) + inspect.getsource(workflow) + inspect.getsource(auth)
    for malo in ("time.monotonic()", "_intentos", "_login_intentos", "_disp_fallos", "_sesiones: Dict"):
        assert malo not in fuente


# ---------------------------------------------------------------- sesiones
def test_la_sesion_creada_en_una_instancia_vale_en_otra():
    auth.crear_usuario("jefa", CLAVE, Rol.ADMIN)
    c1 = nueva_instancia()
    token = c1.post("/api/v1/auth/login", json={"usuario": "jefa", "clave": CLAVE}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    c2 = nueva_instancia()
    assert c2.get("/api/v1/auth/yo", headers=h).json()["usuario"] == "jefa"
    c3 = nueva_instancia()
    assert c3.post("/api/v1/auth/logout", headers=h).status_code == 200
    c4 = nueva_instancia()
    assert c4.get("/api/v1/auth/yo", headers=h).status_code == 401           # el cierre de sesión también es global
    # en la base solo hay huellas, no el token
    assert token not in json.dumps(db.listar("sesiones"))


# ---------------------------------------------------------------- bloqueos y límites
def test_el_bloqueo_de_login_sobrevive_al_cambio_de_instancia():
    auth.crear_usuario("jefa", CLAVE, Rol.ADMIN)
    for _ in range(auth.MAX_FALLOS):
        c = nueva_instancia()
        assert c.post("/api/v1/auth/login", json={"usuario": "jefa", "clave": "mala-clave-1A"}).status_code == 401
    c = nueva_instancia()
    r = c.post("/api/v1/auth/login", json={"usuario": "jefa", "clave": CLAVE})      # clave correcta, pero bloqueado
    assert r.status_code == 401 and "Demasiados" in r.json()["detail"]
    # el mensaje no revela si el usuario existe
    r2 = nueva_instancia().post("/api/v1/auth/login", json={"usuario": "nadie", "clave": "x"})
    r3 = nueva_instancia().post("/api/v1/auth/login", json={"usuario": "jefa2", "clave": "x"})
    assert r2.json() == r3.json()


def test_limite_de_reportes_publicos_sobrevive_al_cambio_de_instancia():
    codigos = []
    for i in range(13):
        c = nueva_instancia()
        codigos.append(c.post("/api/v1/publico/reportes", json={"texto": f"sin agua en Chame, caso {i}"}).status_code)
    assert codigos[:10] == [201] * 10 and codigos[10:] == [429] * 3


def test_limite_de_consultas_de_codigo_y_de_login_por_origen():
    cods = [nueva_instancia().get(f"/api/v1/publico/reportes/HID-AAAAAAA{i % 8 + 2}").status_code for i in range(24)]
    assert cods[:20] == [404] * 20 and 429 in cods[20:]
    logins = [nueva_instancia().post("/api/v1/auth/login", json={"usuario": f"u{i}", "clave": "x"}).status_code
              for i in range(13)]
    assert logins[:10] == [401] * 10 and 429 in logins[10:]


def test_bloqueo_de_terminales_por_fallos_de_token_sobrevive_al_cambio_de_instancia():
    codigos = [nueva_instancia().get("/api/v1/dispositivo/mision", headers={"X-Device-Token": f"mal{i}"}).status_code
               for i in range(13)]
    assert codigos[:10] == [401] * 10 and codigos[10:] == [429] * 3
    # un token VÁLIDO desde el origen bloqueado también espera (bloqueo suave por origen), y otro origen no se ve afectado
    t = nueva_instancia().post("/api/v1/dispositivos", json={"cisterna_id": "CIS-001"}, headers=ADMIN).json()["token"]
    assert nueva_instancia().get("/api/v1/dispositivo/mision", headers={"X-Device-Token": t}).status_code == 429


def test_credenciales_admin_invalidas_acaban_en_429_pero_las_validas_nunca_se_bloquean():
    for i in range(api_main._CRED_FALLOS_MAX + 3):
        r = nueva_instancia().get("/api/v1/reportes", headers={"X-API-Token": f"malo{i}"})
    assert r.status_code == 429
    assert nueva_instancia().get("/api/v1/reportes", headers=ADMIN).status_code == 200


def test_limites_viejos_se_limpian(monkeypatch):
    db.registrar_evento_limite("x", 60)
    assert db.limpiar_limites(0) >= 1


# ---------------------------------------------------------------- idempotencia de eventos de terminal
def test_eventos_de_terminal_son_idempotentes_entre_instancias():
    c = nueva_instancia()
    token = c.post("/api/v1/dispositivos", json={"cisterna_id": "CIS-001"}, headers=ADMIN).json()["token"]
    m = c.post("/api/v1/misiones", headers=ADMIN, json={"cisterna_id": "CIS-001", "paradas": [
        {"orden": 1, "sector": "Chame", "litros": 500}]}).json()
    ev = {"uid": "evt-serverless-01", "tipo": "entrega", "mision_id": m["id"], "parada_orden": 1, "litros_entregados": 500}
    h = {"X-Device-Token": token}
    r1 = nueva_instancia().post("/api/v1/dispositivo/eventos", json={"eventos": [ev]}, headers=h)
    r2 = nueva_instancia().post("/api/v1/dispositivo/eventos", json={"eventos": [ev]}, headers=h)
    assert [x["resultado"] for x in r1.json()["resultados"]] == ["aplicado"]
    assert [x["resultado"] for x in r2.json()["resultados"]] == ["duplicado"]
    entregas = [e for e in db.listar("eventos_mision") if e.get("uid") == ev["uid"]]
    assert len(entregas) == 1


def test_idempotencia_de_reportes_por_clave_entre_instancias():
    cuerpo = {"texto": "Sin agua en Capira desde ayer", "clave_idempotencia": "k-serverless-1"}
    a = nueva_instancia().post("/api/v1/reportes", json=cuerpo, headers=ADMIN).json()
    b = nueva_instancia().post("/api/v1/reportes", json=cuerpo, headers=ADMIN).json()
    assert a["id"] == b["id"]


# ---------------------------------------------------------------- origen del cliente y proxies
def _req(client_host, **cabeceras):
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(k.replace("_", "-").encode(), v.encode())
                                                                       for k, v in cabeceras.items()],
             "client": (client_host, 1234)}
    return Request(scope)


def test_cabeceras_de_proxy_se_ignoran_fuera_de_produccion_o_sin_proxy_declarado(monkeypatch):
    r = _req("10.0.0.5", x_forwarded_for="1.2.3.4", x_real_ip="1.2.3.4")
    assert api_main._ip(r) == "10.0.0.5"                                   # dev/test: no confiar
    monkeypatch.setenv("HIDRO360_ENV", "prod")
    assert api_main._ip(r) == "10.0.0.5"                                   # prod sin proxy declarado: no confiar
    monkeypatch.setenv("HIDRO360_TRUST_PROXY", "1")
    assert api_main._ip(r) == "1.2.3.4"                                    # prod + proxy de confianza
    # un cliente que intenta falsificar: la entrada confiable es la que añadió el último proxy (derecha)
    falso = _req("10.0.0.5", x_forwarded_for="9.9.9.9, 1.2.3.4")
    assert api_main._ip(falso) == "1.2.3.4"
    monkeypatch.setenv("HIDRO360_PROXY_SALTOS", "2")
    assert api_main._ip(falso) == "9.9.9.9"
    monkeypatch.setenv("HIDRO360_PROXY_SALTOS", "1")
    basura = _req("10.0.0.5", x_forwarded_for="no-es-ip", x_real_ip="tampoco")
    assert api_main._ip(basura) == "10.0.0.5"
    monkeypatch.delenv("HIDRO360_TRUST_PROXY")
    monkeypatch.setenv("VERCEL", "1")
    assert api_main._ip(r) == "1.2.3.4"                                    # Vercel declara su proxy


def test_falsificar_x_forwarded_for_no_evade_el_limite_fuera_de_proxy():
    c = TestClient(app)
    codigos = [c.post("/api/v1/publico/reportes", json={"texto": f"sin agua en Chame {i}"},
                      headers={"X-Forwarded-For": f"8.8.8.{i}"}).status_code for i in range(13)]
    assert codigos[10:] == [429] * 3


# ---------------------------------------------------------------- API endurecida
def test_cabeceras_de_seguridad_y_salud_sin_secretos(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_NO_DEBE_SALIR_0123456789")
    r = TestClient(app).get("/api/v1/salud")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    j = r.json()
    assert j["persistencia"] == "sqlite" and j["autenticacion"] == "local" and j["almacenamiento_fotos"] == "local"
    assert "sb_secret" not in r.text and "supabase.co" not in r.text and "postgres" not in r.text.replace("sqlite", "")
    monkeypatch.setenv("HIDRO360_ENV", "prod")
    assert "strict-transport-security" in TestClient(app).get("/api/v1/salud").headers


def test_foto_conserva_su_csp_propia():
    r = TestClient(app).get("/api/v1/reportes/1/fotos/x", headers=ADMIN)
    assert r.status_code in (404, 422)                                       # no existe; la ruta responde con las cabeceras globales
    assert r.headers["x-content-type-options"] == "nosniff"


def test_cors_por_lista_blanca_sin_comodin(monkeypatch):
    c = TestClient(app)
    assert "access-control-allow-origin" not in c.get("/api/v1/salud", headers={"Origin": "https://panel.example"}).headers
    monkeypatch.setenv("HIDRO360_CORS_ORIGINS", "https://panel.example, *")
    ok = c.get("/api/v1/salud", headers={"Origin": "https://panel.example"})
    assert ok.headers["access-control-allow-origin"] == "https://panel.example" and ok.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in ok.headers
    mal = c.get("/api/v1/salud", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in mal.headers
    pre = c.options("/api/v1/reportes", headers={"Origin": "https://panel.example",
                                                  "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "https://panel.example"
    assert "Authorization" in pre.headers["access-control-allow-headers"]
    pre_mal = c.options("/api/v1/reportes", headers={"Origin": "https://evil.example",
                                                      "Access-Control-Request-Method": "POST"})
    assert pre_mal.status_code == 403 and "access-control-allow-origin" not in pre_mal.headers
    # el comodín «*» se ignora aunque se configure
    monkeypatch.setenv("HIDRO360_CORS_ORIGINS", "*")
    assert "access-control-allow-origin" not in c.get("/api/v1/salud", headers={"Origin": "https://panel.example"}).headers


def test_cuerpo_demasiado_grande_con_y_sin_content_length(monkeypatch):
    monkeypatch.setattr(api_main, "MAX_CUERPO_BYTES", 50_000)
    c = TestClient(app)
    grande = {"texto": "x" * 60_000}
    assert c.post("/api/v1/publico/reportes", json=grande).status_code == 413          # con Content-Length

    def trozos():                                                                      # sin Content-Length (chunked)
        for _ in range(8):
            yield b"x" * 10_000
    r = c.post("/api/v1/publico/reportes", content=trozos(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == {"detail": "El envío es demasiado grande."}
    r = c.post("/api/v1/publico/reportes", content=b"x" * 10, headers={"Content-Type": "application/json",
                                                                       "Content-Length": "999999"})
    assert r.status_code in (400, 413)                                                 # Content-Length mentiroso
    assert c.post("/api/v1/publico/reportes", json={"texto": "sin agua en Chame"}).status_code == 201


def test_errores_de_validacion_no_devuelven_los_valores_enviados():
    r = TestClient(app).post("/api/v1/auth/login", json={"usuario": "", "clave": "MiClaveSecreta-123"})
    assert r.status_code == 422 and "MiClaveSecreta-123" not in r.text
    assert isinstance(r.json()["detail"], list) and set(r.json()["detail"][0]) == {"loc", "msg", "type"}
    r = TestClient(app).post("/api/v1/auth/login", json={"clave": "MiClaveSecreta-123"})
    assert "MiClaveSecreta-123" not in r.text


def test_error_interno_es_uniforme_y_opaco(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("detalle interno postgresql://u:p@host/db")
    monkeypatch.setattr(workflow, "avisos_publicos", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/publico/avisos")
    assert r.status_code == 500 and r.json() == {"detail": "Error interno."}


def test_protocolo_de_terminal_en_mision_y_latido():
    c = TestClient(app)
    t = c.post("/api/v1/dispositivos", json={"cisterna_id": "CIS-001"}, headers=ADMIN).json()["token"]
    h = {"X-Device-Token": t}
    m = c.get("/api/v1/dispositivo/mision", headers=h)
    assert m.status_code == 200 and m.json()["protocolo"] == api_main.PROTOCOLO_TERMINAL == 1
    l = c.post("/api/v1/dispositivo/latido", json={"firmware": "t-1"}, headers=h)
    assert l.json()["protocolo"] == 1 and l.json()["ok"] is True


# ---------------------------------------------------------------- trazas sin disco
def test_trazas_a_stdout_sin_cadena_y_sin_secretos(monkeypatch, capsys):
    antes = config.TRACE_PATH.stat().st_size if config.TRACE_PATH.exists() else 0   # (la carga de datos de la fixture ya trazó)
    monkeypatch.setenv("HIDRO360_TRACE", "stdout")
    ev = tracing.emitir("prueba", "accion", "ok", {"token": "abc", "dato": "postgresql://u:clave123@h/db"})
    salida = capsys.readouterr().out.strip().splitlines()
    assert len(salida) == 1
    linea = json.loads(salida[0])
    assert linea["agent"] == "prueba" and linea["cadena"] is False and "hash" not in linea
    assert linea["input_summary"]["token"] == "***" and "clave123" not in salida[0]
    assert ev["cadena"] is False
    assert (config.TRACE_PATH.stat().st_size if config.TRACE_PATH.exists() else 0) == antes   # nada escrito en disco
    integ = tracing.verificar_integridad()
    assert integ["modo"] == "stdout" and tracing.leer() == []
    # la auditoría de negocio mantiene su cadena aunque las trazas no la tengan
    from services import audit
    workflow.rechazar_reporte(workflow.listar_reportes()[0].id, "t", "prueba")
    assert audit.verificar_integridad() is True


def test_trazas_en_vercel_o_con_disco_de_solo_lectura_van_a_stdout(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    tracing.emitir("a", "b")
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["agent"] == "a"
    monkeypatch.delenv("VERCEL")
    (tmp_path / "archivo").write_text("no es una carpeta")                     # escribir debajo falla con OSError
    monkeypatch.setattr(config, "TRACE_PATH", tmp_path / "archivo" / "trazas.jsonl")
    tracing.emitir("c", "d")                                                   # no lanza: degrada a stdout
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["agent"] == "c"


def test_trazas_en_archivo_siguen_con_cadena(capsys):
    tracing.emitir("a", "b")
    tracing.emitir("a", "c")
    assert tracing.verificar_integridad()["integra"] is True and len(tracing.leer()) >= 2
    assert capsys.readouterr().out == ""
