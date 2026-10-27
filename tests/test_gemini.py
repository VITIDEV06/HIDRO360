"""Proveedor Gemini y tareas de IA. No usan red ni claves reales: el cliente del SDK se sustituye por un doble."""
import json
from types import SimpleNamespace

import pytest

import config
from models.schemas import FactorPrioridad, PrioridadSector
from services import ia_gemini, ia_tareas, ingest, tracing

CLAVE_FALSA = "clave-ficticia-de-prueba-no-real"


class ErrorHTTP(Exception):
    def __init__(self, code, msg="x"):
        super().__init__(msg)
        self.code = code


class Tipos:
    class GenerateContentConfig:
        def __init__(self, **kw):
            self.kw = kw


class ClienteFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.llamadas.append((model, contents, config.kw))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(text=r)


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    ia_gemini.reiniciar_estado()
    monkeypatch.setattr(ia_gemini.time, "sleep", lambda s: None)
    yield
    ia_gemini.reiniciar_estado()


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_FALSA)

    def poner(respuestas):
        c = ClienteFalso(respuestas)
        monkeypatch.setattr(ia_gemini, "_cliente", lambda: (c, Tipos))
        return c
    return poner


# ---------------------------------------------------------------- selección de proveedor
def test_proveedor_auto_local_sin_clave_y_gemini_con_clave(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert config.ai_provider() == "local"
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_FALSA)
    assert config.ai_provider() == "gemini"
    monkeypatch.setenv("AI_PROVIDER", "local")      # el modo local manda aunque haya clave
    assert config.ai_provider() == "local"


