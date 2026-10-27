"""
Robustez de la capa de IA: fallo controlado, presupuesto de tiempo, permisos (solo lectura/redacción),
ayudas al operador y observabilidad sin fugas.

Los dobles de Gemini son CLIENTES SIMULADOS (mock): demuestran la validación y el fallback, no el servicio real
(la verificación contra el servicio real se hizo aparte y está descrita en docs/IA_GEMINI.md).
"""
import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from models.schemas import EstadoRecomendacion, Reporte
from services import auth, ia_gemini, ia_operador, ingest, orquestador, prioritization, tracing, workflow, workflow_ia
from services.workflow import ErrorDeNegocio

CLAVE_FALSA = "clave-ficticia-de-prueba-no-real"
TEXTO = "Llevamos 3 días sin agua en La Chorrera, calle principal. Somos 40 personas con ancianos."
NOMBRES = list(orquestador.ETAPAS)


def reporte_confirmado(texto=TEXTO):
    r = workflow.crear_reporte(texto, "tests")
    return workflow.corregir_y_confirmar(r.id, "tests", "La Chorrera", 72, 40, "alta", "calle principal")


class ErrorHTTP(Exception):
    def __init__(self, code):
        super().__init__("x")
        self.code = code


class ErrorTimeout(Exception):  # el nombre contiene «timeout»: se clasifica como transitorio
    pass


class Tipos:
    class GenerateContentConfig:
        def __init__(self, **kw):
            self.kw = kw

    class HttpOptions:
        def __init__(self, timeout):
            self.timeout = timeout


class ClienteFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0
        self.plazos = []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.llamadas += 1
        ho = config.kw.get("http_options")
        self.plazos.append(ho.timeout if ho else None)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(text=r)


INTERPRETACION_OK = json.dumps({"sector": "La Chorrera", "horas_sin_agua": 72, "personas_afectadas": 40,
                                "tipo_incidencia": "sin_suministro", "confianza": 0.8,
                                "evidencia": {"horas_sin_agua": "3 días", "personas_afectadas": "40 personas"}})
RECOMENDACION_OK = json.dumps({"accion": "Verificar el reporte y atender La Chorrera según la prioridad calculada."})


@pytest.fixture
def gemini(monkeypatch):
    ia_gemini.reiniciar_estado()
    monkeypatch.setattr(ia_gemini.time, "sleep", lambda s: None)

    def poner(respuestas):
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", CLAVE_FALSA)
        c = ClienteFalso(respuestas)
        monkeypatch.setattr(ia_gemini, "_cliente", lambda: (c, Tipos))
        return c
    yield poner
    ia_gemini.reiniciar_estado()


TABLAS_CRITICAS = ("reportes", "planes", "misiones", "usuarios", "cisternas", "sectores", "dispositivos")


def _foto_estado():
    from data import database as db
    return {t: db.listar(t) for t in TABLAS_CRITICAS}


# ============================================================================================================
# Fallo controlado: Gemini roto => el sistema sigue y NO cambia datos críticos
# ============================================================================================================
@pytest.mark.parametrize("respuestas", [
    [ErrorHTTP(400), ErrorHTTP(400)],                                    # clave inválida (API_KEY_INVALID llega como 400)
    [ErrorHTTP(404), ErrorHTTP(404)],                                    # modelo inexistente
    [ErrorTimeout(), ErrorTimeout(), ErrorTimeout(), ErrorTimeout()],    # timeout (con su reintento)
    [ErrorHTTP(429)],                                                    # cuota
    ["{no es json", "[]"],                                               # JSON inválido
    [json.dumps({"sector": "Chame", "campo_inventado": 1}), json.dumps({"accion": "Revisar.", "extra": 1})],
    [INTERPRETACION_OK, json.dumps({"accion": "Enviar 5000 litros con CIS-009 a Chame hoy."})],
], ids=["clave_invalida", "modelo_inexistente", "timeout", "429", "json_invalido", "campos_extra", "inventa_datos"])
def test_gemini_roto_no_cambia_datos_criticos_y_el_flujo_termina(gemini, respuestas):
    r = reporte_confirmado()
    antes = _foto_estado()
    esperado = [(p.sector, p.puntaje) for p in
                prioritization.calcular_prioridades(workflow.listar_reportes(), workflow.listar_sectores())]
    gemini(respuestas)
    rec = workflow_ia.analizar_reporte(r.id, "tests")
    assert [e.nombre for e in rec.etapas] == NOMBRES and rec.estado == EstadoRecomendacion.PROPUESTA
    assert rec.proveedor == "reglas(fallback)"
    assert all(e.proveedor != "gemini" or e.nombre == "clasificacion" for e in rec.etapas)
    assert "Chame" not in rec.accion_sugerida and "CIS-009" not in rec.accion_sugerida and "5000" not in rec.accion_sugerida
    assert rec.cisterna_sugerida == "CIS-001"
    assert _foto_estado() == antes                       # reportes, planes, misiones, usuarios, flota: intactos
    assert workflow.listar_misiones() == [] and workflow.listar_planes() == []
    assert [(p.sector, p.puntaje) for p in prioritization.calcular_prioridades(
        workflow.listar_reportes(), workflow.listar_sectores())] == esperado


