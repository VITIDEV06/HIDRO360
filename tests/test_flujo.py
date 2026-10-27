import pytest

from models.schemas import AccionDecision, Asignacion, EstadoPlan, EstadoReporte
from services import audit, ingest, planner, workflow
from services.workflow import ErrorDeNegocio


def _confirmar_todos():
    for r in workflow.listar_reportes():
        if r.sector is None:  # el reporte sin sector exige corrección manual
            continue
        workflow.corregir_y_confirmar(r.id, "t", r.sector, r.horas_sin_agua, r.personas_afectadas,
                                      r.severidad.value if r.severidad else None, r.calle)


def test_ingesta_conserva_texto_y_marca_faltantes():
    ex = ingest.interpretar("Sin agua en Capira", workflow.listar_sectores())
    assert ex.sector == "Capira"
    assert {"horas_sin_agua", "personas_afectadas"} <= set(ex.datos_faltantes)
    r = workflow.crear_reporte("Llevamos 3 días sin agua en La Chorrera, 40 familias. Urgente")
    assert r.mensaje_original.startswith("Llevamos 3 días")
    assert r.horas_sin_agua == 72 and r.personas_afectadas == 160
    assert "personas_afectadas" in r.datos_inciertos  # familias x 4 es estimación


def test_nuevo_arraijan_no_se_confunde_con_arraijan():
    ex = ingest.interpretar("6 horas sin agua en Nuevo Arraiján", workflow.listar_sectores())
    assert ex.sector == "Nuevo Arraiján"


def test_duplicados_se_sugieren_pero_no_se_fusionan():
    reps = workflow.listar_reportes()
    chorrera = [r for r in reps if r.sector == "La Chorrera"]
    cands = workflow.sugerir_duplicados(chorrera[1].id)
    assert any(c.reporte_id == chorrera[0].id for c in cands)
    assert all(c.dudoso for c in cands)
    assert all(r.estado == EstadoReporte.POR_REVISAR for r in workflow.listar_reportes())


def test_prioridad_reproducible_y_quejas_no_suman():
    _confirmar_todos()
    p1 = {p.sector: p.puntaje for p in workflow.calcular_prioridades()}
    assert p1 == {p.sector: p.puntaje for p in workflow.calcular_prioridades()}
    # añadir otra queja idéntica en La Chorrera no cambia el puntaje
    n = workflow.crear_reporte("3 días sin agua en La Chorrera, calle principal. 40 familias.")
    workflow.corregir_y_confirmar(n.id, "t", n.sector, n.horas_sin_agua, n.personas_afectadas, None, n.calle)
    p2 = {p.sector: p.puntaje for p in workflow.calcular_prioridades()}
    assert p2["La Chorrera"] == p1["La Chorrera"]


def test_plan_respeta_capacidad_y_reporta_pendientes():
    _confirmar_todos()
    plan = workflow.generar_plan("t")
    cist = {c.id: c for c in workflow.listar_cisternas()}
    usado = {}
    for a in plan.asignaciones:
        usado[a.cisterna_id] = usado.get(a.cisterna_id, 0) + a.litros
        assert cist[a.cisterna_id].estado.value == "disponible"
    assert all(usado[k] <= cist[k].litros_disponibles for k in usado)
    assert plan.pendientes, "la demo debe mostrar escasez"
    assert all(p.motivo for p in plan.pendientes)
    assert planner.validar_asignaciones(plan.asignaciones, workflow.listar_cisternas()) == []
    primero = plan.prioridades[0].sector  # el de mayor prioridad se atiende primero
    assert any(a.sector == primero for a in plan.asignaciones)


def test_borradores_no_prometen_hora():
    _confirmar_todos()
    plan = workflow.generar_plan("t")
    for b in plan.borradores_comunicacion:
        assert "BORRADOR" in b["texto"]
        assert "llegará a las" not in b["texto"].lower()


def test_edicion_valida_capacidad_y_aprobacion_audita():
    _confirmar_todos()
    plan = workflow.generar_plan("t")
    a0 = plan.asignaciones[0]
    excesivo = [Asignacion(sector=a0.sector, cisterna_id=a0.cisterna_id, litros=10**6, prioridad=a0.prioridad)]
    with pytest.raises(ErrorDeNegocio):
        workflow.editar_plan(plan.id, excesivo, "t", "prueba")
    with pytest.raises(ErrorDeNegocio):
        workflow.editar_plan(plan.id, plan.asignaciones[1:], "t", "")
    editado = workflow.editar_plan(plan.id, plan.asignaciones[1:], "ana", "cisterna reservada")
    assert editado.editado and editado.version == 2
    with pytest.raises(ErrorDeNegocio):
        workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "ana", "")  # editado: exige comentario
    ap = workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "ana", "ok")
    assert ap.estado == EstadoPlan.APROBADO
    acciones = [e.accion for e in audit.listar("plan", plan.id)]
    assert acciones == ["generar_plan", "editar_plan", "aprobar_plan"]
    e = audit.listar("plan", plan.id)[1]
    assert e.responsable == "servicio:ana" and e.motivo and "antes" in e.detalle and "despues" in e.detalle
    assert audit.verificar_integridad()


def test_rechazo_exige_motivo():
    _confirmar_todos()
    plan = workflow.generar_plan("t")
    with pytest.raises(ErrorDeNegocio):
        workflow.decidir_plan(plan.id, AccionDecision.RECHAZAR, "t", "")
    assert workflow.decidir_plan(plan.id, AccionDecision.RECHAZAR, "t", "datos dudosos").estado == EstadoPlan.RECHAZADO


def test_api_contrato(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    _confirmar_todos()
    plan = workflow.generar_plan("t")
    workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "t", "")
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")
    c = TestClient(app, headers={"X-API-Token": "op-test"})
    est = c.get("/api/v1/estado").json()
    assert est["plan_aprobado"]["id"] == plan.id
    r = c.post("/api/v1/dispositivos/confirmaciones",
               json={"dispositivo_id": "rpi-01", "plan_id": plan.id, "tipo": "plan_recibido"})
    assert r.status_code == 202
    assert c.get("/api/v1/auditoria").json()["integra"] is True
