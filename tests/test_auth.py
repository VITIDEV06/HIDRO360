"""Autenticación, autorización en el BACKEND (workflow y API) y portal público. Sin actor por defecto:
cada prueba que lo necesita anula el actor de administrador que pone conftest."""

import inspect
import json
import re
import sqlite3

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import config
from api.main import app
from models.schemas import AccionDecision
from data import database as db
from services import audit, auth, workflow
from services.auth import ErrorAutenticacion, ErrorAutorizacion, Rol
from services.workflow import ErrorDeNegocio

CLAVE = "Clave-Segura-1"


@pytest.fixture
def sin_actor():
    with auth.como(None):
        yield


@pytest.fixture
def como_usuario():
    with auth.como(auth.Actor("pepe", Rol.USUARIO)):
        yield


def _crear(usuario, rol=Rol.USUARIO, clave=CLAVE):
    auth.crear_usuario(usuario, clave, rol)


def _login(cliente, usuario, clave=CLAVE):
    r = cliente.post("/api/v1/auth/login", json={"usuario": usuario, "clave": clave})
    return r


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------------- contraseñas
def test_sin_usuarios_por_defecto():
    assert auth.hay_administradores() is False
    assert auth.listar_usuarios() == []
    _crear("pepe", Rol.USUARIO)
    assert auth.hay_administradores() is False        # un usuario común no cuenta
    _crear("jefa", Rol.ADMIN)
    assert auth.hay_administradores() is True


def test_contrasena_no_se_guarda_en_texto_plano():
    _crear("jefa", Rol.ADMIN)
    con = sqlite3.connect(str(config.DB_PATH))
    try:
        (datos,) = con.execute("SELECT datos FROM usuarios WHERE id = 'jefa'").fetchone()
    finally:
        con.close()
    doc = json.loads(datos)
    assert CLAVE not in datos and CLAVE.lower() not in datos.lower()
    assert doc["kdf"] == "scrypt" and len(doc["hash"]) == 64 and len(doc["sal"]) == 32
    assert CLAVE.encode() not in config.DB_PATH.read_bytes()   # tampoco en el archivo crudo


def test_misma_contrasena_produce_hash_distinto_por_sal():
    _crear("uno", Rol.USUARIO)
    _crear("dos", Rol.USUARIO)
    a, b = auth._doc("uno"), auth._doc("dos")
    assert a["sal"] != b["sal"] and a["hash"] != b["hash"]


def test_politica_de_contrasena():
    for mala in ("corta1A", "todominusculas1", "TODOMAYUSCULAS1", "SinNumeroAlguno"):
        with pytest.raises(ValueError):
            auth.crear_usuario("alguien", mala)
    with pytest.raises(ValueError):
        auth.crear_usuario("x", CLAVE)                  # usuario demasiado corto
    assert auth.listar_usuarios() == []


def test_login_correcto_e_incorrecto_con_mensaje_identico():
    _crear("jefa", Rol.ADMIN)
    assert auth.autenticar("jefa", CLAVE).rol == Rol.ADMIN
    msgs = []
    for u, c in (("jefa", "mala-clave-1A"), ("fantasma", "mala-clave-1A"), ("", "")):
        with pytest.raises(ErrorAutenticacion) as e:
            auth.autenticar(u, c)
        msgs.append(str(e.value))
    assert len(set(msgs)) == 1


def test_bloqueo_tras_fallos_repetidos():
    _crear("jefa", Rol.ADMIN)
    for _ in range(auth.MAX_FALLOS):
        with pytest.raises(ErrorAutenticacion):
            auth.autenticar("jefa", "mala-clave-1A")
    with pytest.raises(ErrorAutenticacion, match="Demasiados"):
        auth.autenticar("jefa", CLAVE)                  # incluso con la clave correcta
    auth.reiniciar_estado()
    assert auth.autenticar("jefa", CLAVE).usuario == "jefa"


