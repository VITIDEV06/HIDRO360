"""
Pruebas específicas del motor PostgreSQL (esquema híbrido, FKs, zona horaria, RLS, errores sin secretos, migraciones).

Se omiten sin TEST_DATABASE_URL (PostgreSQL local desechable: scripts/postgres_prueba.py). NO prueban Supabase real:
los roles anon/authenticated/service_role se imitan con supabase/tests/00_roles_supabase_simulados.sql.
Las pruebas «puras» (sin servidor) corren siempre.
"""

import threading
import traceback

import pytest

from data import _postgres, database as db, migraciones
from data._comun import ErrorBaseDeDatos, ErrorConexionBD, ErrorIntegridadBD
from services import audit
from tests.pg_fixtures import pg_admin, pg_esquema, pg_url  # noqa: F401


def _reporte(**k):
    return {"mensaje_original": "sin agua", "fecha_creacion": "2026-03-01T10:30:00", "estado": "por_revisar",
            "sector": "Chame", **k}


def _cisterna(i="C-1"):
    db.insertar("cisternas", {"id": i, "capacidad_litros": 100, "litros_disponibles": 100, "estado": "disponible"}, clave=i)


@pytest.fixture
def url_falsa(monkeypatch):
    """Permite fijar DATABASE_URL dentro de la prueba y la retira ANTES del teardown de conftest (que usa la base activa)."""
    try:
        yield monkeypatch
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("HIDRO360_DB_SCHEMA", raising=False)


# ===================================================================== sin servidor
def test_ssl_se_exige_fuera_de_localhost(url_falsa):
    monkeypatch = url_falsa
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.ejemplo.supabase.co:6543/postgres?sslmode=disable")
    assert _postgres._parametros_conexion()["sslmode"] == "require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.ejemplo.supabase.co:6543/postgres")
    assert _postgres._parametros_conexion()["sslmode"] == "require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.ejemplo.supabase.co/postgres?sslmode=verify-full")
    assert _postgres._parametros_conexion()["sslmode"] == "verify-full"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/x")
    assert _postgres._parametros_conexion()["sslmode"] == "prefer"        # servidor local de pruebas
    assert _postgres._parametros_conexion()["connect_timeout"] > 0


def test_cadena_invalida_no_se_filtra(url_falsa):
    monkeypatch = url_falsa
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:SECRETO123@[::1:bad")
    with pytest.raises(ErrorConexionBD) as e:
        db.obtener("reportes", 1)
    assert "SECRETO123" not in str(e.value) and "usuario" not in str(e.value)


def test_error_de_conexion_no_filtra_credenciales_ni_servidor(url_falsa):
    monkeypatch = url_falsa
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario_x:SECRETO123@127.0.0.1:1/base_x?connect_timeout=2")
    with pytest.raises(ErrorConexionBD) as e:
        db.listar("reportes")
    texto = "".join(traceback.format_exception(e.type, e.value, e.tb)) + repr(e.value) + str(e.value)
    for prohibido in ("SECRETO123", "usuario_x", "base_x", "127.0.0.1"):
        assert prohibido not in texto


def test_nombres_de_tabla_con_lista_blanca():
    from data._comun import CLAVES
    for t in CLAVES:
        assert t.isidentifier()
    with pytest.raises(ValueError):
        _postgres.obtener("reportes; select 1", 1)


def test_conversion_de_zona_horaria_sin_servidor(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.delenv("HIDRO360_TZ", raising=False)
    assert _postgres.a_timestamptz("2026-03-01T10:30:00") == datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)   # Panamá UTC-5
    assert _postgres.a_timestamptz("2026-03-01T10:30:00-05:00") == datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)
    assert _postgres.a_timestamptz("2026-03-01T15:30:00Z") == datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)
    assert _postgres.a_timestamptz("no es fecha") is None and _postgres.a_timestamptz(None) is None
    monkeypatch.setenv("HIDRO360_TZ", "UTC")
    assert _postgres.a_timestamptz("2026-03-01T10:30:00") == datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc)


