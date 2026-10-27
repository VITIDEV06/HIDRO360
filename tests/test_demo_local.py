"""Modo demostración local: SQLite explícito, asistente por reglas y sin dependencias externas."""

import pytest

import config
from interface.flet_app.portal import asistente


def test_modo_sqlite_gana_a_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/postgres")
    monkeypatch.setenv("HIDRO360_BD", "sqlite")
    assert config.modo_postgres() is False


def test_sin_modo_sqlite_database_url_activa_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/postgres")
    monkeypatch.delenv("HIDRO360_BD", raising=False)
    assert config.modo_postgres() is True
    monkeypatch.delenv("DATABASE_URL")  # antes de la limpieza de conftest, que no debe conectar a Postgres


@pytest.mark.parametrize("pregunta,tema", [
    ("¿Cómo reporto la falta de agua?", "reportar"),
    ("quiero consultar mi código HID-AB12CD34", "consultar"),
    ("¿Qué significa el estado En atención?", "estados"),
    ("¿Qué información debo dar?", "informacion"),
    ("ya llevo días y el problema continúa", "continua"),
    ("¿A qué hora llega el agua?", "hora"),
])
def test_asistente_responde_preguntas_frecuentes(pregunta, tema):
    detectado, texto = asistente.responder(pregunta)
    assert detectado == tema
    assert texto == asistente.TEMAS[tema][1]


def test_asistente_no_inventa_cuando_no_entiende():
    tema, texto = asistente.responder("xyzzy 123")
    assert tema is None
    assert "No entendí" in texto


def test_asistente_no_promete_hora_ni_envios():
    for _, (_, texto) in asistente.TEMAS.items():
        assert "enviamos" not in texto.lower()
        assert "enviado" not in texto.lower()
