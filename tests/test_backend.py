import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import config
from api.main import app
from models.schemas import Cisterna, EstadoReporte, PrioridadSector
from services import audit, planner, tracing, workflow
from services.workflow import ErrorDeNegocio


def _confirmar(r, **kw):
    return workflow.corregir_y_confirmar(r.id, "t", kw.get("sector", r.sector), r.horas_sin_agua,
                                         r.personas_afectadas, r.severidad.value if r.severidad else None, r.calle,
                                         **{k: v for k, v in kw.items() if k != "sector"})


# ------------------------------------------------------------- estados e idempotencia
def test_maquina_de_estados_del_reporte():
    r = workflow.crear_reporte("2 días sin agua en Chame, 30 personas", "t")
    workflow.rechazar_reporte(r.id, "t", "prueba")
    with pytest.raises(ErrorDeNegocio, match="Transición"):
        _confirmar(workflow.obtener_reporte(r.id))
    with pytest.raises(ErrorDeNegocio, match="Transición"):
        workflow.rechazar_reporte(r.id, "t", "otra vez")


def test_responsable_obligatorio_en_operaciones_sensibles():
    r = workflow.crear_reporte("2 días sin agua en Chame, 30 personas", "t")
    with pytest.raises(ErrorDeNegocio):
        workflow.corregir_y_confirmar(r.id, "  ", "Chame", 48, 30, "alta", None)
    with pytest.raises(ErrorDeNegocio):
        workflow.rechazar_reporte(r.id, "", "x")


def test_idempotencia_por_clave_y_por_texto_reciente():
    a = workflow.crear_reporte("2 días sin agua en Chame", "t", clave_idempotencia="k1")
    assert workflow.crear_reporte("otro texto distinto en Chame", "t", clave_idempotencia="k1").id == a.id
    b = workflow.crear_reporte("3 días sin agua en Capira", "t")
    assert workflow.crear_reporte("3 días sin agua en Capira", "t").id == b.id  # doble clic
    assert workflow.crear_reporte("3 días sin agua en Veracruz", "t").id != b.id
    assert len([r for r in workflow.listar_reportes() if "Capira" in r.mensaje_original and not r.ficticio]) == 1


def test_mensaje_original_inalterado_tras_correcciones():
    texto = "Llevamos 3 días sin agua en La Chorrera"
    r = workflow.crear_reporte(texto, "t")
    workflow.corregir_y_confirmar(r.id, "t", "Capira", 10, 99, "baja", None)
    assert workflow.obtener_reporte(r.id).mensaje_original == texto


def test_auditoria_y_cambio_de_negocio_son_atomicos(monkeypatch):
    r = workflow.crear_reporte("2 días sin agua en Chame, 30 personas", "t")
    n = len(audit.listar())

    def falla(*a, **k):
        raise RuntimeError("disco lleno")

    with monkeypatch.context() as m:
        m.setattr(audit, "registrar", falla)
        with pytest.raises(RuntimeError):
            workflow.corregir_y_confirmar(r.id, "t", "Chame", 48, 30, "alta", None)
    assert workflow.obtener_reporte(r.id).estado == EstadoReporte.POR_REVISAR  # se revirtió
    assert len(audit.listar()) == n


# ------------------------------------------------------------------------ trazas
def test_trazas_jsonl_integridad_y_privacidad():
    texto = "Sin agua desde ayer en Vista Alegre, telefono 6000-1234 llamar a Maria Perez"
    workflow.crear_reporte(texto, "t")
    eventos = tracing.leer()
    assert {"ingesta", "workflow"} <= {e["agent"] for e in eventos}
    for e in eventos:
        assert {"run_id", "timestamp", "agent", "action", "status", "duration_ms", "correlation_id"} <= set(e)
    crudo = config.TRACE_PATH.read_text(encoding="utf-8")
    assert "Maria Perez" not in crudo and "6000-1234" not in crudo
    assert tracing.verificar_integridad()["integra"]
    lineas = crudo.splitlines()
    lineas[1] = lineas[1].replace('"ok"', '"error"', 1)
    config.TRACE_PATH.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    assert not tracing.verificar_integridad()["integra"]


def test_trazas_filtros_y_correlacion():
    r = workflow.crear_reporte("2 días sin agua en Chame, 30 personas", "t")
    assert tracing.leer(correlation_id=f"reporte:{r.id}")
    assert not tracing.leer(status="error")