def test_sesion_expira_y_logout_invalida():
    _crear("jefa", Rol.ADMIN)
    t1 = auth.iniciar_sesion("jefa", CLAVE)
    assert auth.actor_de_token(t1).usuario == "jefa"
    auth.cerrar_sesion(t1)
    assert auth.actor_de_token(t1) is None
    t2 = auth.iniciar_sesion("jefa", CLAVE)
    h = auth._huella(t2)
    doc = db.obtener("sesiones", h)
    assert doc["usuario"] == "jefa" and h != t2 and t2 not in json.dumps(doc)   # solo se guarda la huella
    db.actualizar("sesiones", h, {**doc, "expira": auth.time.time() - 1})        # ya venció
    assert auth.actor_de_token(t2) is None
    assert db.obtener("sesiones", h) is None                                     # y se purga al consultarla
    assert auth.actor_de_token("token-inventado") is None


def test_sesion_se_invalida_si_se_desactiva_o_cambia_de_rol_el_usuario():
    _crear("jefa", Rol.ADMIN)
    _crear("otra", Rol.ADMIN)
    t = auth.iniciar_sesion("jefa", CLAVE)
    d = auth._doc("jefa")
    db.actualizar("usuarios", "jefa", {**d, "rol": "usuario"})
    assert auth.actor_de_token(t).rol == Rol.USUARIO                  # el rol vigente es el de la tabla
    db.actualizar("usuarios", "jefa", {**d, "activo": False})
    assert auth.actor_de_token(t) is None                             # desactivada: la sesión ya no vale


# ------------------------------------------------------ autorización en la lógica de negocio
def _args_ficticios(fn):
    return [None] * len([p for p in inspect.signature(fn).parameters.values()
                         if p.default is inspect.Parameter.empty and p.kind == p.POSITIONAL_OR_KEYWORD])


FUNCIONES_ADMIN = ["corregir_y_confirmar", "rechazar_reporte", "resolver_duplicado", "generar_plan", "editar_plan",
                   "decidir_plan", "cargar_datos_demo", "listar_reportes", "obtener_reporte", "sugerir_duplicados",
                   "decisiones_duplicado", "calcular_prioridades", "visibilidad_sectores", "listar_planes",
                   "obtener_plan", "explicar_prioridad_ia", "redactar_borrador_ia",
                   "obtener_foto", "eliminar_foto", "listar_usuarios_admin", "crear_usuario_admin",
                   "desactivar_usuario", "reactivar_usuario", "cambiar_rol_usuario", "registrar_dispositivo", "rotar_token", "desactivar_dispositivo",
                   "listar_dispositivos", "crear_mision", "crear_mision_desde_plan", "listar_misiones",
                   "obtener_mision", "cancelar_mision", "listar_eventos", "datos_mapa",
                   "obtener_audio", "eliminar_audio"]
# Sin rol de usuario por diseño (ver comentarios en workflow.py)
FUNCIONES_ABIERTAS = {"listar_sectores", "listar_cisternas", "estado_actual", "registrar_confirmacion_dispositivo",
                      "crear_reporte", "complementar_reporte", "consultar_reporte_publico", "avisos_publicos",
                      "reiniciar_limites",
                      "operacion",
                      # terminales de vehículo: se autentican con su token (X-Device-Token), no con rol de usuario
                      "autenticar_dispositivo", "dispositivo_latido", "dispositivo_mision_activa",
                      "dispositivo_recibir_eventos"}


def test_ninguna_funcion_publica_de_workflow_queda_sin_clasificar():
    publicas = {n for n, f in inspect.getmembers(workflow, inspect.isfunction)
                if not n.startswith("_") and f.__module__ == workflow.__name__}
    assert publicas == set(FUNCIONES_ADMIN) | FUNCIONES_ABIERTAS


@pytest.mark.parametrize("nombre", FUNCIONES_ADMIN)
def test_funcion_administrativa_sin_actor_lanza_autenticacion(nombre, sin_actor):
    fn = getattr(workflow, nombre)
    with pytest.raises(ErrorAutenticacion):
        fn(*_args_ficticios(fn))


@pytest.mark.parametrize("nombre", FUNCIONES_ADMIN)
def test_funcion_administrativa_con_rol_usuario_lanza_autorizacion(nombre, como_usuario):
    fn = getattr(workflow, nombre)
    with pytest.raises(ErrorAutorizacion):
        fn(*_args_ficticios(fn))


def test_cargar_datos_demo_denegado_no_borra_nada(como_usuario):
    antes = len(workflow._listar_reportes())
    with pytest.raises(ErrorAutorizacion):
        workflow.cargar_datos_demo("x")
    assert len(workflow._listar_reportes()) == antes > 0


