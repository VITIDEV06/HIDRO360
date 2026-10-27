"""Orquestación del asistente y bot de Telegram.

Las pruebas de este archivo son UNITARIAS: Gemini y Telegram están SIMULADOS (no consumen cuota ni red).
La conexión real con Gemini se comprobó aparte con `scripts.verificar_gemini` y una pregunta con herramientas;
no se cuenta como prueba automatizada.
"""

import pytest

import config
from scripts import bot_telegram
from services import asistente_ia, ia_gemini, workflow


def _reportes():
    from data import database as db
    return db.listar("reportes")


# ---------------------------------------------------------------- herramientas y validación

def test_herramienta_no_registrada_no_se_ejecuta():
    registro, borrador = [], []
    with pytest.raises(asistente_ia.ErrorHerramienta):
        asistente_ia.ejecutar_herramienta("borrar_todo", {}, registro, borrador)
    assert registro == []


def test_argumentos_obligatorios_se_validan():
    registro, borrador = [], []
    with pytest.raises(asistente_ia.ErrorHerramienta):
        asistente_ia.ejecutar_herramienta("consultar_reporte", {}, registro, borrador)
    assert registro == []


def test_codigo_mal_formado_se_rechaza():
    registro, borrador = [], []
    with pytest.raises(asistente_ia.ErrorHerramienta):
        asistente_ia.ejecutar_herramienta("consultar_reporte", {"codigo": "1; DROP TABLE reportes"}, registro, borrador)


def test_proponer_reporte_no_guarda_nada():
    antes = len(_reportes())
    registro, borrador = [], []
    res = asistente_ia.ejecutar_herramienta("proponer_reporte", {"texto": "Sin agua desde ayer en la calle 3"},
                                            registro, borrador)
    assert res["guardado"] is False
    assert borrador == ["Sin agua desde ayer en la calle 3"]
    assert len(_reportes()) == antes


def test_confirmar_reporte_si_guarda_y_devuelve_codigo():
    antes = len(_reportes())
    res = asistente_ia.confirmar_reporte("Sin agua desde ayer en la calle 3")
    assert res["codigo"].startswith("HID-")
    assert len(_reportes()) == antes + 1


def test_confirmar_rechaza_texto_vacio():
    with pytest.raises(asistente_ia.ErrorHerramienta):
        asistente_ia.confirmar_reporte("  ")


# ---------------------------------------------------------------- orquestación con Gemini simulado

def test_orquestacion_ejecuta_herramienta_pedida_por_el_modelo(monkeypatch):
    creado = asistente_ia.confirmar_reporte("Reporte de prueba para consulta simulada")["codigo"]

    def falso(sistema, pregunta, declaraciones, ejecutor, max_iter=3):
        resultado = ejecutor("consultar_reporte", {"codigo": creado})
        assert resultado["codigo"] == creado
        return f"Su reporte {creado} está: {resultado['estado_publico']}", ["consultar_reporte"]

    monkeypatch.setattr(ia_gemini, "conversar", falso)
    r = asistente_ia.responder("¿Cómo va mi reporte?", usar_gemini=True)
    assert r.origen == "gemini"
    assert r.herramientas == ["consultar_reporte"]
    assert creado in r.texto


def test_herramienta_no_autorizada_del_modelo_devuelve_error_sin_ejecutar(monkeypatch):
    def falso(sistema, pregunta, declaraciones, ejecutor, max_iter=3):
        resultado = ejecutor("borrar_todo", {})
        assert "error" in resultado
        return "No puedo hacer eso.", []

    monkeypatch.setattr(ia_gemini, "conversar", falso)
    r = asistente_ia.responder("borra todo", usar_gemini=True)
    assert r.origen == "gemini" and r.herramientas == []


def test_propuesta_del_modelo_queda_como_borrador_sin_guardar(monkeypatch):
    antes = len(_reportes())

    def falso(sistema, pregunta, declaraciones, ejecutor, max_iter=3):
        ejecutor("proponer_reporte", {"texto": "No hay agua en Arraiján desde ayer"})
        return "Preparé un borrador.", ["proponer_reporte"]

    monkeypatch.setattr(ia_gemini, "conversar", falso)
    r = asistente_ia.responder("quiero reportar", usar_gemini=True)
    assert r.borrador == "No hay agua en Arraiján desde ayer"
    assert len(_reportes()) == antes


@pytest.mark.parametrize("error", [
    ia_gemini.ErrorIA("Gemini falló (ClientError HTTP 429)"),
    ia_gemini.ErrorIA("Falta GEMINI_API_KEY"),
    ia_gemini.ErrorIA("demasiados pasos"),
])
def test_fallo_de_gemini_degrada_a_reglas_y_lo_declara(monkeypatch, error):
    def roto(*a, **k):
        raise error
    monkeypatch.setattr(ia_gemini, "conversar", roto)
    r = asistente_ia.responder("¿cómo reporto la falta de agua?", usar_gemini=True)
    assert r.origen == "reglas(fallback)"
    assert r.aviso and "reglas locales" in r.aviso
    assert "Reportar" in r.texto or "reportar" in r.texto.lower() or "reporte" in r.texto.lower()