def test_trazas_redactan_claves():
    tracing.emitir("x", "y", input_summary={"api_key": "SECRETO", "n": 1})
    assert "SECRETO" not in config.TRACE_PATH.read_text(encoding="utf-8")


# ------------------------------------------------------------------- planificador
def _prio(sector, puntaje, personas):
    return PrioridadSector(sector=sector, puntaje=puntaje, categoria="X", factores=[], reportes_ids=[1],
                           reportes_contados=1, personas_afectadas=personas, razones=[])


def test_flujo_supera_al_voraz_con_elegibilidad():
    # A (prioridad alta) puede usar X o Y; B solo X. El voraz da a A la cisterna con más litros (X)
    # y deja a B sin agua aunque Y alcanzaba para A.
    cist = [Cisterna(id="X", capacidad_litros=3000, litros_disponibles=3000, sectores_elegibles=["A", "B"]),
            Cisterna(id="Y", capacidad_litros=2000, litros_disponibles=2000, sectores_elegibles=["A"])]
    pri = [_prio("A", 90, 200), _prio("B", 50, 200)]  # 3000 L cada uno
    a_f, _ = planner.proponer_asignaciones(pri, cist, metodo="flujo")
    a_g, _ = planner.proponer_asignaciones(pri, cist, metodo="greedy")

    def cub(asig, s):
        return sum(a.litros for a in asig if a.sector == s)

    assert cub(a_f, "B") > cub(a_g, "B")
    assert sum(a.litros for a in a_f) > sum(a.litros for a in a_g)
    assert planner.validar_asignaciones(a_f, cist) == []


def _demo_confirmada():
    workflow.cargar_datos_demo("t")
    for r in workflow.listar_reportes():
        if r.sector:
            _confirmar(r)


def test_planificador_determinista_y_respeta_limites():
    _demo_confirmada()
    p1 = workflow.generar_plan("t")
    _demo_confirmada()
    p2 = workflow.generar_plan("t")

    def f(p):
        return [(a.sector, a.cisterna_id, a.litros) for a in p.asignaciones]

    assert f(p1) == f(p2) and p1.pendientes and p1.metodo == "flujo_maximo_lexicografico"
    cis = {c.id: c for c in workflow.listar_cisternas()}
    for cid in {a.cisterna_id for a in p1.asignaciones}:
        total = sum(a.litros for a in p1.asignaciones if a.cisterna_id == cid)
        assert total <= min(cis[cid].litros_disponibles, cis[cid].capacidad_litros)
    for a in p1.asignaciones:  # camión pequeño solo a sectores elegibles
        if a.cisterna_id == "CIS-006":
            assert a.sector in cis["CIS-006"].sectores_elegibles
        assert cis[a.cisterna_id].estado.value == "disponible"


def test_cisterna_no_puede_declarar_mas_litros_que_su_capacidad():
    with pytest.raises(ValueError):
        Cisterna(id="Z", capacidad_litros=1000, litros_disponibles=2000)


# ---------------------------------------------------------- prioridad y visibilidad
def test_servicio_esencial_confirmado_eleva_vulnerabilidad():
    a = workflow.crear_reporte("2 días sin agua en Chame, 30 personas", "t")
    b = workflow.crear_reporte("2 días sin agua en La Chorrera, 30 personas", "t")  # ambos sector 'media'
    _confirmar(a)
    _confirmar(b, servicio_esencial="centro de salud")
    pr = {p.sector: p for p in workflow.calcular_prioridades()}
    assert pr["La Chorrera"].puntaje > pr["Chame"].puntaje
    assert "servicio esencial" in pr["La Chorrera"].factores[2].explicacion


def test_visibilidad_no_confunde_ausencia_con_necesidad():
    vis = {v["sector"]: v for v in workflow.visibilidad_sectores()}
    assert vis["Bejuco"]["estado"] == "con_reportes_por_revisar"
    # sector sin reportes: jamás aparece con puntaje ni como "sin necesidad"
    lejos = {v["sector"]: v for v in workflow.visibilidad_sectores(datetime.now() + timedelta(days=30))}
    assert all("puntaje" not in v for v in lejos.values())
    assert all(v["estado"] in ("con_reportes_confirmados", "con_reportes_por_revisar", "sin_informacion_suficiente",
                               "sin_reportes_recientes", "necesidad_no_evaluada") for v in lejos.values())
    # sin ningún reporte en un sector con antecedentes => verificación proactiva
    sec = [s for s in workflow.listar_sectores() if s.nombre == "Santa Clara"][0]
    assert sec.antecedentes_interrupciones == 1