def test_clasificacion_inventada_no_cambia_el_reporte(gemini):
    r = reporte_confirmado()
    mala = json.dumps({"sector": "Chame", "severidad": "crítica", "personas_afectadas": 9999,
                       "evidencia": {"severidad": "crítica", "personas_afectadas": "9999"}})
    antes = workflow.obtener_reporte(r.id)
    gemini([mala, RECOMENDACION_OK])
    rec = workflow_ia.analizar_reporte(r.id, "tests")
    assert workflow.obtener_reporte(r.id) == antes
    clasif = next(e for e in rec.etapas if e.nombre == "clasificacion")
    assert clasif.detalle["sector_texto"] != "Chame" and clasif.detalle["severidad_texto"] is None


def test_salida_real_con_servicio_esencial_booleano_y_evidencia_nula_ya_no_se_descarta(gemini):
    """Forma observada con el servicio real: `servicio_esencial: false` y `evidencia: {campo: null}`."""
    r = reporte_confirmado()
    real = json.dumps({"sector": "La Chorrera", "horas_sin_agua": 72, "personas_afectadas": 40, "severidad": "alta",
                       "servicio_esencial": False, "vulnerabilidades": ["ancianos"],
                       "evidencia": {"horas_sin_agua": "3 días", "personas_afectadas": "40 personas", "calle": None},
                       "confianza": 0.9})
    gemini([real, RECOMENDACION_OK])
    rec = workflow_ia.analizar_reporte(r.id, "tests")
    est = {e.nombre: e for e in rec.etapas}
    assert est["clasificacion"].proveedor == "gemini" and est["recomendacion"].proveedor == "gemini"


def test_campos_desconocidos_siguen_rechazandose():
    assert ingest._normalizar_tipos({"servicio_esencial": True, "evidencia": {"a": None, "b": "x"}, "otro": 1}) == \
        {"servicio_esencial": None, "evidencia": {"b": "x"}, "otro": 1}
    with pytest.raises(Exception):
        ingest.SalidaIA(**ingest._normalizar_tipos({"campo_nuevo": 1}))


# ============================================================================================================
# Presupuesto de tiempo (serverless)
# ============================================================================================================
class RelojFalso:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_presupuesto_corta_la_segunda_etapa_y_usa_respaldo_local(gemini, monkeypatch):
    r = reporte_confirmado()
    reloj = RelojFalso()
    c = gemini([INTERPRETACION_OK, RECOMENDACION_OK])
    monkeypatch.setattr(ia_gemini, "time", reloj)
    gen = c._gen

    def lenta(model, contents, config):
        reloj.t += 12.0          # la primera llamada consume 12 s de un presupuesto de 20
        return gen(model, contents, config)
    c.models.generate_content = lenta
    rec = workflow_ia.analizar_reporte(r.id, "tests")
    est = {e.nombre: e for e in rec.etapas}
    assert est["clasificacion"].proveedor == "gemini"
    assert est["recomendacion"].proveedor == "reglas(fallback)"       # quedan 8 s (< 10 s mínimos de la API)
    assert "presupuesto" in est["recomendacion"].detalle["motivo_fallback"]
    assert c.llamadas == 1 and rec.proveedor == "reglas(fallback)" and rec.cisterna_sugerida == "CIS-001"