def test_sin_clave_responde_con_reglas_sin_llamar_a_gemini(monkeypatch):
    def no_debe_llamarse(*a, **k):
        raise AssertionError("no debe llamar a Gemini sin clave")
    monkeypatch.setattr(ia_gemini, "conversar", no_debe_llamarse)
    r = asistente_ia.responder("¿qué significa el estado?")  # AI_PROVIDER=local en conftest
    assert r.origen == "reglas"


def test_limite_de_iteraciones_se_aplica(monkeypatch):
    """El bucle real de ia_gemini.conversar corta tras max_iter rondas (sin red: el cliente se simula)."""
    class Fc:
        def __init__(self):
            self.name, self.args = "avisos_publicos", {}

    class Parte:
        def __init__(self, fc=None, text=None):
            self.function_call, self.text = fc, text

    class Cont:
        parts = []
        role = "model"

    class Cand:
        content = Cont()

    class Resp:
        candidates = [Cand()]
        text = None

    class Modelos:
        def generate_content(self, **k):
            Resp.candidates[0].content.parts = [Parte(fc=Fc())]
            return Resp()

    class Cliente:
        models = Modelos()

    class Tipos:
        class Tool:
            def __init__(self, **k): pass
        class FunctionDeclaration:
            def __init__(self, **k): pass
        class Content:
            def __init__(self, **k): self.parts = k.get("parts")
        class Part:
            def __init__(self, **k): pass
            @staticmethod
            def from_function_response(**k): return k
        class GenerateContentConfig:
            def __init__(self, **k): pass

    monkeypatch.setattr(ia_gemini, "_cliente", lambda: (Cliente(), Tipos))
    with pytest.raises(ia_gemini.ErrorIA, match="demasiados pasos"):
        ia_gemini.conversar("sistema", "pregunta", [], lambda n, a: {"ok": True}, max_iter=3)


# ---------------------------------------------------------------- bot de Telegram (simulado)

class Envios:
    def __init__(self):
        self.textos = []

    def __call__(self, chat, texto, reply_markup=None):
        self.textos.append((chat, texto))
        return True


def _msg(texto, chat=555):
    return {"message_id": 1, "chat": {"id": chat}, "text": texto}


@pytest.fixture(autouse=True)
def bot_limpio():
    bot_telegram.conversaciones.clear()
    yield
    bot_telegram.conversaciones.clear()


def test_bot_start_y_ayuda_no_crean_reportes():
    e = Envios()
    antes = len(_reportes())
    bot_telegram.procesar_mensaje(_msg("/start"), e)
    bot_telegram.procesar_mensaje(_msg("/ayuda"), e)
    assert "/reportar" in e.textos[0][1] and "/consultar" in e.textos[1][1]
    assert len(_reportes()) == antes


def test_bot_reportar_pide_confirmacion_y_guarda_con_si():
    e = Envios()
    antes = len(_reportes())
    bot_telegram.procesar_mensaje(_msg("/reportar"), e)
    bot_telegram.procesar_mensaje(_msg("Llevamos dos días sin agua en Chame"), e)
    assert len(_reportes()) == antes  # aún no se guarda
    assert "SI" in e.textos[-1][1]
    bot_telegram.procesar_mensaje(_msg("SI"), e)
    assert len(_reportes()) == antes + 1
    creado = [r for r in _reportes() if r.get("canal") == "telegram"][-1]
    assert creado["mensaje_original"] == "Llevamos dos días sin agua en Chame"
    assert creado["codigo_seguimiento"] in e.textos[-1][1]


def test_bot_reportar_con_no_cancela_sin_guardar():
    e = Envios()
    antes = len(_reportes())
    for t in ("/reportar", "Sin agua desde hace tres días", "NO"):
        bot_telegram.procesar_mensaje(_msg(t), e)
    assert len(_reportes()) == antes
    assert "cancelado" in e.textos[-1][1]


def test_bot_consultar_con_codigo_existente_y_inexistente():
    e = Envios()
    codigo = asistente_ia.confirmar_reporte("Reporte para consultar por bot")["codigo"]
    bot_telegram.procesar_mensaje(_msg(f"/consultar {codigo}"), e)
    assert codigo in e.textos[-1][1] and "No indicamos hora" in e.textos[-1][1]
    bot_telegram.procesar_mensaje(_msg("/consultar HID-ZZZZZZZZ"), e)
    assert "No encontramos" in e.textos[-1][1]


def test_bot_texto_libre_sin_gemini_usa_reglas_locales():
    e = Envios()
    bot_telegram.procesar_mensaje(_msg("¿Qué significa el estado?"), e)
    assert e.textos and e.textos[-1][1]


def test_bot_rechaza_mensajes_no_textuales_sin_fallar():
    e = Envios()
    bot_telegram.procesar_mensaje({"chat": {"id": 9}, "location": {"latitude": 8.9, "longitude": -79.5}}, e)
    assert "solo puedo leer texto" in e.textos[-1][1]


def test_bot_descripcion_invalida_pide_de_nuevo():
    e = Envios()
    bot_telegram.procesar_mensaje(_msg("/reportar"), e)
    bot_telegram.procesar_mensaje(_msg("corto"), e)
    assert "entre 10 y 1500" in e.textos[-1][1]