def test_caracter_nul_se_rechaza_con_mensaje_claro():
    with pytest.raises(ValueError, match="NUL"):
        _postgres._dump({"mensaje_original": "hola\x00mundo"})
    assert "literal" in _postgres._dump({"mensaje_original": "barra \\u0000 literal"})   # la barra literal no es NUL


# ===================================================================== columnas tipadas
def test_columnas_tipadas_se_derivan_del_documento(pg_esquema, pg_admin):
    rid = db.insertar("reportes", _reporte(codigo_seguimiento="HID-AAAA0001", severidad="alta", lat=8.9, lng=-79.7,
                                           clave_idempotencia="k1", canal="web", ficticio=True))
    fila = pg_admin.execute("SELECT estado, sector, severidad, fecha_creacion AT TIME ZONE 'UTC', codigo_seguimiento, "
                            "lat, lng, canal, ficticio FROM reportes WHERE id = %s", (rid,)).fetchone()
    assert fila[:3] == ("por_revisar", "Chame", "alta")
    assert str(fila[3]) == "2026-03-01 15:30:00"                   # 10:30 en Panamá = 15:30 UTC
    assert fila[4:] == ("HID-AAAA0001", 8.9, -79.7, "web", True)
    db.actualizar("reportes", rid, {**db.obtener("reportes", rid), "estado": "confirmado", "sector": "Arraiján"})
    assert pg_admin.execute("SELECT estado, sector FROM reportes WHERE id = %s", (rid,)).fetchone() == ("confirmado", "Arraiján")
    assert pg_admin.execute("SELECT datos->>'estado' FROM reportes WHERE id = %s", (rid,)).fetchone()[0] == "confirmado"


def test_ida_y_vuelta_de_fechas_sin_perdida(pg_esquema, pg_admin, monkeypatch):
    for texto in ("2026-03-01T10:30:00", "2026-12-31T23:59:59", "2026-03-01T10:30:00.123456", "2026-03-01T10:30:00-05:00"):
        rid = db.insertar("reportes", _reporte(fecha_creacion=texto))
        assert db.obtener("reportes", rid)["fecha_creacion"] == texto          # el documento conserva el texto exacto
    # el instante guardado es el mismo con o sin desplazamiento explícito
    a = db.insertar("reportes", _reporte(fecha_creacion="2026-03-01T10:30:00"))
    b = db.insertar("reportes", _reporte(fecha_creacion="2026-03-01T10:30:00-05:00"))
    assert pg_admin.execute("SELECT (SELECT fecha_creacion FROM reportes WHERE id=%s) = "
                            "(SELECT fecha_creacion FROM reportes WHERE id=%s)", (a, b)).fetchone()[0]
    # y una zona de negocio distinta cambia la interpretación del ingenuo
    monkeypatch.setenv("HIDRO360_TZ", "UTC")
    c = db.insertar("reportes", _reporte(fecha_creacion="2026-03-01T10:30:00"))
    assert str(pg_admin.execute("SELECT fecha_creacion AT TIME ZONE 'UTC' FROM reportes WHERE id=%s", (c,)).fetchone()[0]) \
        == "2026-03-01 10:30:00"


def test_fecha_invalida_deja_columna_nula_pero_conserva_el_documento(pg_esquema, pg_admin):
    rid = db.insertar("reportes", _reporte(fecha_creacion="ayer por la tarde"))
    assert pg_admin.execute("SELECT fecha_creacion FROM reportes WHERE id=%s", (rid,)).fetchone()[0] is None
    assert db.obtener("reportes", rid)["fecha_creacion"] == "ayer por la tarde"


# ===================================================================== restricciones
def test_unicos_y_checks(pg_esquema):
    db.insertar("reportes", _reporte(codigo_seguimiento="HID-UNICO001"))
    with pytest.raises(ErrorIntegridadBD):
        db.insertar("reportes", _reporte(codigo_seguimiento="HID-UNICO001"))
    db.insertar("reportes", _reporte())                       # sin código (reportes antiguos): varios NULL permitidos
    db.insertar("reportes", _reporte())
    with pytest.raises(ErrorIntegridadBD):
        db.insertar("reportes", _reporte(estado="inventado"))
    with pytest.raises(ErrorIntegridadBD):
        db.insertar("cisternas", {"id": "X", "capacidad_litros": -5}, clave="X")
    _cisterna("C-1")
    _cisterna("C-2")
    db.insertar("dispositivos", {"cisterna_id": "C-1", "token_hash": "h" * 64, "activo": True}, clave="C-1")
    with pytest.raises(ErrorIntegridadBD):
        db.insertar("dispositivos", {"cisterna_id": "C-2", "token_hash": "h" * 64, "activo": True}, clave="C-2")