def test_presupuesto_menor_al_minimo_no_llama_a_gemini(gemini, monkeypatch):
    r = reporte_confirmado()
    monkeypatch.setenv("HIDRO360_IA_PRESUPUESTO_S", "5")
    c = gemini([INTERPRETACION_OK, RECOMENDACION_OK])
    rec = workflow_ia.analizar_reporte(r.id, "tests")
    assert c.llamadas == 0 and rec.proveedor == "reglas(fallback)" and [e.nombre for e in rec.etapas] == NOMBRES


def test_el_plazo_de_cada_llamada_se_recorta_al_presupuesto_sin_bajar_de_10_s(gemini):
    c = gemini(['{"a": 1}', '{"a": 1}'])
    with ia_gemini.presupuesto(12):
        ia_gemini.generar_json("s", "u")
    assert 11000 <= c.plazos[0] <= 12000                       # recortado al tiempo restante (< 15 s configurados)
    ia_gemini.generar_json("s", "u")
    assert c.plazos[1] is None                         # sin presupuesto: se usa el plazo del cliente


def test_sin_tiempo_para_el_reintento_no_se_reintenta(gemini, monkeypatch):
    monkeypatch.setattr(ia_gemini, "time", RelojFalso())
    c = gemini([ErrorHTTP(503), '{"ok": true}'])
    with ia_gemini.presupuesto(10.5):                 # 10 s mínimos + 1,5 s de pausa no caben
        with pytest.raises(ia_gemini.ErrorIA):
            ia_gemini.generar_json("s", "u")
    assert c.llamadas == 1


def test_peor_caso_documentado_con_presupuesto(gemini, monkeypatch):
    """Todas las llamadas agotan el plazo (15 s): con presupuesto de 20 s el análisis simulado dura <= 20 s."""
    r = reporte_confirmado()
    reloj = RelojFalso()
    c = gemini([ErrorTimeout()] * 8)
    monkeypatch.setattr(ia_gemini, "time", reloj)
    gen = c._gen

    def agota(model, contents, config):
        reloj.t += config.kw["http_options"].timeout / 1000 if config.kw.get("http_options") else 15.0
        return gen(model, contents, config)
    c.models.generate_content = agota
    inicio = reloj.t
    rec = workflow_ia.analizar_reporte(r.id, "tests")
    assert reloj.t - inicio <= 20.0 + 1e-6, reloj.t - inicio
    assert rec.proveedor == "reglas(fallback)" and [e.nombre for e in rec.etapas] == NOMBRES


# ============================================================================================================
# Permisos de la capa de IA: lectura y redacción, nunca escritura
# ============================================================================================================
MODULOS_IA = ("ia_gemini", "ia_tareas", "ia_operador", "orquestador", "ingest")
ESCRITURAS = {"crear_mision", "decidir_recomendacion", "aprobar_plan", "crear_usuario", "crear_reporte", "complementar_reporte", "confirmar",
              "corregir_y_confirmar", "rechazar_reporte", "marcar_duplicado", "insertar", "actualizar", "borrar",
              "eliminar", "eliminar_foto", "eliminar_audio", "registrar", "executescript", "cargar_datos_demo"}
PROHIBIDOS = {"workflow", "workflow_ia", "database", "audit", "auth", "fotos", "audios"}


@pytest.mark.parametrize("modulo", MODULOS_IA)
def test_la_capa_de_ia_no_importa_ni_llama_funciones_que_escriben(modulo):
    arbol = ast.parse((config.BASE_DIR / "services" / f"{modulo}.py").read_text(encoding="utf-8"))
    for n in ast.walk(arbol):
        if isinstance(n, ast.ImportFrom):
            importados = {a.name for a in n.names} | {(n.module or "").split(".")[-1]}
        elif isinstance(n, ast.Import):
            importados = {x for a in n.names for x in a.name.split(".")}
        else:
            continue
        assert not (importados & PROHIBIDOS), f"{modulo} importa {importados & PROHIBIDOS}"
    llamadas = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                for n in ast.walk(arbol) if isinstance(n, ast.Call)}
    assert not (llamadas & ESCRITURAS), f"{modulo} llama a {llamadas & ESCRITURAS}"