def test_responsable_auditado_es_el_usuario_autenticado():
    with auth.como(auth.Actor("jefa", Rol.ADMIN)):
        r = workflow.listar_reportes()[0]
        workflow.rechazar_reporte(r.id, "otra-persona", "prueba")      # el declarado se ignora
    assert audit.listar("reporte", r.id)[-1].responsable == "jefa"
    # actor de servicio: se respeta el declarado, marcado
    r2 = workflow.listar_reportes()[1]
    workflow.rechazar_reporte(r2.id, "ana", "prueba")
    assert audit.listar("reporte", r2.id)[-1].responsable == "servicio:ana"
    with pytest.raises(ErrorDeNegocio):
        workflow.rechazar_reporte(workflow.listar_reportes()[2].id, " ", "x")


# ------------------------------------------------------------------------ API: rutas
EXENTAS = {"/", "/api/v1/salud", "/api/v1/estado", "/api/v1/dispositivos/confirmaciones", "/api/v1/auth/login",
           # terminal de vehículo (token propio): su 401/403 se prueba en tests/test_flota.py
           "/api/v1/dispositivo/mision", "/api/v1/dispositivo/eventos", "/api/v1/dispositivo/latido",
           # webhook de Telegram: autenticado por su propio secreto (X-Telegram-Bot-Api-Secret-Token); ver test_telegram_webhook.py
           "/api/v1/telegram/webhook"}
SOLO_AUTENTICADAS = {"/api/v1/auth/logout", "/api/v1/auth/yo"}


def _rutas(admin: bool):
    salida = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api/v1"):
            continue
        if r.path in EXENTAS or r.path.startswith("/api/v1/publico/"):
            continue
        if admin == (r.path not in SOLO_AUTENTICADAS):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                salida.append((m, re.sub(r"\{[^}]+\}", "1", r.path)))
    return salida


def _llamar(c, metodo, ruta, **kw):
    return c.request(metodo, ruta, json={} if metodo in ("POST", "PUT") else None, **kw)


def test_el_listado_de_rutas_cubre_las_administrativas():
    r = _rutas(admin=True)
    assert len(r) >= 20
    assert ("POST", "/api/v1/planes/1/decision") in r and ("GET", "/api/v1/auditoria") in r
    assert ("GET", "/api/v1/trazas") in r and ("PUT", "/api/v1/planes/1/asignaciones") in r


@pytest.mark.parametrize("metodo,ruta", _rutas(admin=True) + _rutas(admin=False))
def test_endpoint_sin_credenciales_da_401(metodo, ruta, monkeypatch):
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")
    assert _llamar(TestClient(app), metodo, ruta).status_code == 401


@pytest.mark.parametrize("metodo,ruta", _rutas(admin=True))
def test_endpoint_sin_credenciales_da_401_aunque_no_haya_tokens_configurados(metodo, ruta):
    assert _llamar(TestClient(app), metodo, ruta).status_code == 401      # localhost no concede rol


@pytest.mark.parametrize("metodo,ruta", _rutas(admin=True))
def test_endpoint_admin_con_sesion_de_usuario_da_403(metodo, ruta):
    _crear("pepe", Rol.USUARIO)
    c = TestClient(app)
    token = _login(c, "pepe").json()["token"]
    assert _llamar(c, metodo, ruta, headers=_bearer(token)).status_code == 403


@pytest.mark.parametrize("metodo,ruta", _rutas(admin=True))
def test_token_de_dispositivo_no_sirve_en_endpoints_admin(metodo, ruta, monkeypatch):
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")
    monkeypatch.setenv("AGUA_CLARA_DEVICE_TOKEN", "dev-test")
    assert _llamar(TestClient(app), metodo, ruta, headers={"X-API-Token": "dev-test"}).status_code == 401


def test_sesion_admin_y_logout_por_api():
    _crear("jefa", Rol.ADMIN)
    c = TestClient(app)
    r = _login(c, "jefa")
    assert r.status_code == 200 and set(r.json()) == {"token", "rol", "expira_horas"} and r.json()["rol"] == "administrador"
    h = _bearer(r.json()["token"])
    assert c.get("/api/v1/reportes", headers=h).status_code == 200
    assert c.get("/api/v1/auth/yo", headers=h).json()["usuario"] == "jefa"
    assert c.post("/api/v1/auth/logout", headers=h).status_code == 200
    assert c.get("/api/v1/reportes", headers=h).status_code == 401
    assert c.get("/api/v1/auth/yo", headers=h).status_code == 401


