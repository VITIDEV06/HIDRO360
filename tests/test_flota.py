"""Flota: dispositivos por vehículo, misiones, eventos idempotentes y mapa (integración con TestClient)."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from api.main import app
from data import database as db
from models.schemas import AccionDecision
from services import audit, auth, workflow
from services.auth import ErrorAutenticacion, ErrorAutorizacion, Rol
from services.workflow import ErrorDeNegocio

ADMIN = {"X-API-Token": "op-test"}


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")


@pytest.fixture
def c():
    return TestClient(app)


def _registrar(c, cis="CIS-001"):
    r = c.post("/api/v1/dispositivos", json={"cisterna_id": cis, "nombre": f"Camión {cis}"}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _th(token):
    return {"X-Device-Token": token}


def _mision(c, cis="CIS-001", paradas=None):
    paradas = paradas or [{"orden": 1, "sector": "Chame", "litros": 500},
                          {"orden": 2, "sector": "Bejuco", "litros": 700}]
    r = c.post("/api/v1/misiones", json={"cisterna_id": cis, "paradas": paradas, "motivo": "prueba"}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def _ev(uid, mision, orden, tipo, litros=None):
    e = {"uid": uid, "tipo": tipo, "mision_id": mision, "parada_orden": orden}
    if litros is not None:
        e["litros_entregados"] = litros
    return e


def _enviar(c, token, *eventos):
    r = c.post("/api/v1/dispositivo/eventos", json={"eventos": list(eventos)}, headers=_th(token))
    assert r.status_code == 200, r.text
    return [x["resultado"] for x in r.json()["resultados"]]


def _litros(cis):
    return db.obtener("cisternas", cis)["litros_disponibles"]


# ------------------------------------------------------------------ dispositivos
def test_token_se_muestra_una_vez_y_solo_se_guarda_el_hash(c):
    token = _registrar(c)
    assert len(token) >= 43                                     # 256 bits en base64url
    doc = db.obtener("dispositivos", "CIS-001")
    assert doc["token_hash"] == hashlib.sha256(token.encode()).hexdigest() and token not in str(doc)
    for ruta in ("/api/v1/dispositivos", "/api/v1/auditoria", "/api/v1/trazas"):
        t = c.get(ruta, headers=ADMIN).text
        assert token not in t and doc["token_hash"] not in t and "token_hash" not in t
    assert c.post("/api/v1/dispositivos", json={"cisterna_id": "CIS-001"}, headers=ADMIN).status_code == 409  # ya existe
    assert c.post("/api/v1/dispositivos", json={"cisterna_id": "CIS-999"}, headers=ADMIN).status_code == 404


def test_rotar_token_invalida_el_anterior_y_desactivar_bloquea(c):
    t1 = _registrar(c)
    assert c.get("/api/v1/dispositivo/mision", headers=_th(t1)).status_code == 200
    r = c.post("/api/v1/dispositivos/CIS-001/rotar-token", headers=ADMIN)
    t2 = r.json()["token"]
    assert t2 != t1
    assert c.get("/api/v1/dispositivo/mision", headers=_th(t1)).status_code == 401
    assert c.get("/api/v1/dispositivo/mision", headers=_th(t2)).status_code == 200
    d = c.post("/api/v1/dispositivos/CIS-001/desactivar", headers=ADMIN)
    assert d.status_code == 200 and d.json()["activo"] is False
    assert c.get("/api/v1/dispositivo/mision", headers=_th(t2)).status_code == 401


def test_terminal_sin_token_o_con_token_ajeno_da_401(c, monkeypatch):
    _registrar(c)
    assert c.get("/api/v1/dispositivo/mision").status_code == 401
    assert c.get("/api/v1/dispositivo/mision", headers=_th("inventado")).status_code == 401
    assert c.post("/api/v1/dispositivo/eventos", json={"eventos": []}).status_code == 401
    assert c.post("/api/v1/dispositivo/latido", json={}).status_code == 401
    # ni el token de servicio, ni la sesión admin, ni el token compartido antiguo sirven como terminal
    monkeypatch.setenv("AGUA_CLARA_DEVICE_TOKEN", "viejo")
    assert c.get("/api/v1/dispositivo/mision", headers={"X-Device-Token": "op-test"}).status_code == 401
    assert c.get("/api/v1/dispositivo/mision", headers={"X-Device-Token": "viejo"}).status_code == 401
    assert c.get("/api/v1/dispositivo/mision", headers=ADMIN).status_code == 401


def test_token_de_terminal_no_sirve_en_endpoints_admin(c):
    t = _registrar(c)
    for ruta in ("/api/v1/misiones", "/api/v1/eventos", "/api/v1/mapa", "/api/v1/dispositivos", "/api/v1/reportes"):
        assert c.get(ruta, headers=_th(t)).status_code == 401
        assert c.get(ruta, headers={"X-API-Token": t}).status_code == 401


def test_bloqueo_suave_tras_intentos_fallidos(c):
    codigos = [c.get("/api/v1/dispositivo/mision", headers=_th(f"mal{i}")).status_code for i in range(14)]
    assert codigos[0] == 401 and 429 in codigos


def test_el_token_compartido_antiguo_sigue_sirviendo_en_rutas_antiguas(monkeypatch, c):
    monkeypatch.setenv("AGUA_CLARA_DEVICE_TOKEN", "viejo")
    assert c.get("/api/v1/estado", headers={"X-API-Token": "viejo"}).status_code == 200


# ------------------------------------------------------------------- misiones
def test_crear_mision_valida_reglas(c):
    m = _mision(c)
    assert m["estado"] == "asignada" and [p["estado"] for p in m["paradas"]] == ["pendiente", "pendiente"]
    assert m["paradas"][0]["lat"] is not None                    # tomadas del sector
    assert audit.listar("mision", m["id"])[0].accion == "crear_mision"

    def malo(**kw):
        cuerpo = {"cisterna_id": "CIS-002", "paradas": [{"orden": 1, "sector": "Chame", "litros": 100}]}
        cuerpo.update(kw)
        return c.post("/api/v1/misiones", json=cuerpo, headers=ADMIN)
    assert c.post("/api/v1/misiones", json={"cisterna_id": "CIS-001", "paradas": [
        {"orden": 1, "sector": "Chame", "litros": 10}]}, headers=ADMIN).status_code == 409     # ya hay una activa
    assert malo(cisterna_id="CIS-999").status_code == 404
    assert malo(cisterna_id="CIS-005").status_code == 422                                      # en mantenimiento
    assert malo(paradas=[{"orden": 1, "sector": "Chame", "litros": 99999}]).status_code == 422   # excede lo disponible
    assert malo(paradas=[{"orden": 1, "sector": "Chame", "litros": 0}]).status_code == 422
    assert malo(paradas=[{"orden": 1, "sector": "Narnia", "litros": 10}]).status_code == 422
    assert malo(paradas=[{"orden": 1, "sector": "Chame", "litros": 10},
                         {"orden": 1, "sector": "Capira", "litros": 10}]).status_code == 422   # órdenes repetidos
    assert malo(paradas=[{"orden": 1, "sector": "Chame", "litros": 10, "lat": 50, "lng": 10}]).status_code == 422
    assert malo(plan_id=999).status_code == 404
    assert malo(recomendacion_id=999).status_code == 404


def test_cancelar_exige_motivo_y_libera_la_cisterna(c):
    m = _mision(c)
    assert c.post(f"/api/v1/misiones/{m['id']}/cancelar", json={"motivo": ""}, headers=ADMIN).status_code == 422
    r = c.post(f"/api/v1/misiones/{m['id']}/cancelar", json={"motivo": "avería"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["estado"] == "cancelada"
    assert c.post(f"/api/v1/misiones/{m['id']}/cancelar", json={"motivo": "otra vez"}, headers=ADMIN).status_code == 409
    _mision(c)                                                   # ahora sí se puede otra
    assert [x["estado"] for x in c.get("/api/v1/misiones?estado=cancelada", headers=ADMIN).json()] == ["cancelada"]
    assert c.get("/api/v1/misiones?estado=raro", headers=ADMIN).status_code == 422


def test_mision_desde_plan_aprobado():
    for r in workflow.listar_reportes():
        if r.sector and r.personas_afectadas is not None:
            workflow.corregir_y_confirmar(r.id, "t", r.sector, r.horas_sin_agua, r.personas_afectadas,
                                          r.severidad.value if r.severidad else None, r.calle)
    plan = workflow.generar_plan("t")
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_mision_desde_plan(plan.id, plan.asignaciones[0].cisterna_id, "t")     # aún propuesto
    workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "t", "")
    cis = plan.asignaciones[0].cisterna_id
    m = workflow.crear_mision_desde_plan(plan.id, cis, "t")
    esperado = {a.sector for a in plan.asignaciones if a.cisterna_id == cis}
    assert {p.sector for p in m.paradas} == esperado and m.plan_id == plan.id
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_mision_desde_plan(plan.id, "CIS-999", "t")
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_mision_desde_plan(plan.id, cis, "t")                                   # ya convertida


# --------------------------------------------------------------------- terminal
def test_terminal_solo_ve_su_propia_mision(c):
    ta, tb = _registrar(c, "CIS-001"), _registrar(c, "CIS-002")
    ma = _mision(c, "CIS-001")
    a = c.get("/api/v1/dispositivo/mision", headers=_th(ta)).json()
    b = c.get("/api/v1/dispositivo/mision", headers=_th(tb)).json()
    assert a["cisterna_id"] == "CIS-001" and a["mision"]["id"] == ma["id"]
    assert a["mision"]["siguiente_tarea"]["parada_orden"] == 1 and a["mision"]["siguiente_tarea"]["accion"] == "ir_a"
    assert a["servidor_hora"].endswith("+00:00")
    assert b["cisterna_id"] == "CIS-002" and b["mision"] is None
    # B no puede operar la misión de A, ni aunque diga serlo en el cuerpo
    r = c.post("/api/v1/dispositivo/eventos", headers=_th(tb), json={"eventos": [
        {**_ev("evt-0000001", ma["id"], 1, "llegada"), "cisterna_id": "CIS-001"}]})
    assert r.json()["resultados"][0]["resultado"] == "rechazado"
    assert db.obtener("misiones", ma["id"])["estado"] == "asignada"


def test_eventos_idempotentes_y_descuento_de_litros(c):
    t = _registrar(c)
    m = _mision(c)
    base = _litros("CIS-001")
    assert _enviar(c, t, _ev("evt-00000001", m["id"], 1, "llegada")) == ["aplicado"]
    assert db.obtener("misiones", m["id"])["estado"] == "en_curso"
    lote = [_ev("evt-00000002", m["id"], 1, "entrega", 500)]
    assert _enviar(c, t, *lote) == ["aplicado"] and _litros("CIS-001") == base - 500
    # reenvío (la terminal no recibió la respuesta): no duplica ni descuenta de nuevo
    assert _enviar(c, t, *lote, _ev("evt-00000001", m["id"], 1, "llegada")) == ["duplicado", "duplicado"]
    assert _litros("CIS-001") == base - 500
    # mismo uid repetido dentro del mismo lote
    assert _enviar(c, t, _ev("evt-00000003", m["id"], 2, "entrega", 700),
                   _ev("evt-00000003", m["id"], 2, "entrega", 700)) == ["aplicado", "duplicado"]
    assert _litros("CIS-001") == base - 1200
    final = c.get(f"/api/v1/misiones/{m['id']}", headers=ADMIN).json()
    assert final["estado"] == "completada" and [p["estado"] for p in final["paradas"]] == ["entregada", "entregada"]
    assert final["paradas"][0]["litros_entregados"] == 500
    assert c.get("/api/v1/dispositivo/mision", headers=_th(t)).json()["mision"] is None   # ya no hay activa
    # visibles al panel admin; auditados como el dispositivo
    ev = c.get(f"/api/v1/eventos?mision_id={m['id']}", headers=ADMIN).json()
    assert [e["uid"] for e in ev] == ["evt-00000001", "evt-00000002", "evt-00000003"]
    assert all(e["cisterna_id"] == "CIS-001" and e["resultado"] == "aplicado" for e in ev)
    assert c.get("/api/v1/eventos?cisterna_id=CIS-002", headers=ADMIN).json() == []
    resp = {e.responsable for e in audit.listar("mision", m["id"])}
    assert "dispositivo:CIS-001" in resp
    assert audit.verificar_integridad()


def test_eventos_rechazados(c):
    t = _registrar(c)
    m = _mision(c)
    base = _litros("CIS-001")
    res = _enviar(c, t,
                  _ev("evt-00000001", m["id"], 9, "llegada"),                 # parada inexistente
                  _ev("evt-00000002", 99999, 1, "llegada"),                   # misión inexistente
                  _ev("evt-00000003", m["id"], 1, "entrega", 501),            # más de lo planificado
                  _ev("evt-00000004", m["id"], 1, "entrega", 0),              # cero litros
                  {"uid": "corto", "tipo": "x"},                              # formato inválido
                  _ev("evt-00000005", m["id"], 1, "entrega", 500),            # válido (sin llegada previa)
                  _ev("evt-00000006", m["id"], 1, "llegada"))                 # transición ilegal tras entregar
    assert res == ["rechazado"] * 5 + ["aplicado", "rechazado"]
    assert _litros("CIS-001") == base - 500
    # el rechazado quedó registrado (visible al administrador) con su motivo
    regs = {e["uid"]: e for e in c.get("/api/v1/eventos", headers=ADMIN).json()}
    assert regs["evt-00000003"]["resultado"] == "rechazado" and "RECHAZADO" in regs["evt-00000003"]["nota"]
    # un uid usado por otro vehículo no se puede reutilizar
    t2 = _registrar(c, "CIS-002")
    assert _enviar(c, t2, _ev("evt-00000005", m["id"], 1, "entrega", 1)) == ["rechazado"]
    # el lote tiene tope
    r = c.post("/api/v1/dispositivo/eventos", headers=_th(t), json={"eventos": [{}] * 51})
    assert r.status_code == 422


def test_litros_nunca_bajan_de_cero_y_mision_cancelada_rechaza_eventos(c):
    t = _registrar(c)
    m = _mision(c, paradas=[{"orden": 1, "sector": "Chame", "litros": 4000}])
    c.post(f"/api/v1/misiones/{m['id']}/cancelar", json={"motivo": "cambio"}, headers=ADMIN)
    assert _enviar(c, t, _ev("evt-00000001", m["id"], 1, "entrega", 100)) == ["rechazado"]
    # litros disponibles bajaron por otra vía antes de entregar: se descuenta como máximo lo que hay
    m2 = _mision(c, paradas=[{"orden": 1, "sector": "Chame", "litros": 1000}])
    cis = db.obtener("cisternas", "CIS-001")
    cis["litros_disponibles"] = 300
    db.actualizar("cisternas", "CIS-001", cis)
    assert _enviar(c, t, _ev("evt-00000002", m2["id"], 1, "entrega", 1000)) == ["aplicado"]
    assert _litros("CIS-001") == 0


def test_incidencia_cierra_la_parada_y_completa(c):
    t = _registrar(c)
    m = _mision(c, paradas=[{"orden": 1, "sector": "Chame", "litros": 100}])
    assert _enviar(c, t, _ev("evt-00000001", m["id"], 1, "incidencia")) == ["aplicado"]
    assert db.obtener("misiones", m["id"])["estado"] == "completada"
    assert _litros("CIS-001") == 4000


def test_latido_actualiza_estado_de_la_terminal(c):
    t = _registrar(c)
    r = c.post("/api/v1/dispositivo/latido", headers=_th(t),
               json={"firmware": "0.3.1", "wifi_rssi": -61, "pendientes": 2, "uptime_s": 120})
    assert r.status_code == 200
    d = next(x for x in c.get("/api/v1/dispositivos", headers=ADMIN).json() if x["cisterna_id"] == "CIS-001")
    assert d["ultima_conexion"] and d["ultimo_estado"]["wifi_rssi"] == -61 and d["ultimo_estado"]["pendientes"] == 2
    assert c.post("/api/v1/dispositivo/latido", headers=_th(t), json={"wifi_rssi": 50}).status_code == 422


def test_mapa_con_datos_reales_sin_inventar(c):
    c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame con mapa", "lat": 8.6, "lng": -79.9})
    c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Capira sin mapa"})
    t = _registrar(c)
    m = _mision(c)
    mapa = c.get("/api/v1/mapa", headers=ADMIN).json()
    assert [r["lat"] for r in mapa["reportes"]] == [8.6]                      # solo reportes con coordenadas
    cis = {x["id"]: x for x in mapa["cisternas"]}
    assert cis["CIS-001"]["fuente_ubicacion"] == "base_sector" and cis["CIS-001"]["lat"] == 8.880
    assert len(mapa["paradas"]) == 2 and {p["mision_id"] for p in mapa["paradas"]} == {m["id"]}
    _enviar(c, t, _ev("evt-00000001", m["id"], 1, "llegada"))
    cis = {x["id"]: x for x in c.get("/api/v1/mapa", headers=ADMIN).json()["cisternas"]}
    assert cis["CIS-001"]["fuente_ubicacion"] == "ultima_parada" and cis["CIS-001"]["lat"] == 8.595   # Chame


def test_persistencia_de_flota_tras_reabrir_conexiones(c):
    t = _registrar(c)
    m = _mision(c)
    _enviar(c, t, _ev("evt-00000001", m["id"], 1, "llegada"))
    assert [d["cisterna_id"] for d in db.listar("dispositivos")] == ["CIS-001"]
    assert db.obtener("misiones", m["id"])["estado"] == "en_curso"
    assert len(db.listar("eventos_mision")) == 1
    otro = TestClient(app)                                   # cliente nuevo: el estado vive en la base, no en memoria
    assert otro.get("/api/v1/dispositivo/mision", headers=_th(t)).json()["mision"]["estado"] == "en_curso"


def test_recargar_datos_demo_no_borra_dispositivos(c):
    _registrar(c)
    workflow.cargar_datos_demo("t")
    assert [d["cisterna_id"] for d in db.listar("dispositivos")] == ["CIS-001"]
    assert db.listar("misiones") == []


# ------------------------------------------------- 401/403 de las funciones y rutas nuevas
NUEVAS_ADMIN = [("GET", "/api/v1/dispositivos"), ("POST", "/api/v1/dispositivos"),
                ("POST", "/api/v1/dispositivos/CIS-001/rotar-token"), ("POST", "/api/v1/dispositivos/CIS-001/desactivar"),
                ("GET", "/api/v1/misiones"), ("POST", "/api/v1/misiones"), ("GET", "/api/v1/misiones/1"),
                ("POST", "/api/v1/misiones/1/cancelar"), ("POST", "/api/v1/planes/1/misiones"),
                ("GET", "/api/v1/eventos"), ("GET", "/api/v1/mapa"), ("GET", "/api/v1/reportes/1/fotos/abc")]


@pytest.mark.parametrize("metodo,ruta", NUEVAS_ADMIN)
def test_rutas_nuevas_401_sin_credenciales_y_403_con_rol_usuario(metodo, ruta, c):
    cuerpo = {} if metodo == "POST" else None
    assert c.request(metodo, ruta, json=cuerpo).status_code == 401
    auth.crear_usuario("pepe", "Clave12345x", Rol.USUARIO)
    tk = c.post("/api/v1/auth/login", json={"usuario": "pepe", "clave": "Clave12345x"}).json()["token"]
    assert c.request(metodo, ruta, json=cuerpo, headers={"Authorization": f"Bearer {tk}"}).status_code == 403


def test_funciones_de_terminal_rechazan_dispositivo_inexistente_o_inactivo():
    with auth.como(None):
        assert workflow.autenticar_dispositivo("nada") is None
        with pytest.raises(ErrorDeNegocio):
            workflow.dispositivo_latido("CIS-001")
        with pytest.raises(ErrorDeNegocio):
            workflow.dispositivo_recibir_eventos("CIS-001", [])
    with pytest.raises(ErrorAutenticacion):
        with auth.como(None):
            workflow.registrar_dispositivo("CIS-001", "x")
    with auth.como(auth.Actor("pepe", Rol.USUARIO)):
        with pytest.raises(ErrorAutorizacion):
            workflow.crear_mision("CIS-001", [], "pepe")