def test_fks_y_cascadas(pg_esquema, pg_admin):
    _cisterna("C-1")
    with pytest.raises(ErrorIntegridadBD):                      # misión sin cisterna
        db.insertar("misiones", {"cisterna_id": "NO-EXISTE", "estado": "asignada", "paradas": []})
    mid = db.insertar("misiones", {"cisterna_id": "C-1", "estado": "asignada", "paradas": []})
    db.insertar("eventos_mision", {"uid": "evento-0001", "mision_id": mid, "resultado": "aplicado", "tipo": "llegada",
                                   "parada_orden": 1, "cisterna_id": "C-1"})
    # evento rechazado por apuntar a una misión inexistente: se registra (mision_id NULL), el número queda en el documento
    eid = db.insertar("eventos_mision", {"uid": "evento-0002", "mision_id": 999, "resultado": "rechazado", "tipo": "llegada",
                                         "parada_orden": 1, "cisterna_id": "C-1"})
    assert pg_admin.execute("SELECT mision_id FROM eventos_mision WHERE id=%s", (eid,)).fetchone()[0] is None
    assert db.obtener("eventos_mision", eid)["mision_id"] == 999
    # uid: un evento APLICADO no se repite; los rechazados sí pueden repetirse con el mismo uid
    with pytest.raises(ErrorIntegridadBD):
        db.insertar("eventos_mision", {"uid": "evento-0001", "mision_id": mid, "resultado": "aplicado", "tipo": "llegada"})
    db.insertar("eventos_mision", {"uid": "evento-0002", "mision_id": 999, "resultado": "rechazado", "tipo": "llegada"})
    # recomendación exige reporte
    with pytest.raises(ErrorIntegridadBD):
        db.insertar("recomendaciones", {"reporte_id": 404, "estado": "propuesta", "proveedor": "reglas", "etapas": []})
    rid = db.insertar("reportes", _reporte())
    rec = db.insertar("recomendaciones", {"reporte_id": rid, "estado": "propuesta", "proveedor": "reglas", "etapas": []})
    # cascadas: borrar la cisterna borra sus misiones y los eventos ligados; borrar el reporte borra su recomendación
    db.borrar("reportes", rid)
    assert db.obtener("recomendaciones", rec) is None
    db.vaciar(["cisternas"])
    assert db.listar("misiones") == []
    assert [e["uid"] for e in db.listar("eventos_mision")] == ["evento-0002", "evento-0002"]   # los huérfanos rechazados quedan


def test_recargar_demo_conserva_dispositivos_usuarios_y_fotos(pg_esquema):
    """Equivale a cargar_datos_demo: vaciar() + reinsertar catálogos dentro de UNA transacción."""
    _cisterna("C-1")
    db.insertar("dispositivos", {"cisterna_id": "C-1", "token_hash": "a" * 64, "activo": True}, clave="C-1")
    db.insertar("usuarios", {"usuario": "ana", "rol": "administrador", "activo": True}, clave="ana")
    db.insertar("fotos", {"id": "f1", "reporte_id": 7, "tipo_mime": "image/png", "bytes": 3, "sha256": "x"}, clave="f1")
    db.insertar("misiones", {"cisterna_id": "C-1", "estado": "asignada", "paradas": []})
    with db.transaccion():
        db.vaciar()
        _cisterna("C-1")
    assert db.obtener("dispositivos", "C-1") and db.obtener("usuarios", "ana") and db.obtener("fotos", "f1")
    assert db.listar("misiones") == []
    with pytest.raises(ErrorIntegridadBD):                      # una terminal no puede quedar apuntando a una cisterna inexistente
        with db.transaccion():
            db.vaciar()
    assert db.obtener("cisternas", "C-1")                       # y la transacción fallida se revirtió entera