def test_login_mensaje_generico_y_limite_por_ip():
    _crear("jefa", Rol.ADMIN)
    c = TestClient(app)
    a = _login(c, "jefa", "mala-clave-1A")
    b = _login(c, "no-existe", "mala-clave-1A")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    codigos = [_login(c, f"u{i}", "x").status_code for i in range(15)]
    assert 429 in codigos


def test_responsable_del_cuerpo_no_suplanta_al_usuario_autenticado():
    _crear("jefa", Rol.ADMIN)
    c = TestClient(app)
    h = _bearer(_login(c, "jefa").json()["token"])
    rid = workflow.listar_reportes()[0].id
    r = c.post(f"/api/v1/reportes/{rid}/rechazar", headers=h, json={"responsable": "otro", "motivo": "x"})
    assert r.status_code == 200
    assert audit.listar("reporte", rid)[-1].responsable == "jefa"
    rid2 = workflow.listar_reportes()[1].id
    r = c.post(f"/api/v1/reportes/{rid2}/rechazar", headers=h, json={"motivo": "x"})     # responsable opcional
    assert r.status_code == 200 and audit.listar("reporte", rid2)[-1].responsable == "jefa"


def test_ninguna_respuesta_de_la_api_expone_hash_ni_sal(monkeypatch):
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")
    _crear("jefa", Rol.ADMIN)
    doc = auth._doc("jefa")
    c = TestClient(app)
    login = _login(c, "jefa")
    textos = [login.text]
    h = {"X-API-Token": "op-test"}
    for ruta in ("/api/v1/auditoria", "/api/v1/trazas", "/api/v1/reportes", "/api/v1/planes", "/api/v1/auth/yo",
                 "/api/v1/estado", "/api/v1/cisternas", "/api/v1/salud"):
        textos.append(c.get(ruta, headers=h).text)
    junto = "\n".join(textos)
    assert doc["hash"] not in junto and doc["sal"] not in junto and CLAVE not in junto
    assert "scrypt" not in junto


