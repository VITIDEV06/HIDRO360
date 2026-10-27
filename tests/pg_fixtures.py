"""
Fixtures para probar la capa de datos contra un PostgreSQL REAL pero desechable (ver scripts/postgres_prueba.py).

Reglas de seguridad:
  * Solo se usa TEST_DATABASE_URL. Nunca DATABASE_URL real: la fixture `bd_temporal` de conftest la borra del entorno y
    aquí se vuelve a fijar, con monkeypatch, apuntando a TEST_DATABASE_URL.
  * Cada prueba trabaja en su propio ESQUEMA (`t_<uuid>`), creado con las migraciones reales y eliminado al terminar.
  * Se niega a correr contra una URL que no sea local, salvo HIDRO360_PERMITIR_PG_REMOTO_EN_PRUEBAS=1.
"""

import os
import uuid

import pytest


def _url_prueba() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL no definida (ver scripts/postgres_prueba.py)")
    from psycopg.conninfo import conninfo_to_dict
    host = (conninfo_to_dict(url).get("host") or "").split(",")[0]
    if host not in ("", "localhost", "127.0.0.1", "::1") and os.environ.get("HIDRO360_PERMITIR_PG_REMOTO_EN_PRUEBAS") != "1":
        pytest.skip("TEST_DATABASE_URL apunta a un servidor no local: las pruebas destructivas se niegan")
    return url


@pytest.fixture
def pg_url():
    return _url_prueba()


@pytest.fixture
def pg_esquema(pg_url, monkeypatch):
    """Esquema aislado con las migraciones aplicadas; deja DATABASE_URL/HIDRO360_DB_SCHEMA apuntando a él."""
    import psycopg
    from data import migraciones
    esquema = "t_" + uuid.uuid4().hex[:16]
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("HIDRO360_DB_SCHEMA", esquema)
    migraciones.aplicar(esquema)
    try:
        yield esquema
    finally:
        # Antes de borrar el esquema: el teardown de conftest (auth.reiniciar_estado) aún usa la base activa.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("HIDRO360_DB_SCHEMA", raising=False)
        with psycopg.connect(pg_url, autocommit=True) as con:
            con.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')


@pytest.fixture
def pg_admin(pg_url, pg_esquema):
    """Conexión superusuaria (dueña) sobre el esquema de la prueba, para inspeccionar columnas y probar roles."""
    import psycopg
    con = psycopg.connect(pg_url, autocommit=True)
    con.execute(f'SET search_path TO "{pg_esquema}", public')
    try:
        yield con
    finally:
        con.close()