# ===================================================================== auditoría
def test_auditoria_inmutable_en_la_base(pg_esquema, pg_admin):
    audit.registrar("ana", "crear", "reporte", 1)
    e = db.listar("auditoria")[0]
    with pytest.raises(ErrorIntegridadBD):
        db.actualizar("auditoria", e["id"], {**e, "responsable": "intruso"})
    with pytest.raises(ErrorIntegridadBD):
        db.borrar("auditoria", e["id"])
    with pytest.raises(Exception, match="append-only"):
        pg_admin.execute("UPDATE auditoria SET responsable = 'x'")          # ni siquiera el dueño
    with pytest.raises(Exception, match="append-only"):
        pg_admin.execute("TRUNCATE auditoria")
    assert db.listar("auditoria")[0]["responsable"] == "ana"
    assert audit.verificar_integridad()


def test_vaciar_auditoria_solo_fuera_de_produccion(pg_esquema, monkeypatch):
    audit.registrar("ana", "crear", "reporte", 1)
    monkeypatch.setenv("HIDRO360_ENV", "prod")
    with pytest.raises(ErrorBaseDeDatos):
        db.vaciar(["auditoria"])
    assert len(db.listar("auditoria")) == 1
    monkeypatch.setenv("HIDRO360_ENV", "test")
    db.vaciar(["auditoria"])
    assert db.listar("auditoria") == []
    audit.registrar("ana", "crear", "reporte", 1)                          # la cadena vuelve a empezar desde génesis
    assert audit.verificar_integridad() and db.listar("auditoria")[0]["id"] == 1


def test_hashes_sobreviven_al_ida_y_vuelta_aunque_jsonb_normalice(pg_esquema, pg_admin):
    # números que jsonb reescribe (1e22, 1.0, 0.1+0.2) y claves en distinto orden: la cadena se sigue verificando
    audit.registrar("ana", "a", "x", 1, detalle={"z": 1e22, "a": 1.0, "m": 0.1 + 0.2, "txt": "ñ😀", "anidado": {"b": 2, "a": [1.50, 2]}})
    audit.registrar("ana", "b", "x", 2, detalle={"n": 123456789012345678})
    assert audit.verificar_integridad()
    jsonb = pg_admin.execute("SELECT datos->'detalle'->>'z' FROM auditoria ORDER BY id LIMIT 1").fetchone()[0]
    assert jsonb != repr(1e22)            # jsonb sí normalizó: por eso la lectura usa datos_texto
    assert db.listar("auditoria")[0]["detalle"]["z"] == 1e22