def test_cors_no_esta_habilitado():
    r = TestClient(app).get("/api/v1/salud", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_errores_no_filtran_trazas(monkeypatch):
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")
    c = TestClient(app, headers={"X-API-Token": "op-test"})
    r = c.get("/api/v1/reportes/99999")
    assert r.status_code == 404 and "Traceback" not in r.text


# ------------------------------------------------------------------ portal público
PROHIBIDOS = ("mensaje_original", "motivo_rechazo", "responsable", "confirmado_por", "evidencia", "datos_faltantes",
              "datos_inciertos", "clave_idempotencia", "canal", "interprete", "duplicado_de", "personas_afectadas",
              "horas_sin_agua", "litros", "cisterna", "borrador", "CIS-")
SECRETO = "Vivo en la casa azul de Maria Perez, telefono 6000-1234, no hay agua en Capira hace 3 dias"


def test_portal_publico_crea_y_consulta_sin_credenciales_ni_filtraciones(monkeypatch):
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")        # aunque haya tokens, lo público no los pide
    c = TestClient(app)
    r = c.post("/api/v1/publico/reportes", json={"texto": SECRETO})
    assert r.status_code == 201
    assert set(r.json()) == {"codigo_seguimiento", "mensaje"}
    codigo = r.json()["codigo_seguimiento"]
    assert re.fullmatch(r"HID-[A-HJ-NP-Z2-9]{8}", codigo)
    g = c.get(f"/api/v1/publico/reportes/{codigo.lower()}")
    assert g.status_code == 200
    cuerpo = g.json()
    assert set(cuerpo) == {"codigo", "estado", "estado_publico", "atencion", "sector", "fecha_creacion",
                          "tiene_ubicacion", "n_fotos"}
    assert cuerpo["estado"] == "por_revisar" and cuerpo["atencion"] is None
    assert cuerpo["estado_publico"].startswith("Recibido") and cuerpo["sector"] is None
    for texto in (r.text, g.text):
        assert "Maria" not in texto and "6000" not in texto and "casa azul" not in texto
        for p in PROHIBIDOS:
            assert p not in texto
    assert "id" not in cuerpo and "id" not in r.json()
    guardado = next(x for x in workflow.listar_reportes() if x.codigo_seguimiento == codigo)
    assert guardado.mensaje_original == SECRETO and guardado.canal == "portal"
    assert audit.listar("reporte", guardado.id)[0].responsable == "ciudadano"


def test_portal_publico_no_acepta_campos_extra_ni_texto_vacio():
    c = TestClient(app)
    assert c.post("/api/v1/publico/reportes", json={"texto": ""}).status_code == 422
    assert c.post("/api/v1/publico/reportes", json={"texto": "x" * 4001}).status_code == 422
    assert c.post("/api/v1/publico/reportes", json={"texto": "sin agua en Chame", "estado": "confirmado"}).status_code == 422


def test_codigo_invalido_e_inexistente_dan_el_mismo_error():
    c = TestClient(app)
    a = c.get("/api/v1/publico/reportes/HID-ABCDEFGH")      # formato válido, no existe
    b = c.get("/api/v1/publico/reportes/cualquier-cosa")    # formato inválido
    d = c.get("/api/v1/publico/reportes/1")                 # un id interno no sirve como código
    assert a.status_code == b.status_code == d.status_code == 404
    assert a.json() == b.json() == d.json() == {"detail": "No existe un reporte con ese código."}


def test_consulta_de_codigos_limita_intentos():
    c = TestClient(app)
    codigos = [c.get(f"/api/v1/publico/reportes/HID-AAAAAAA{i}").status_code for i in range(30)]
    assert codigos[0] == 404 and 429 in codigos


def test_creacion_publica_limita_tasa():
    c = TestClient(app)
    codigos = [c.post("/api/v1/publico/reportes", json={"texto": f"sin agua en Chame numero {i}"}).status_code
               for i in range(14)]
    assert codigos[:10] == [201] * 10 and 429 in codigos


def test_codigos_unicos_y_estado_publico_sigue_al_reporte():
    c = TestClient(app)
    codigos = {c.post("/api/v1/publico/reportes", json={"texto": f"sin agua en Capira, caso {i}"}).json()["codigo_seguimiento"]
               for i in range(8)}
    assert len(codigos) == 8
    rep = next(x for x in workflow.listar_reportes() if x.codigo_seguimiento in codigos)
    workflow.rechazar_reporte(rep.id, "ana", "MOTIVO-INTERNO-SECRETO")
    g = c.get(f"/api/v1/publico/reportes/{rep.codigo_seguimiento}")
    assert g.json()["estado_publico"] == "No procede como reporte."
    assert "MOTIVO-INTERNO-SECRETO" not in g.text and "ana" not in json.dumps(g.json())


def test_avisos_publicos_sin_datos_operativos():
    for r in workflow.listar_reportes():
        if r.sector and r.personas_afectadas is not None:
            workflow.corregir_y_confirmar(r.id, "t", r.sector, r.horas_sin_agua, r.personas_afectadas,
                                          r.severidad.value if r.severidad else None, r.calle)
    c = TestClient(app)
    antes = c.get("/api/v1/publico/avisos")
    assert antes.status_code == 200
    assert all(a["tipo"] == "en_evaluacion" for a in antes.json()) and antes.json()
    plan = workflow.generar_plan("t")
    workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "t", "")
    r = c.get("/api/v1/publico/avisos")
    avisos = r.json()
    assert any(a["tipo"] == "atencion_programada" for a in avisos)
    for a in avisos:
        assert set(a) <= {"tipo", "sector", "mensaje", "reportes_en_evaluacion"}
    assert any("hora aún sin confirmar" in a["mensaje"] for a in avisos)
    for p in PROHIBIDOS:
        assert p not in r.text
    for x in workflow.listar_reportes():
        assert x.mensaje_original not in r.text


def test_reporte_antiguo_sin_codigo_sigue_leyendose():
    from data import database as db
    rid = db.insertar("reportes", {"mensaje_original": "sin agua en Chame", "fecha_creacion": "2026-01-01T10:00:00",
                                   "estado": "por_revisar", "id": 0})
    assert workflow.obtener_reporte(rid).codigo_seguimiento is None


