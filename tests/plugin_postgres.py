"""
Plugin de pytest para ejecutar TODA la suite del proyecto contra PostgreSQL en lugar de SQLite, sin tocar conftest:

    $env:TEST_DATABASE_URL = '<url de scripts/postgres_prueba.py>'
    .venv\\Scripts\\python -m pytest -p tests.plugin_postgres -q

Tras la fixture `bd_temporal` (que aísla SQLite y borra DATABASE_URL) fija DATABASE_URL = TEST_DATABASE_URL con un
esquema propio por prueba, aplica las migraciones y recarga los datos de ejemplo. Sin TEST_DATABASE_URL no hace nada.
"""

import os
import uuid

import pytest

from tests.pg_fixtures import _url_prueba, pg_admin, pg_esquema, pg_url  # noqa: F401 - se reexportan como fixtures


# Pruebas que abren el archivo SQLite con sqlite3: no tienen sentido contra PostgreSQL.
SOLO_SQLITE = {"test_contrasena_no_se_guarda_en_texto_plano"}


@pytest.fixture(autouse=True)
def bd_en_postgres(request, bd_temporal, monkeypatch):
    if not os.environ.get("TEST_DATABASE_URL", "").strip():
        yield
        return
    if request.node.get_closest_marker("sin_postgres") or "test_datos_postgres" in request.node.nodeid \
            or "test_database_contrato" in request.node.nodeid:
        yield                     # estas gestionan su propio motor/esquema
        return
    if request.node.name in SOLO_SQLITE:
        pytest.skip("inspecciona el archivo SQLite directamente (equivalente PostgreSQL en test_datos_postgres.py)")
    import psycopg
    from data import migraciones
    from services import workflow
    url = _url_prueba()
    esquema = "t_" + uuid.uuid4().hex[:16]
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("HIDRO360_DB_SCHEMA", esquema)
    migraciones.aplicar(esquema)
    try:
        workflow.cargar_datos_demo("test")
        yield
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)     # el teardown de conftest vuelve a SQLite
        monkeypatch.delenv("HIDRO360_DB_SCHEMA", raising=False)
        with psycopg.connect(url, autocommit=True) as con:
            con.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')