def test_escritura_concurrente_fuera_de_transaccion_explicita_no_bifurca(pg_esquema):
    errores = []

    def trabajo(n):
        try:
            for i in range(5):
                audit.registrar(f"h{n}", "a", "x", i)          # sin db.transaccion(): cada insertar() toma la cerradura
        except BaseException as e:  # noqa: BLE001
            errores.append(e)

    ts = [threading.Thread(target=trabajo, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    # leer-último y escribir son transacciones SEPARADAS aquí: puede haber carreras, pero UNIQUE(hash_previo) impide
    # corromper la cadena (el perdedor falla con ErrorIntegridadBD en vez de bifurcarla). Lo que NO debe pasar es una cadena inválida.
    assert all(isinstance(e, ErrorIntegridadBD) for e in errores)
    assert audit.verificar_integridad()


# ===================================================================== pooler / serverless
def test_sin_sentencias_preparadas_ni_estado_de_sesion(pg_esquema):
    for i in range(12):                                         # más que cualquier prepare_threshold por defecto (5)
        db.obtener("reportes", i)
    with db.transaccion() as con:
        assert con.execute("SELECT count(*) FROM pg_prepared_statements").fetchone()[0] == 0
        assert con.prepare_threshold is None
        assert con.execute("SHOW statement_timeout").fetchone()[0] == "15s"
        assert con.execute("SHOW lock_timeout").fetchone()[0] == "10s"
    # tras COMMIT la configuración LOCAL desaparece: una conexión del pooler no hereda nada
    inicio = _postgres._sentencia_inicio(True, True)
    con = _postgres.conectar()
    try:
        con.execute(inicio)
        con.execute("COMMIT")
        assert con.execute("SHOW statement_timeout").fetchone()[0] in ("0", "0ms")
        assert con.execute("SHOW search_path").fetchone()[0].startswith('"$user"')
    finally:
        con.close()


def test_statement_timeout_cancela_consultas_lentas(pg_esquema, monkeypatch):
    monkeypatch.setattr(_postgres, "STATEMENT_TIMEOUT_MS", 300)
    with pytest.raises(ErrorBaseDeDatos, match="tiempo máximo"):
        with db.transaccion() as con:
            con.execute("SELECT pg_sleep(3)")
    assert db.listar("reportes") == []                          # y la conexión sigue siendo utilizable


def test_conexion_reutilizada_que_murio_se_reabre(pg_esquema, pg_url, monkeypatch):
    monkeypatch.setenv("HIDRO360_DB_REUSO_S", "60")
    _postgres.cerrar_conexiones_libres()
    db.insertar("reportes", _reporte())
    assert _postgres._libres, "la conexión debería haberse guardado"
    import psycopg
    with psycopg.connect(pg_url, autocommit=True) as admin:     # mata las conexiones inactivas del esquema de prueba
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
                      "AND datname = current_database() AND state = 'idle' AND application_name = 'hidro360'")
    assert len(db.listar("reportes")) == 1                       # se reabre sola y reintenta
    _postgres.cerrar_conexiones_libres()


# ===================================================================== migraciones
def test_migraciones_idempotentes_y_con_checksum(pg_esquema, tmp_path):
    assert migraciones.aplicar(pg_esquema) == []                # ya aplicadas por la fixture
    copia = tmp_path / "m"
    copia.mkdir()
    for p in migraciones.archivos():
        (copia / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    primero = sorted(copia.glob("*.sql"))[0]
    primero.write_text(primero.read_text(encoding="utf-8") + "\n-- cambio\n", encoding="utf-8")
    with pytest.raises(ErrorBaseDeDatos, match="cambió"):
        migraciones.aplicar(pg_esquema, directorio=copia)


def test_todos_los_archivos_sql_son_reaplicables(pg_esquema, pg_admin):
    """Idempotencia real del SQL (sin el control de versiones): ejecutarlos otra vez no falla ni duplica."""
    for p in migraciones.archivos():
        pg_admin.execute(p.read_text(encoding="utf-8"))
    assert pg_admin.execute("SELECT count(*) FROM pg_policies WHERE schemaname = current_schema()").fetchone()[0] >= 14


def test_sin_migraciones_el_error_es_claro(pg_url, url_falsa):
    monkeypatch = url_falsa
    from data._comun import EsquemaNoAplicado
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("HIDRO360_DB_SCHEMA", "no_existe_xyz")
    with pytest.raises(EsquemaNoAplicado, match="migraciones"):
        db.listar("reportes")


# ===================================================================== RLS y roles
TABLAS = ["sectores", "cisternas", "reportes", "planes", "decisiones_duplicado", "auditoria", "usuarios", "sesiones",
          "limites", "fotos", "audios", "telegram_updates", "telegram_sesiones", "dispositivos", "misiones",
          "eventos_mision", "recomendaciones", "schema_migraciones"]


def _como(con, rol, sql, params=None):
    """Ejecuta `sql` como `rol` (SET ROLE) y devuelve filas, o la excepción si fue denegado."""
    con.execute(f"SET ROLE {rol}")
    try:
        cur = con.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount
    except Exception as e:  # noqa: BLE001
        return e
    finally:
        con.execute("RESET ROLE")


def test_rls_activada_en_todas_las_tablas(pg_esquema, pg_admin):
    filas = pg_admin.execute("SELECT relname, relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                             "WHERE n.nspname = current_schema() AND c.relkind = 'r'").fetchall()
    assert {r for r, _ in filas} == set(TABLAS)
    assert all(activa for _, activa in filas), [r for r, a in filas if not a]


@pytest.mark.parametrize("rol", ["anon", "authenticated"])
def test_roles_de_la_api_publica_no_leen_ni_escriben(pg_esquema, pg_admin, rol):
    db.insertar("reportes", _reporte())
    pg_admin.execute(f'GRANT USAGE ON SCHEMA "{pg_esquema}" TO anon, authenticated')     # como en `public` de Supabase
    for t in TABLAS:
        r = _como(pg_admin, rol, f"SELECT * FROM {t}")
        assert isinstance(r, Exception) and "permission denied" in str(r), (t, r)
        w = _como(pg_admin, rol, f"DELETE FROM {t}")
        assert isinstance(w, Exception) and "permission denied" in str(w), (t, w)
    w = _como(pg_admin, rol, "INSERT INTO reportes (datos) VALUES ('{}'::jsonb)")
    assert isinstance(w, Exception)
    s = _como(pg_admin, rol, "SELECT nextval(pg_get_serial_sequence('reportes','id'))")
    assert isinstance(s, Exception)
    assert len(db.listar("reportes")) == 1                         # nada cambió


@pytest.mark.parametrize("rol", ["anon", "authenticated"])
def test_rls_niega_incluso_si_alguien_concede_privilegios(pg_esquema, pg_admin, rol):
    """Segunda barrera: aunque un GRANT erróneo devolviera privilegios, RLS (sin políticas) sigue negando."""
    db.insertar("reportes", _reporte())
    pg_admin.execute(f'GRANT USAGE ON SCHEMA "{pg_esquema}" TO {rol}')
    pg_admin.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON reportes, auditoria TO {rol}")
    assert _como(pg_admin, rol, "SELECT * FROM reportes") == []                      # 0 filas visibles
    assert _como(pg_admin, rol, "UPDATE reportes SET estado = 'rechazado'") == 0     # 0 filas afectadas
    assert _como(pg_admin, rol, "DELETE FROM reportes") == 0
    w = _como(pg_admin, rol, "INSERT INTO reportes (datos) VALUES ('{}'::jsonb)")
    assert isinstance(w, Exception) and "row-level security" in str(w)
    assert db.obtener("reportes", 1)["estado"] == "por_revisar"


def test_no_hay_politicas_para_anon_ni_authenticated(pg_esquema, pg_admin):
    filas = pg_admin.execute("SELECT tablename, policyname, roles FROM pg_policies WHERE schemaname = current_schema()").fetchall()
    for _, _, roles in filas:
        assert not ({"anon", "authenticated", "public"} & set(roles)), filas
    assert not pg_admin.execute("SELECT 1 FROM information_schema.role_table_grants WHERE table_schema = current_schema() "
                                "AND grantee IN ('anon', 'authenticated', 'PUBLIC') LIMIT 1").fetchone()


def test_el_rol_de_la_aplicacion_funciona_pero_no_toca_la_auditoria(pg_esquema, pg_admin, pg_url, monkeypatch):
    """El backend conectado como `hidro360_app` (privilegio mínimo, sujeto a RLS) opera con normalidad."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    pg_admin.execute("ALTER ROLE hidro360_app LOGIN")
    try:
        d = conninfo_to_dict(pg_url)
        d["user"] = "hidro360_app"
        monkeypatch.setenv("DATABASE_URL", make_conninfo(**d).replace(" ", "&") if False else
                           f"postgresql://hidro360_app@{d['host']}:{d['port']}/{d['dbname']}")
        _postgres.cerrar_conexiones_libres()
        rid = db.insertar("reportes", _reporte())
        db.actualizar("reportes", rid, {**db.obtener("reportes", rid), "estado": "confirmado"})
        _cisterna("C-1")
        with db.transaccion():
            db.insertar("misiones", {"cisterna_id": "C-1", "estado": "asignada", "paradas": []})
            audit.registrar("ana", "crear", "reporte", rid)
        audit.registrar("ana", "crear", "reporte", rid)
        assert audit.verificar_integridad() and len(db.listar("auditoria")) == 2
        assert db.registrar_evento_limite("k", 60) == 1
        db.vaciar(["reportes", "misiones", "cisternas"])                       # el reinicio de datos operativos sí
        e = db.listar("auditoria")[0]
        for operacion in (lambda: db.actualizar("auditoria", e["id"], {**e, "responsable": "x"}),
                          lambda: db.borrar("auditoria", e["id"]),
                          lambda: db.vaciar(["auditoria"])):
            with pytest.raises(ErrorIntegridadBD):
                operacion()
        assert audit.verificar_integridad() and len(db.listar("auditoria")) == 2
    finally:
        _postgres.cerrar_conexiones_libres()
        pg_admin.execute("ALTER ROLE hidro360_app NOLOGIN")


def test_el_rol_de_la_aplicacion_no_puede_alterar_el_esquema(pg_esquema, pg_admin):
    pg_admin.execute(f'GRANT USAGE ON SCHEMA "{pg_esquema}" TO hidro360_app')
    for sql in ("DROP TABLE reportes", "ALTER TABLE reportes DISABLE ROW LEVEL SECURITY", "TRUNCATE auditoria",
                "ALTER TABLE auditoria DISABLE TRIGGER auditoria_sin_modificar", "CREATE TABLE x (a int)"):
        r = _como(pg_admin, "hidro360_app", sql)
        assert isinstance(r, Exception), sql


def test_service_role_y_dueno_si_acceden(pg_esquema, pg_admin):
    """El rol del backend en Supabase (dueño `postgres` o `service_role`, BYPASSRLS) lee y escribe."""
    db.insertar("reportes", _reporte())
    pg_admin.execute(f'GRANT USAGE ON SCHEMA "{pg_esquema}" TO service_role')
    pg_admin.execute("GRANT SELECT ON reportes TO service_role")
    assert len(_como(pg_admin, "service_role", "SELECT * FROM reportes")) == 1


def test_contrasena_no_se_guarda_en_texto_plano_en_postgres(pg_esquema, pg_admin):
    """Equivalente PostgreSQL de tests/test_auth.py::test_contrasena_no_se_guarda_en_texto_plano."""
    from services import auth
    auth.crear_usuario("jefa", "Clave-Secreta-123", auth.Rol.ADMIN)
    fila = pg_admin.execute("SELECT datos::text, rol, usuario FROM usuarios WHERE id = 'jefa'").fetchone()
    assert "Clave-Secreta-123" not in fila[0] and fila[1] == "administrador" and fila[2] == "jefa"


# ===================================================================== migración SQLite -> PostgreSQL
@pytest.fixture
def origen_sqlite(tmp_path):
    """Copia (API de backup) de la base SQLite de la prueba, con un reporte REAL y una cuenta extra. Se crea ANTES
    de activar PostgreSQL (la fixture pg_esquema debe pedirse después)."""
    import sqlite3
    from services import auth, workflow
    auth.crear_usuario("jefa", "Clave-Muy-Segura-123", auth.Rol.ADMIN)
    workflow.crear_reporte("Llevamos 2 días sin agua en Chame, calle 5. Hay un bebé.", origen="1.2.3.4")
    db.insertar("fotos", {"id": "ab12cd34ef56", "reporte_id": 1, "tipo_mime": "image/png", "bytes": 10, "sha256": "0" * 64,
                          "fecha": "2026-03-01T10:00:00"}, clave="ab12cd34ef56")
    import config
    destino = tmp_path / "origen.db"
    s, d = sqlite3.connect(str(config.DB_PATH)), sqlite3.connect(str(destino))
    s.backup(d)
    s.close(), d.close()
    return destino


def _conteos(pg_admin):
    return {t: pg_admin.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("sectores", "cisternas", "reportes", "auditoria", "usuarios", "fotos")}


def _sqlite_conteos(ruta):
    import sqlite3
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("sectores", "cisternas", "reportes", "auditoria", "usuarios", "fotos")}
    finally:
        con.close()


def test_migracion_dry_run_ensayo_y_aplicar(origen_sqlite, pg_esquema, pg_admin, tmp_path, capsys):
    from scripts import migrar_sqlite_a_postgres as mig
    import hashlib
    antes = hashlib.sha256(origen_sqlite.read_bytes()).hexdigest()
    esperado = _sqlite_conteos(origen_sqlite)
    assert esperado["reportes"] >= 20 and esperado["usuarios"] == 1
    cero = {t: 0 for t in esperado}

    assert mig.main(["--origen", str(origen_sqlite)]) == 0                       # dry-run por defecto
    assert _conteos(pg_admin) == cero
    assert mig.main(["--origen", str(origen_sqlite), "--ensayo"]) == 0           # carga y revierte
    assert _conteos(pg_admin) == cero
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar", "--backup-dir", str(tmp_path / "resp")]) == 0
    assert _conteos(pg_admin) == esperado
    assert list((tmp_path / "resp").glob("*.db"))                                # copia de seguridad hecha
    assert hashlib.sha256(origen_sqlite.read_bytes()).hexdigest() == antes       # el origen no se tocó

    # IDs, hashes y cadena con las funciones de la aplicación (ahora sobre PostgreSQL)
    import sqlite3
    o = sqlite3.connect(f"file:{origen_sqlite.as_posix()}?mode=ro", uri=True)
    try:
        crudo = [r[0] for r in o.execute("SELECT datos FROM auditoria ORDER BY id")]
        ids = [r[0] for r in o.execute("SELECT id FROM reportes ORDER BY id")]
    finally:
        o.close()
    destino = [r[0] for r in pg_admin.execute("SELECT datos_texto FROM auditoria ORDER BY id")]
    assert destino == crudo                                                      # byte a byte
    assert [r["id"] for r in db.listar("reportes")] == ids
    assert audit.verificar_integridad()
    assert db.insertar("reportes", _reporte()) == max(ids) + 1                   # IDENTITY ajustada
    audit.registrar("ana", "post-migracion", "sistema", 1)                       # la cadena continúa desde el último hash
    assert audit.verificar_integridad()

    # reintentos: rechazado sin --reanudar; idempotente con --reanudar
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar"]) == 1
    capsys.readouterr()


def test_migracion_reanudar_es_idempotente(origen_sqlite, pg_esquema, pg_admin):
    from scripts import migrar_sqlite_a_postgres as mig
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar"]) == 0
    primero = _conteos(pg_admin)
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar", "--reanudar"]) == 0
    assert _conteos(pg_admin) == primero and audit.verificar_integridad()


def test_migracion_solo_reales(origen_sqlite, pg_esquema, pg_admin):
    from scripts import migrar_sqlite_a_postgres as mig
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar", "--solo-reales"]) == 0
    c = _conteos(pg_admin)
    assert c["sectores"] == 0 and c["cisternas"] == 0 and c["reportes"] == 1        # solo el reporte real
    assert c["auditoria"] == _sqlite_conteos(origen_sqlite)["auditoria"]            # auditoría completa: la cadena no se parte
    assert audit.verificar_integridad()
    assert pg_admin.execute("SELECT count(*) FROM reportes WHERE ficticio").fetchone()[0] == 0


def test_migracion_rechaza_cadena_rota_en_el_origen(origen_sqlite, pg_esquema, pg_admin):
    import sqlite3
    from scripts import migrar_sqlite_a_postgres as mig
    con = sqlite3.connect(str(origen_sqlite))
    fila = con.execute("SELECT id, datos FROM auditoria ORDER BY id LIMIT 1").fetchone()
    con.execute("UPDATE auditoria SET datos = ? WHERE id = ?", (fila[1].replace('"responsable": "', '"responsable": "X'), fila[0]))
    con.commit()
    con.close()
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar"]) == 1
    assert _conteos(pg_admin)["reportes"] == 0                                       # no se escribió nada


def test_migracion_con_discrepancia_revierte_todo(origen_sqlite, pg_esquema, pg_admin, monkeypatch):
    from scripts import migrar_sqlite_a_postgres as mig
    original = mig.cargar

    def cargar_incompleto(con, tabla, filas):
        return original(con, tabla, filas[:-1] if tabla == "reportes" else filas)     # «pierde» un reporte

    monkeypatch.setattr(mig, "cargar", cargar_incompleto)
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar"]) == 1
    assert all(v == 0 for v in _conteos(pg_admin).values())                          # ROLLBACK total


def test_migracion_se_niega_sin_database_url(origen_sqlite, monkeypatch):
    from scripts import migrar_sqlite_a_postgres as mig
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert mig.main(["--origen", str(origen_sqlite), "--aplicar"]) == 2