def test_ciudadano_puede_reportar_sin_actor_pero_no_administrar(sin_actor):
    r = workflow.crear_reporte("2 días sin agua en Chame", origen="1.2.3.4")
    assert r.codigo_seguimiento
    with pytest.raises(ErrorAutenticacion):
        workflow.obtener_reporte(r.id)
    with pytest.raises(ErrorAutenticacion):
        workflow.listar_reportes()


# ------------------------------------------------------------ gestión de usuarios (administración)
ADMIN_SVC = {"X-API-Token": "op-test"}


@pytest.fixture
def api_admin(monkeypatch):
    monkeypatch.setenv("HIDRO360_API_TOKEN", "op-test")
    return TestClient(app, headers=ADMIN_SVC)


def test_usuarios_exigen_credenciales_y_rol_admin():
    c = TestClient(app)
    _crear("pepe", Rol.USUARIO)
    h = _bearer(_login(c, "pepe").json()["token"])
    for m, ruta, cuerpo in (("GET", "/api/v1/usuarios", None), ("POST", "/api/v1/usuarios", {}),
                            ("POST", "/api/v1/usuarios/pepe/desactivar", {}),
                            ("POST", "/api/v1/usuarios/pepe/reactivar", {}),
                            ("POST", "/api/v1/usuarios/pepe/rol", {"rol": "administrador"})):
        assert c.request(m, ruta, json=cuerpo).status_code == 401
        assert c.request(m, ruta, json=cuerpo, headers=h).status_code == 403
    # un usuario no puede auto-ascenderse
    assert auth._doc("pepe")["rol"] == "usuario"


def test_ciclo_de_vida_de_usuarios_por_api(api_admin):
    c = api_admin
    r = c.post("/api/v1/usuarios", json={"usuario": "ana", "rol": "administrador", "clave": CLAVE})
    assert r.status_code == 201 and r.json()["usuario"] == "ana" and r.json()["activo"] is True
    assert "hash" not in r.text and "sal" not in r.text
    assert c.post("/api/v1/usuarios", json={"usuario": "ana", "rol": "usuario", "clave": CLAVE}).status_code == 422   # no reemplaza
    assert c.post("/api/v1/usuarios", json={"usuario": "luis", "rol": "usuario", "clave": "corta"}).status_code == 422
    assert c.post("/api/v1/usuarios", json={"usuario": "luis", "rol": "dios", "clave": CLAVE}).status_code == 422
    assert c.post("/api/v1/usuarios", json={"usuario": "luis", "rol": "usuario"}).status_code == 422              # sin clave
    assert [u["usuario"] for u in c.get("/api/v1/usuarios").json()] == ["ana"]
    # desactivar: la sesión abierta deja de valer al instante
    cliente = TestClient(app)
    tok = _login(cliente, "ana").json()["token"]
    assert cliente.get("/api/v1/auth/yo", headers=_bearer(tok)).status_code == 200
    c.post("/api/v1/usuarios", json={"usuario": "beto", "rol": "administrador", "clave": CLAVE})
    assert c.post("/api/v1/usuarios/ana/desactivar", json={}).status_code == 200
    assert cliente.get("/api/v1/auth/yo", headers=_bearer(tok)).status_code == 401
    assert _login(cliente, "ana").status_code == 401
    assert c.post("/api/v1/usuarios/ana/desactivar", json={}).status_code == 409          # ya desactivado
    assert c.post("/api/v1/usuarios/ana/reactivar", json={}).status_code == 200
    assert _login(cliente, "ana").status_code == 200
    assert c.post("/api/v1/usuarios/nadie/desactivar", json={}).status_code == 404
    # cambio de rol auditado
    assert c.post("/api/v1/usuarios/ana/rol", json={"rol": "usuario"}).json()["rol"] == "usuario"
    acciones = [e.accion for e in audit.listar("usuario")]
    assert {"crear_usuario", "desactivar_usuario", "reactivar_usuario", "cambiar_rol_usuario"} <= set(acciones)


