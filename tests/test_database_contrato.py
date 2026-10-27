"""
Contrato de la capa de datos: las MISMAS aserciones sobre SQLite y sobre PostgreSQL.
La variante PostgreSQL se omite si no hay TEST_DATABASE_URL (ver scripts/postgres_prueba.py).
"""

import threading
import time

import pytest

from data import database as db
from services import audit
from tests.pg_fixtures import pg_esquema, pg_url  # noqa: F401


@pytest.fixture(params=["sqlite", "postgres"])
def motor(request):
    """Activa el motor pedido. SQLite: la base temporal que ya aísla conftest. PostgreSQL: un esquema por prueba."""
    if request.param == "postgres":
        request.getfixturevalue("pg_esquema")
    assert db.motor() == request.param
    # conftest precarga la demo en SQLite: se parte de tablas vacías en ambos motores para aserciones idénticas
    db.vaciar(["dispositivos", "fotos", "audios", "telegram_updates", "telegram_sesiones", "usuarios", "sesiones", "limites"])
    db.vaciar()
    return request.param


def _reporte(**k):
    return {"mensaje_original": "sin agua en Chame", "fecha_creacion": "2026-03-01T10:30:00", "estado": "por_revisar",
            "sector": "Chame", **k}


def test_insertar_autoincremental_y_obtener(motor):
    a = db.insertar("reportes", _reporte())
    b = db.insertar("reportes", _reporte(mensaje_original="otro"))
    assert (a, b) == (1, 2)
    assert db.obtener("reportes", a)["id"] == 1
    assert db.obtener("reportes", b)["mensaje_original"] == "otro"
    assert db.obtener("reportes", 999) is None


def test_insertar_con_clave_reemplaza(motor):
    db.insertar("cisternas", {"id": "C-1", "capacidad_litros": 100, "litros_disponibles": 50, "estado": "disponible"}, clave="C-1")
    db.insertar("cisternas", {"id": "C-1", "capacidad_litros": 100, "litros_disponibles": 10, "estado": "disponible"}, clave="C-1")
    assert [c["litros_disponibles"] for c in db.listar("cisternas")] == [10]
    assert db.obtener("cisternas", "C-1")["litros_disponibles"] == 10


def test_listar_orden_estable(motor):
    for n in ("Zeta", "alfa", "Beta"):
        db.insertar("sectores", {"nombre": n, "poblacion_estimada": 1}, clave=n)
    assert [s["nombre"] for s in db.listar("sectores")] == ["Beta", "Zeta", "alfa"]   # orden binario en ambos motores
    ids = [db.insertar("planes", {"estado": "propuesto"}) for _ in range(3)]
    assert [p["id"] for p in db.listar("planes")] == ids == [1, 2, 3]


def test_actualizar_y_ultimo(motor):
    rid = db.insertar("reportes", _reporte())
    db.actualizar("reportes", rid, {**db.obtener("reportes", rid), "estado": "confirmado"})
    assert db.obtener("reportes", rid)["estado"] == "confirmado"
    db.actualizar("reportes", 12345, _reporte())                 # no crea filas
    assert len(db.listar("reportes")) == 1
    db.insertar("reportes", _reporte(mensaje_original="segundo"))
    assert db.ultimo("reportes")["mensaje_original"] == "segundo"
    assert db.ultimo("planes") is None


def test_borrar(motor):
    db.insertar("sesiones", {"usuario": "ana"}, clave="h1")
    assert db.borrar("sesiones", "h1") == 1
    assert db.borrar("sesiones", "h1") == 0
    assert db.obtener("sesiones", "h1") is None


def test_vaciar_reinicia_ids_y_respeta_tablas_operativas(motor):
    db.insertar("reportes", _reporte())
    db.insertar("reportes", _reporte())
    db.insertar("usuarios", {"usuario": "ana", "rol": "administrador"}, clave="ana")
    db.insertar("fotos", {"id": "f1", "tipo_mime": "image/png", "bytes": 3, "sha256": "x"}, clave="f1")
    db.insertar("audios", {"id": "a1", "tipo_mime": "audio/ogg", "bytes": 4, "sha256": "y", "transcripcion": None}, clave="a1")
    db.insertar("sesiones", {"usuario": "ana"}, clave="hs")
    db.vaciar(["reportes"])
    assert db.listar("reportes") == []
    assert db.insertar("reportes", _reporte()) == 1               # contador reiniciado
    db.vaciar()
    assert db.listar("reportes") == []
    assert db.obtener("usuarios", "ana") and db.obtener("fotos", "f1") and db.obtener("audios", "a1") and db.obtener("sesiones", "hs")