def test_sector_sin_reportes_con_antecedentes_pide_verificar():
    from data import database as db
    db.vaciar(["reportes"])
    vis = {v["sector"]: v for v in workflow.visibilidad_sectores()}
    assert vis["Capira"]["estado"] == "sin_reportes_recientes" and vis["Capira"]["requiere_verificacion_proactiva"]
    assert vis["La Chorrera"]["estado"] == "necesidad_no_evaluada"


# --------------------------------------------------------------------------- API
@pytest.fixture
def cliente(monkeypatch):
    """Cliente con el token de servicio (operador administrador) configurado por la prueba."""
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")
    return TestClient(app, headers={"X-API-Token": "op-test"})


def test_api_flujo_completo_y_estados_invalidos(cliente):
    r = cliente.post("/api/v1/reportes", json={"texto": "3 días sin agua en Capira, 80 personas. Urgente",
                                              "responsable": "ana", "clave_idempotencia": "abc"})
    assert r.status_code == 201
    rid = r.json()["id"]
    otra = cliente.post("/api/v1/reportes", json={"texto": "x", "responsable": "ana", "clave_idempotencia": "abc"})
    assert otra.json()["id"] == rid
    c = cliente.post(f"/api/v1/reportes/{rid}/confirmar", json={"responsable": "ana", "sector": "Capira",
                     "horas_sin_agua": 72, "personas_afectadas": 80, "severidad": "alta"})
    assert c.status_code == 200 and c.json()["estado"] == "confirmado"
    assert cliente.post(f"/api/v1/reportes/{rid}/confirmar",
                        json={"responsable": "ana", "sector": "Inexistente"}).status_code == 422
    plan = cliente.post("/api/v1/plan", json={"responsable": "ana"}).json()
    d = cliente.post(f"/api/v1/plan/{plan['id']}/decision", json={"accion": "aprobar", "responsable": "ana"})
    assert d.status_code == 200
    # transiciones inválidas directamente por la API
    pid = plan["id"]
    assert cliente.post(f"/api/v1/planes/{pid}/decision", json={"accion": "aprobar", "responsable": "ana"}).status_code == 409
    assert cliente.post(f"/api/v1/planes/{pid}/decision",
                        json={"accion": "rechazar", "responsable": "ana", "motivo": "x"}).status_code == 409
    assert cliente.put(f"/api/v1/planes/{pid}/asignaciones",
                       json={"responsable": "a", "motivo": "m", "asignaciones": []}).status_code in (409, 422)
    assert cliente.post("/api/v1/planes/999/decision", json={"accion": "aprobar", "responsable": "ana"}).status_code == 404
    assert cliente.post(f"/api/v1/reportes/{rid}/rechazar",
                        json={"responsable": "ana", "motivo": "x"}).status_code == 409  # ya está en plan


def test_api_estado_oculta_borradores_y_textos(cliente):
    workflow.corregir_y_confirmar(1, "t", "La Chorrera", 72, 40, "alta", None)
    workflow.generar_plan("t")
    est = cliente.get("/api/v1/estado").json()
    assert est["plan_pendiente_de_revision"]["borradores_comunicacion"] == []
    assert est["plan_aprobado"] is None  # un plan no aprobado nunca figura como aprobado
    assert "mensaje_original" not in json.dumps(est)


def test_api_autenticacion(monkeypatch):
    remoto = TestClient(app, client=("203.0.113.9", 5000))
    assert remoto.get("/api/v1/estado").status_code == 403           # sin tokens: solo local
    assert remoto.get("/api/v1/salud").status_code == 200
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-secreto")
    monkeypatch.setenv("AGUA_CLARA_DEVICE_TOKEN", "dev-secreto")
    assert remoto.get("/api/v1/estado").status_code == 401
    assert remoto.get("/api/v1/estado", headers={"X-API-Token": "dev-secreto"}).status_code == 200
    assert remoto.get("/api/v1/reportes", headers={"X-API-Token": "dev-secreto"}).status_code == 401
    assert remoto.post("/api/v1/planes/1/decision", headers={"X-API-Token": "dev-secreto"},
                       json={"accion": "aprobar", "responsable": "esp32"}).status_code == 401  # el dispositivo no aprueba
    assert remoto.get("/api/v1/reportes", headers={"X-API-Token": "op-secreto"}).status_code == 200