def test_no_se_puede_dejar_el_sistema_sin_administradores(api_admin):
    c = api_admin
    c.post("/api/v1/usuarios", json={"usuario": "ana", "rol": "administrador", "clave": CLAVE})
    assert c.post("/api/v1/usuarios/ana/desactivar", json={}).status_code == 422
    assert c.post("/api/v1/usuarios/ana/rol", json={"rol": "usuario"}).status_code == 422
    assert auth._doc("ana")["activo"] is True and auth._doc("ana")["rol"] == "administrador"
    c.post("/api/v1/usuarios", json={"usuario": "beto", "rol": "administrador", "clave": CLAVE})
    assert c.post("/api/v1/usuarios/ana/rol", json={"rol": "usuario"}).status_code == 200      # ahora hay otro
    assert c.post("/api/v1/usuarios/beto/desactivar", json={}).status_code == 422               # beto es el último
    # un admin tampoco puede quitarse el rol a sí mismo si es el último
    cliente = TestClient(app)
    h = _bearer(_login(cliente, "beto").json()["token"])
    assert cliente.post("/api/v1/usuarios/beto/rol", json={"rol": "usuario"}, headers=h).status_code == 422


def test_responsable_de_usuarios_es_el_autenticado(api_admin):
    c = api_admin
    c.post("/api/v1/usuarios", json={"usuario": "ana", "rol": "administrador", "clave": CLAVE})
    cliente = TestClient(app)
    h = _bearer(_login(cliente, "ana").json()["token"])
    r = cliente.post("/api/v1/usuarios", headers=h, json={"usuario": "luis", "rol": "usuario", "clave": CLAVE,
                                                         "responsable": "otro"})
    assert r.status_code == 201
    assert audit.listar("usuario", "luis")[-1].responsable == "ana"


def test_consulta_publica_expone_estado_y_atencion_solo_en_plan_sin_datos_operativos():
    from models.schemas import Parada
    c = TestClient(app)
    codigo = c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame hace 2 dias, 40 personas"}).json()["codigo_seguimiento"]
    rep = next(x for x in workflow.listar_reportes() if x.codigo_seguimiento == codigo)
    workflow.corregir_y_confirmar(rep.id, "t", "Chame", 48, 40, "alta", None)
    g = c.get(f"/api/v1/publico/reportes/{codigo}").json()
    assert g["estado"] == "confirmado" and g["atencion"] is None
    # forzamos el estado en_plan y una misión con parada para este reporte (sin pasar por el planificador)
    d = db.obtener("reportes", rep.id)
    db.actualizar("reportes", rep.id, {**d, "estado": "en_plan"})
    assert c.get(f"/api/v1/publico/reportes/{codigo}").json()["atencion"] == {"mision_asignada": False, "entrega_registrada": False}
    m = workflow.crear_mision("CIS-001", [Parada(orden=1, sector="Chame", litros=500, reporte_ids=[rep.id])], "t")
    assert c.get(f"/api/v1/publico/reportes/{codigo}").json()["atencion"] == {"mision_asignada": True, "entrega_registrada": False}
    md = db.obtener("misiones", m.id)
    md["paradas"][0]["estado"] = "entregada"
    db.actualizar("misiones", m.id, md)
    r = c.get(f"/api/v1/publico/reportes/{codigo}")
    assert r.json()["atencion"] == {"mision_asignada": True, "entrega_registrada": True}
    for prohibido in ("CIS-", "litros", "500", "lat", "lng", "horas"):
        assert prohibido not in json.dumps(r.json()["atencion"]) and prohibido not in r.text.replace("tiene_ubicacion", "")


def test_cargar_datos_demo_usa_siempre_reglas_locales_sin_llamar_a_la_ia(monkeypatch):
    from services import ingest
    llamadas = []

    def prohibido(self, *a, **k):
        llamadas.append(1)
        raise AssertionError("cargar_datos_demo no debe invocar a la IA")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa-de-prueba")
    monkeypatch.setattr(ingest.InterpreteGemini, "extraer", prohibido)
    n = workflow.cargar_datos_demo("t")["reportes"]
    assert n > 10 and llamadas == []
    assert {r.interprete for r in workflow.listar_reportes()} == {"reglas"}
    # control negativo: fuera de la carga sí se usa el proveedor configurado (y cae al fallback visible)
    r = workflow.crear_reporte("Sin agua en Chame control negativo")
    assert llamadas == [1] and r.interprete == "reglas(fallback)"
    assert ingest.solo_local.get() is False