def test_transaccion_confirma_y_revierte(motor):
    with db.transaccion():
        db.insertar("reportes", _reporte())
        db.insertar("reportes", _reporte())
    assert len(db.listar("reportes")) == 2
    with pytest.raises(RuntimeError):
        with db.transaccion():
            db.insertar("reportes", _reporte(mensaje_original="se pierde"))
            raise RuntimeError("fallo")
    assert [r["mensaje_original"] for r in db.listar("reportes")] == ["sin agua en Chame"] * 2


def test_transaccion_es_reentrante(motor):
    with db.transaccion() as externa:
        with db.transaccion() as interna:
            assert interna is externa
            db.insertar("reportes", _reporte())
        with db.conexion() as c:
            assert c is externa
        assert db.listar("reportes")[0]["id"] == 1               # la escritura es visible dentro de la misma transacción
    assert len(db.listar("reportes")) == 1


def test_cambio_y_auditoria_son_atomicos(motor):
    with pytest.raises(RuntimeError):
        with db.transaccion():
            db.insertar("reportes", _reporte())
            audit.registrar("ana", "crear", "reporte", 1)
            raise RuntimeError("fallo")
    assert db.listar("reportes") == [] and db.listar("auditoria") == []
    with db.transaccion():
        db.insertar("reportes", _reporte())
        audit.registrar("ana", "crear", "reporte", 1)
    assert len(db.listar("auditoria")) == 1 and audit.verificar_integridad()


def test_cadena_de_auditoria_con_escritores_concurrentes(motor):
    hilos, por_hilo, errores = 6, 8, []

    def trabajo(n):
        try:
            for i in range(por_hilo):
                with db.transaccion():
                    audit.registrar(f"h{n}", "accion", "reporte", i, detalle={"i": i, "x": 0.1 * i})
        except BaseException as e:  # noqa: BLE001
            errores.append(e)

    ts = [threading.Thread(target=trabajo, args=(n,)) for n in range(hilos)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errores, errores
    entradas = db.listar("auditoria")
    assert len(entradas) == hilos * por_hilo
    assert len({e["hash_previo"] for e in entradas}) == len(entradas)    # sin bifurcaciones
    assert audit.verificar_integridad()


def test_documentos_se_leen_igual_que_se_escribieron(motor):
    doc = {"mensaje_original": "Sin agua en Arraiján — ¡ñandú! 😀", "fecha_creacion": "2026-03-01T10:30:00",
           "evidencia": {"sector": "Arraiján"}, "personas_afectadas": 40, "horas_sin_agua": 2.5, "vacio": None,
           "lista": [1, "dos", {"tres": 3.0}], "bool": True, "id": 0}
    rid = db.insertar("reportes", doc)
    leido = db.obtener("reportes", rid)
    assert leido == {**doc, "id": rid}
    assert leido["fecha_creacion"] == "2026-03-01T10:30:00"        # el texto de la fecha no cambia
    assert isinstance(leido["horas_sin_agua"], float) and isinstance(leido["personas_afectadas"], int)


def test_tabla_no_permitida_se_rechaza(motor):
    for malo in ("reportes; DROP TABLE reportes", "sqlite_master", "pg_catalog.pg_class", ""):
        with pytest.raises(ValueError):
            db.obtener(malo, 1)
        with pytest.raises(ValueError):
            db.insertar(malo, {})
    assert db.listar("reportes") == []


def test_limite_cuenta_en_la_ventana(motor):
    assert [db.registrar_evento_limite("login:1.2.3.4", 60) for _ in range(3)] == [1, 2, 3]
    assert db.registrar_evento_limite("login:otra", 60) == 1       # claves independientes
    time.sleep(1.2)
    assert db.registrar_evento_limite("login:1.2.3.4", 1) == 1     # los anteriores salieron de la ventana de 1 s
    assert db.registrar_evento_limite("login:1.2.3.4", 60) == 5     # ... pero siguen en la de 60 s
    assert db.limpiar_limites(3600) == 0
    assert db.limpiar_limites(0) >= 5


def test_limite_es_exacto_con_concurrencia(motor):
    cuentas, errores = [], []

    def evento():
        try:
            cuentas.append(db.registrar_evento_limite("conc", 60))
        except BaseException as e:  # noqa: BLE001
            errores.append(e)

    ts = [threading.Thread(target=evento) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errores, errores
    assert sorted(cuentas) == list(range(1, 13))