def test_solo_decidir_recomendacion_crea_misiones_desde_una_recomendacion():
    quienes = set()
    for ruta in list((config.BASE_DIR / "services").glob("*.py")) + list((config.BASE_DIR / "api").glob("*.py")):
        for f in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            if isinstance(f, ast.FunctionDef) and any(
                    isinstance(c, ast.Call) and getattr(c.func, "attr", getattr(c.func, "id", "")) == "crear_mision"
                    and any(k.arg == "recomendacion_id" for k in c.keywords) for c in ast.walk(f)):
                quienes.add((ruta.name, f.name))
    assert quienes == {("workflow_ia.py", "decidir_recomendacion")}


def test_flujo_completo_de_ia_no_toca_usuarios_misiones_ni_planes(gemini):
    r = reporte_confirmado()
    antes = _foto_estado()
    gemini([INTERPRETACION_OK, RECOMENDACION_OK,
            json.dumps({"resumen": "Reporte de La Chorrera con 72 h sin agua y 40 personas afectadas."}),
            json.dumps({"resumen": "Sin patrones relevantes en este periodo de tiempo."})])
    workflow_ia.analizar_reporte(r.id, "tests")
    workflow_ia.resumen_contexto_reporte(r.id)
    workflow_ia.detectar_patrones(720, 2)
    assert _foto_estado() == antes and workflow.listar_misiones() == []


# ============================================================================================================
# Ayudas al operador
# ============================================================================================================
def test_contexto_local_usa_solo_datos_existentes():
    r = reporte_confirmado()
    out = workflow_ia.resumen_contexto_reporte(r.id)
    assert out["resumen"]["origen"] == "reglas" and f"Reporte {r.id}" in out["resumen"]["texto"]
    assert out["datos"]["reporte"]["sector"] == "La Chorrera"
    assert TEXTO not in json.dumps(out) and "mensaje_original" not in json.dumps(out)
    with pytest.raises(ErrorDeNegocio, match="no existe"):
        workflow_ia.resumen_contexto_reporte(9999)


def test_contexto_con_gemini_valido_y_con_invento(gemini):
    r = reporte_confirmado()
    gemini([json.dumps({"resumen": "El reporte de La Chorrera indica 72 h sin agua y 40 personas afectadas."})])
    assert workflow_ia.resumen_contexto_reporte(r.id)["resumen"]["origen"] == "gemini"
    gemini([json.dumps({"resumen": "Chame también está afectada y llegarán cisternas mañana."})])
    out = workflow_ia.resumen_contexto_reporte(r.id)
    assert out["resumen"]["origen"] == "reglas(fallback)" and "Chame" not in out["resumen"]["texto"]


def _rep(i, fecha, sector, estado="confirmado"):
    return Reporte(id=i, fecha_creacion=fecha, sector=sector, mensaje_original="x", estado=estado)


def test_patrones_deterministas():
    ahora = datetime(2026, 10, 17, 12, 0)
    d = datetime(2026, 10, 17, 8, 0)
    rs = [_rep(i, d.replace(minute=m), "Chame") for i, m in enumerate((5, 10, 20), 1)]
    rs += [_rep(4, d.replace(minute=40), "Arraiján"), _rep(5, d.replace(hour=9, minute=1), "Arraiján"),
           _rep(6, d.replace(minute=50), "La Chorrera", "rechazado"), _rep(8, d.replace(minute=30), "Vista Alegre"), _rep(7, datetime(2026, 9, 1, 8, 0), "Chame")]
    sectores = ["Chame", "Arraiján", "La Chorrera"]
    p = ia_operador.detectar_patrones(rs, sectores, 72, 3, ahora, usar_ia=False)
    tipos = {(x["tipo"], x.get("sector") if x["tipo"] == "sector_recurrente" else x["hora"]) for x in p["patrones"]}
    assert ("sector_recurrente", "Chame") in tipos and ("pico_horario", 8) in tipos
    assert not any(x.get("sector") in ("Arraiján", "La Chorrera") for x in p["patrones"])
    chame = next(x for x in p["patrones"] if x.get("sector") == "Chame")
    assert chame["reportes"] == 3 and chame["reporte_ids"] == [1, 2, 3] and "3 reportes" in chame["explicacion"]
    assert p == ia_operador.detectar_patrones(rs, sectores, 72, 3, ahora, usar_ia=False)
    with pytest.raises(ErrorDeNegocio):
        workflow_ia.detectar_patrones(0, 3)
    with pytest.raises(ErrorDeNegocio):
        workflow_ia.detectar_patrones(72, 1)