def test_modelo_configurable_sin_editar_codigo(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert config.GEMINI_MODEL() == config.GEMINI_MODELO_POR_DEFECTO
    monkeypatch.setenv("GEMINI_MODEL", "otro-modelo")
    assert config.GEMINI_MODEL() == "otro-modelo"


def test_sin_clave_lanza_error_ia(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ia_gemini.ErrorIA, match="GEMINI_API_KEY"):
        ia_gemini.generar_json("s", "u")


def test_uso_del_sdk_real_es_compatible():
    """Valida contra el SDK instalado (sin red): construcción del cliente y de la configuración."""
    from google import genai
    from google.genai import types
    genai.Client(api_key="x", http_options=types.HttpOptions(timeout=1000))
    cfg = types.GenerateContentConfig(system_instruction="s", temperature=0.0, response_mime_type="application/json")
    assert cfg.response_mime_type == "application/json"


# ------------------------------------------------------------------ llamadas y errores
def test_llamada_correcta_json_y_parametros(gemini):
    c = gemini(['{"a": 1}'])
    assert json.loads(ia_gemini.generar_json("sistema", "usuario")) == {"a": 1}
    modelo, contenido, cfg = c.llamadas[0]
    assert modelo == config.GEMINI_MODEL() and contenido == "usuario"
    assert cfg["system_instruction"] == "sistema" and cfg["response_mime_type"] == "application/json" and cfg["temperature"] == 0.0


def test_error_transitorio_se_reintenta_una_vez(gemini):
    c = gemini([ErrorHTTP(503), '{"ok": true}'])
    assert json.loads(ia_gemini.generar_json("s", "u")) == {"ok": True}
    assert len(c.llamadas) == 2


def test_error_transitorio_persistente_no_reintenta_indefinidamente(gemini):
    c = gemini([ErrorHTTP(503), ErrorHTTP(503), ErrorHTTP(503)])
    with pytest.raises(ia_gemini.ErrorIA):
        ia_gemini.generar_json("s", "u")
    assert len(c.llamadas) == 2  # 1 intento + 1 reintento (AI_MAX_REINTENTOS=1)


def test_cuota_429_no_se_reintenta_y_suspende(gemini):
    c = gemini([ErrorHTTP(429)])
    with pytest.raises(ia_gemini.ErrorIA, match="429"):
        ia_gemini.generar_json("s", "u")
    assert len(c.llamadas) == 1
    with pytest.raises(ia_gemini.ErrorIA, match="suspendido"):  # no vuelve a llamar al proveedor
        ia_gemini.generar_json("s", "u")
    assert len(c.llamadas) == 1
    assert ia_gemini.estado()["suspendido_s"] > 0


def test_credenciales_rechazadas_suspenden_mas_tiempo(gemini):
    gemini([ErrorHTTP(403)])
    with pytest.raises(ia_gemini.ErrorIA):
        ia_gemini.generar_json("s", "u")
    assert ia_gemini.estado()["suspendido_s"] > 600


def test_respuesta_vacia_es_error(gemini):
    gemini(["   "])
    with pytest.raises(ia_gemini.ErrorIA, match="vacía"):
        ia_gemini.generar_json("s", "u")


def test_la_clave_no_aparece_en_trazas_errores_ni_estado(gemini):
    gemini([ErrorHTTP(500, f"fallo con {CLAVE_FALSA}")])
    with pytest.raises(ia_gemini.ErrorIA) as e:
        ia_gemini.generar_json("s", "texto del ciudadano")
    assert CLAVE_FALSA not in str(e.value)
    assert CLAVE_FALSA not in config.TRACE_PATH.read_text(encoding="utf-8")
    assert "texto del ciudadano" not in config.TRACE_PATH.read_text(encoding="utf-8")
    assert CLAVE_FALSA not in json.dumps(ia_gemini.estado())


# ------------------------------------------------------------- interpretación con Gemini
def test_interpretar_con_gemini_y_redaccion_de_datos_personales(gemini):
    salida = json.dumps({"sector": "Chame", "horas_sin_agua": 48, "evidencia": {"horas_sin_agua": "2 días"}, "confianza": 0.8})
    c = gemini([salida])
    from services import workflow
    ex = ingest.interpretar("2 días sin agua en Chame. Llamar a Maria al 6000-1234 o maria@correo.com",
                            workflow.listar_sectores())
    enviado = c.llamadas[0][1]
    assert "6000-1234" not in enviado and "maria@correo.com" not in enviado and "[tel]" in enviado and "[correo]" in enviado
    assert ex.metodo == "gemini" and ex.horas_sin_agua == 48 and ex.sector == "Chame"


def test_fallo_de_gemini_se_declara_como_fallback(gemini):
    gemini([ErrorHTTP(500)] * 3)
    from services import workflow
    ex = ingest.interpretar("2 días sin agua en Chame", workflow.listar_sectores())
    assert ex.metodo == "reglas(fallback)" and any("modo local" in n for n in ex.notas)
    assert any(e["status"] == "fallback" for e in tracing.leer(agent="ingesta"))


def test_redactar_pii():
    assert ingest.redactar_pii("tel 6000-1234 y +507 6123 4567, a@b.co") == "tel [tel] y [tel], [correo]"
    assert "12 personas" in ingest.redactar_pii("somos 12 personas")  # no toca cifras cortas


# -------------------------------------------------------------------------- tareas IA
def _prio():
    f = FactorPrioridad(nombre="Horas sin agua", valor=72, normalizado=0.75, peso=0.4, contribucion=30.0, explicacion="72 h")
    return PrioridadSector(sector="Chame", puntaje=50.0, categoria="ALTA", factores=[f], reportes_ids=[1],
                           reportes_contados=1, razones=["Horas sin agua: +30 pts"])


def test_tareas_ia_modo_local(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "local")
    assert ia_tareas.explicar_prioridad(_prio())["origen"] == "local"
    r = ia_tareas.borrador_comunicacion("Chame", 1500, ["CIS-001"])
    assert r["origen"] == "local" and "BORRADOR" in r["texto"]


def test_resumen_gemini_valido_e_invalido(gemini):
    gemini([json.dumps({"resumen": "Chame obtiene 50.0 puntos sobre todo por 72 horas sin agua."})])
    r = ia_tareas.explicar_prioridad(_prio())
    assert r["origen"] == "gemini"
    gemini([json.dumps({"resumen": "Chame tiene 99 puntos por una avería inventada."})])  # cifra inventada
    r = ia_tareas.explicar_prioridad(_prio())
    assert r["origen"].startswith("local") and "Horas sin agua" in r["texto"]


def test_borrador_gemini_no_puede_prometer_hora(gemini):
    gemini([json.dumps({"texto": "Aviso para Chame: la cisterna llegará a las 15:30."})])
    r = ia_tareas.borrador_comunicacion("Chame", 1500, ["CIS-001"])
    assert r["origen"].startswith("local") and "15:30" not in r["texto"]
    gemini([json.dumps({"texto": "Aviso para Chame: se programa abastecimiento de hasta 1500 litros con la cisterna CIS-001; "
                                 "informaremos cuando haya hora confirmada."})])
    r = ia_tareas.borrador_comunicacion("Chame", 1500, ["CIS-001"])
    assert r["origen"] == "gemini" and r["texto"].endswith(ia_tareas.communication.AVISO)
