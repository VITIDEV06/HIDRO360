"""Webhook de Telegram: secreto, texto, ubicacion y adjuntos. Telegram se SIMULA (no hay llamadas reales a la API)."""

import struct
import zlib

import pytest
from fastapi.testclient import TestClient

import config
from api.main import app
from data import database as db
from services import telegram_api

SECRETO = "secreto-de-prueba-telegram-123"
CABECERA = {"X-Telegram-Bot-Api-Secret-Token": SECRETO}


@pytest.fixture
def envios(monkeypatch):
    enviados = []
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", lambda: SECRETO)
    monkeypatch.setattr(telegram_api, "enviar_mensaje",
                        lambda chat, texto, reply_markup=None: enviados.append((chat, texto)) or True)
    return enviados


@pytest.fixture
def c():
    return TestClient(app)


def _de_telegram():
    """La base de prueba trae datos de demostración (autouse en conftest): filtramos solo lo que llega por Telegram."""
    return [r for r in db.listar("reportes") if r.get("canal") == "telegram"]


def _png_valido() -> bytes:
    def fragmento(tipo, datos):
        c = struct.pack(">I", len(datos)) + tipo + datos
        return c + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
    cabecera = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return cabecera + fragmento(b"IHDR", ihdr) + fragmento(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + fragmento(b"IEND", b"")


def _mensaje(**campos):
    base = {"message": {"message_id": 1, "chat": {"id": 555}, "from": {"id": 9}}}
    base["message"].update(campos)
    return base


def test_sin_secreto_o_con_secreto_erroneo_no_procesa(c, envios):
    assert c.post("/api/v1/telegram/webhook", json=_mensaje(text="sin agua")).status_code == 403
    r = c.post("/api/v1/telegram/webhook", json=_mensaje(text="sin agua"),
               headers={"X-Telegram-Bot-Api-Secret-Token": "malo"})
    assert r.status_code == 403
    assert envios == []
    assert _de_telegram() == []


def test_sin_secreto_configurado_responde_503(c, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", lambda: "")
    assert c.post("/api/v1/telegram/webhook", json=_mensaje(text="x"), headers=CABECERA).status_code == 503


def test_texto_crea_reporte_con_canal_telegram_y_responde_codigo(c, envios):
    r = c.post("/api/v1/telegram/webhook", json=_mensaje(text="Sin agua desde hace 2 días en Chame"), headers=CABECERA)
    assert r.status_code == 200
    mios = _de_telegram()
    assert len(mios) == 1
    assert mios[0]["mensaje_original"] == "Sin agua desde hace 2 días en Chame"
    assert envios and envios[0][0] == 555
    assert mios[0]["codigo_seguimiento"] in envios[0][1]


def test_ubicacion_crea_reporte_con_coordenadas(c, envios):
    c.post("/api/v1/telegram/webhook", json=_mensaje(location={"latitude": 8.88, "longitude": -79.78}), headers=CABECERA)
    rep = _de_telegram()[0]
    assert (rep["lat"], rep["lng"]) == (8.88, -79.78)


def test_mensaje_vacio_pide_contenido_sin_crear_reporte(c, envios):
    c.post("/api/v1/telegram/webhook", json=_mensaje(sticker={"file_id": "x"}), headers=CABECERA)
    assert _de_telegram() == []
    assert "Escriba el problema" in envios[0][1]


def test_foto_y_voz_se_adjuntan_al_reporte(c, envios, monkeypatch):
    png = _png_valido()
    ogg = b"OggS" + b"\x00" * 64

    def descargar(file_id, max_bytes):
        return png if file_id == "foto" else ogg
    monkeypatch.setattr(telegram_api, "descargar_archivo", descargar)
    c.post("/api/v1/telegram/webhook", json=_mensaje(
        caption="Tubería rota en la calle principal",
        photo=[{"file_id": "foto", "file_size": 100}],
        voice={"file_id": "voz", "duration": 4}), headers=CABECERA)
    rep = _de_telegram()[0]
    assert rep["mensaje_original"] == "Tubería rota en la calle principal"
    assert len(rep.get("foto_ids", [])) == 1
    assert "Código de seguimiento" in envios[-1][1]
