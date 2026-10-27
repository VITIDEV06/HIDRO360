"""
Motor SQLite (stdlib): el valor por defecto. Sin ORM: tablas «documento» (id + JSON validado por Pydantic).

Cada función abre una conexión corta; la base es un solo archivo (config.DB_PATH),
fácil de copiar a una Raspberry Pi. No lo importe directamente: use `data.database`, que elige el motor.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import config
from data._comun import TABLAS, columna_clave, validar_tabla, validar_vaciable

SCHEMA = """
CREATE TABLE IF NOT EXISTS sectores (
    nombre TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cisternas (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS planes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisiones_duplicado (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fotos (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispositivos (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS misiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eventos_mision (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recomendaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audios (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_updates (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_sesiones (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sesiones (
    id TEXT PRIMARY KEY, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS limites (
    id INTEGER PRIMARY KEY AUTOINCREMENT, clave TEXT NOT NULL, instante REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_limites_clave_instante ON limites (clave, instante);
"""

_local = threading.local()


def _abrir() -> sqlite3.Connection:
    con = sqlite3.connect(str(config.DB_PATH), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def transaccion() -> Iterator[sqlite3.Connection]:
    """Agrupa varias operaciones (cambio de negocio + auditoría) en UNA transacción atómica.
    BEGIN IMMEDIATE serializa escritores; si algo falla se revierte todo. Es reentrante."""
    if getattr(_local, "con", None) is not None:
        yield _local.con
        return
    con = _abrir()
    _local.con = con
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        _local.con = None
        con.close()


@contextmanager
def conexion() -> Iterator[sqlite3.Connection]:
    activa = getattr(_local, "con", None)
    if activa is not None:  # dentro de transaccion(): la confirma el contexto externo
        yield activa
        return
    con = _abrir()
    try:
        con.execute("BEGIN")
        yield con
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def _dump(d: Dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False, default=str)


def insertar(tabla: str, datos: Dict[str, Any], clave: Optional[str] = None) -> Any:
    """Inserta un documento. Si la tabla usa clave de texto, `clave` es obligatoria.
    Devuelve el id (autoincremental o la clave)."""
    validar_tabla(tabla)
    with conexion() as con:
        if clave is not None:
            col = columna_clave(tabla)
            con.execute(f"INSERT OR REPLACE INTO {tabla} ({col}, datos) VALUES (?, ?)", (clave, _dump(datos)))
            return clave
        cur = con.execute(f"INSERT INTO {tabla} (datos) VALUES (?)", ("{}",))
        nuevo_id = cur.lastrowid
        datos = {**datos, "id": nuevo_id}
        con.execute(f"UPDATE {tabla} SET datos = ? WHERE id = ?", (_dump(datos), nuevo_id))
        return nuevo_id


def insertar_si_nuevo(tabla: str, datos: Dict[str, Any], clave: Any) -> bool:
    """Inserta el documento solo si la clave aún no existe. Devuelve True si insertó, False si ya estaba.
    Atómico (INSERT OR IGNORE): sirve para idempotencia entre instancias (p. ej. update_id de Telegram)."""
    validar_tabla(tabla)
    col = columna_clave(tabla)
    with conexion() as con:
        cur = con.execute(f"INSERT OR IGNORE INTO {tabla} ({col}, datos) VALUES (?, ?)", (clave, _dump(datos)))
        return cur.rowcount == 1


def actualizar(tabla: str, clave: Any, datos: Dict[str, Any]) -> None:
    validar_tabla(tabla)
    col = columna_clave(tabla)
    with conexion() as con:
        con.execute(f"UPDATE {tabla} SET datos = ? WHERE {col} = ?", (_dump(datos), clave))


def obtener(tabla: str, clave: Any) -> Optional[Dict[str, Any]]:
    validar_tabla(tabla)
    col = columna_clave(tabla)
    with conexion() as con:
        fila = con.execute(f"SELECT datos FROM {tabla} WHERE {col} = ?", (clave,)).fetchone()
    return json.loads(fila["datos"]) if fila else None


def listar(tabla: str) -> List[Dict[str, Any]]:
    validar_tabla(tabla)
    orden = columna_clave(tabla)
    with conexion() as con:
        filas = con.execute(f"SELECT datos FROM {tabla} ORDER BY {orden}").fetchall()
    return [json.loads(f["datos"]) for f in filas]


def vaciar(tablas: Optional[List[str]] = None) -> None:
    with conexion() as con:
        for t in tablas or TABLAS:
            validar_vaciable(t)
            con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))


def ultimo(tabla: str) -> Optional[Dict[str, Any]]:
    validar_tabla(tabla)
    with conexion() as con:
        fila = con.execute(f"SELECT datos FROM {tabla} ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(fila["datos"]) if fila else None


def borrar(tabla: str, clave: Any) -> int:
    """Borra un documento por su clave y devuelve cuántas filas se borraron (0 o 1)."""
    validar_tabla(tabla)
    col = columna_clave(tabla)
    with conexion() as con:
        return con.execute(f"DELETE FROM {tabla} WHERE {col} = ?", (clave,)).rowcount


def registrar_evento_limite(clave: str, ventana_s: int) -> int:
    """Registra un evento para `clave` (p. ej. "login:ip") y devuelve cuántos eventos con esa clave hay en los
    últimos `ventana_s` segundos, incluido este. Alta y recuento ocurren en una misma transacción, de modo que
    varias instancias del servidor comparten el límite (a diferencia de un diccionario en memoria).
    Si se llama dentro de `transaccion()` se une a ella (y se revierte con ella)."""
    ahora = time.time()
    with conexion() as con:
        con.execute("INSERT INTO limites (clave, instante) VALUES (?, ?)", (clave, ahora))
        return con.execute("SELECT COUNT(*) FROM limites WHERE clave = ? AND instante > ?",
                           (clave, ahora - ventana_s)).fetchone()[0]


def limpiar_limites(antes_s: int = 86400) -> int:
    """Borra los eventos de límite con más de `antes_s` segundos. Devuelve cuántos borró."""
    with conexion() as con:
        return con.execute("DELETE FROM limites WHERE instante < ?", (time.time() - antes_s,)).rowcount
