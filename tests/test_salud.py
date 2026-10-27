"""/api/v1/salud comprueba la base de verdad: con la base caída responde 503 y no declara persistencia funcional."""

import pytest
from fastapi.testclient import TestClient

from api.main import app
from data import database as db


@pytest.fixture
def c():
    return TestClient(app)


def test_salud_con_base_disponible(c):
    r = c.get("/api/v1/salud")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ok"] is True
    assert cuerpo["persistencia"] == "sqlite"  # las pruebas usan SQLite temporal


def test_salud_con_base_caida_no_declara_persistencia(c, monkeypatch):
    def caida():
        raise RuntimeError("detalle interno que no debe salir")
    monkeypatch.setattr(db, "conexion", caida)
    r = c.get("/api/v1/salud")
    assert r.status_code == 503
    cuerpo = r.json()
    assert cuerpo["ok"] is False
    assert cuerpo["persistencia"] == "no_disponible"
    assert "detalle interno" not in r.text


def test_produccion_sin_database_url_no_arranca(monkeypatch):
    import config
    monkeypatch.setenv("HIDRO360_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.exigir_persistencia_produccion()


def test_desarrollo_sin_database_url_usa_sqlite(monkeypatch):
    import config
    monkeypatch.setenv("HIDRO360_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", "")
    config.exigir_persistencia_produccion()  # no debe lanzar