def test_resumen_de_patrones_con_gemini_no_puede_inventar(gemini):
    ahora = datetime(2026, 10, 17, 12, 0)
    rs = [_rep(i, ahora - timedelta(hours=i), "Chame") for i in (1, 2, 3)]
    gemini([json.dumps({"resumen": "Chame acumula 3 reportes en las últimas 72 horas; conviene revisarlo."})])
    assert ia_operador.detectar_patrones(rs, ["Chame", "Arraiján"], 72, 3, ahora)["resumen"]["origen"] == "gemini"
    gemini([json.dumps({"resumen": "Chame acumula 9 reportes por una fuga en Arraiján."})])
    assert ia_operador.detectar_patrones(rs, ["Chame", "Arraiján"], 72, 3, ahora)["resumen"]["origen"] == "reglas(fallback)"


def test_ayudas_del_operador_exigen_admin():
    r = reporte_confirmado()
    auth.actor_actual.set(None)
    with pytest.raises(Exception):
        workflow_ia.resumen_contexto_reporte(r.id)
    with pytest.raises(Exception):
        workflow_ia.detectar_patrones()


@pytest.fixture
def cliente(monkeypatch):
    from fastapi.testclient import TestClient
    from api import main as api_main, rutas_ia
    if not any(getattr(r, "path", "").endswith("/patrones") for r in api_main.app.routes):
        api_main.app.include_router(rutas_ia.crear_router(api_main.admin), prefix="/api/v1")
    monkeypatch.setenv("HIDRO360_API_TOKEN", "token-de-prueba")
    return TestClient(api_main.app, headers={"X-API-Token": "token-de-prueba"})


def test_api_contexto_y_patrones(cliente):
    r = reporte_confirmado()
    c = cliente.get(f"/api/v1/reportes/{r.id}/contexto")
    assert c.status_code == 200 and c.json()["resumen"]["origen"] == "reglas"
    p = cliente.get("/api/v1/patrones", params={"ventana_horas": 720, "minimo_reportes": 2})
    assert p.status_code == 200 and p.json()["parametros"]["ventana_horas"] == 720
    assert cliente.get("/api/v1/patrones", params={"ventana_horas": 0}).status_code == 422
    assert cliente.get("/api/v1/reportes/9999/contexto").status_code == 404
    from fastapi.testclient import TestClient
    assert TestClient(cliente.app).get("/api/v1/patrones").status_code == 401


# ============================================================================================================
# Observabilidad sin fugas
# ============================================================================================================
def test_trazas_con_modelo_esquema_tokens_y_sin_fugas(gemini):
    tel, correo = "6611-2299", "maria.prueba@example.com"
    texto = (f"Soy Maria, mi cel es {tel} y mi correo {correo}. Llevamos 3 días sin agua en La Chorrera, "
             "frente al centro de salud. Somos 40 personas con ancianos.")
    r = workflow.crear_reporte(texto, "tests")
    r = workflow.corregir_y_confirmar(r.id, "tests", "La Chorrera", 72, 40, "alta", "calle principal")
    c = gemini([INTERPRETACION_OK, RECOMENDACION_OK])
    gen = c._gen

    def con_uso(model, contents, config):
        out = gen(model, contents, config)
        out.usage_metadata = SimpleNamespace(prompt_token_count=120, candidates_token_count=30, total_token_count=150)
        return out
    c.models.generate_content = con_uso
    workflow_ia.analizar_reporte(r.id, "tests")
    crudo = config.TRACE_PATH.read_text(encoding="utf-8")
    ia = [e for e in tracing.leer() if e["agent"] == "ia" and e["action"].startswith("gemini.")]
    assert {e["action"] for e in ia} == {"gemini.interpretar", "gemini.recomendar"}
    for e in ia:
        assert e["detalle"]["modelo"] == config.GEMINI_MODEL() and e["detalle"]["esquema_salida"]
        assert e["output_summary"]["uso"] == {"entrada": 120, "salida": 30, "total": 150}
        assert e["duration_ms"] is not None and e["status"] == "ok"
    for secreto in (CLAVE_FALSA, tel, "66112299", correo, "maria.prueba", texto[:40], "frente al centro de salud"):
        assert secreto not in crudo, f"fuga en trazas: {secreto!r}"
    assert tracing.verificar_integridad()["integra"] is True
