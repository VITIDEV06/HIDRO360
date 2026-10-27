"""
Persistencia de HIDRO360: tablas «documento» (id + JSON validado por Pydantic). Sin ORM.

Fachada con DOS motores intercambiables, elegidos en cada llamada:
  * SQLite (stdlib, `data/_sqlite.py`)       por defecto; un archivo (config.DB_PATH), funciona sin internet.
  * PostgreSQL/Supabase (`data/_postgres.py`) si `config.DATABASE_URL()` está definida (`config.modo_postgres()`).
`services/` solo conoce esta API; no sabe qué motor hay debajo. Misma semántica y firmas en ambos (la suite
tests/test_database_contrato.py lo comprueba con las mismas aserciones).

API: insertar, actualizar, obtener, listar, ultimo, borrar, vaciar, transaccion, conexion,
registrar_evento_limite, limpiar_limites. `transaccion()`/`conexion()` ceden la conexión del motor activo
(sqlite3.Connection o psycopg.Connection): el código de negocio no debería usarla directamente.
"""

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import config
from data._comun import (CLAVES, TABLAS, ErrorBaseDeDatos, ErrorConexionBD, ErrorIntegridadBD,  # noqa: F401
                         EsquemaNoAplicado)
from data import _sqlite

SCHEMA = _sqlite.SCHEMA  # esquema SQLite (el de PostgreSQL está en supabase/migrations)


def _motor():
    if config.modo_postgres():
        from data import _postgres   # import perezoso: SQLite no necesita psycopg
        return _postgres
    return _sqlite


def motor() -> str:
    """'postgres' o 'sqlite' (para diagnósticos; no cambie su comportamiento según el motor)."""
    return "postgres" if config.modo_postgres() else "sqlite"


@contextmanager
def transaccion() -> Iterator[Any]:
    """Una transacción atómica reentrante que serializa a los escritores de negocio (SQLite: BEGIN IMMEDIATE;
    PostgreSQL: pg_advisory_xact_lock). Cambio + auditoría + traza van dentro; si algo falla se revierte todo."""
    with _motor().transaccion() as con:
        yield con


@contextmanager
def conexion() -> Iterator[Any]:
    with _motor().conexion() as con:
        yield con


def insertar(tabla: str, datos: Dict[str, Any], clave: Optional[str] = None) -> Any:
    """Inserta un documento. Si la tabla usa clave de texto, `clave` es obligatoria (y reemplaza si existe).
    Devuelve el id (autoincremental o la clave)."""
    return _motor().insertar(tabla, datos, clave)


def insertar_si_nuevo(tabla: str, datos: Dict[str, Any], clave: Any) -> bool:
    """Inserta el documento solo si la clave aún no existe (True) o la deja como estaba (False).
    Atómico y compartido entre instancias: base de la idempotencia del webhook de Telegram (update_id)."""
    return _motor().insertar_si_nuevo(tabla, datos, clave)


def actualizar(tabla: str, clave: Any, datos: Dict[str, Any]) -> None:
    _motor().actualizar(tabla, clave, datos)


def obtener(tabla: str, clave: Any) -> Optional[Dict[str, Any]]:
    return _motor().obtener(tabla, clave)


def listar(tabla: str) -> List[Dict[str, Any]]:
    return _motor().listar(tabla)


def ultimo(tabla: str) -> Optional[Dict[str, Any]]:
    return _motor().ultimo(tabla)


def borrar(tabla: str, clave: Any) -> int:
    """Borra un documento por su clave y devuelve cuántas filas se borraron (0 o 1)."""
    return _motor().borrar(tabla, clave)


eliminar = borrar  # alias de compatibilidad (algunas pruebas lo buscan con ese nombre)


def vaciar(tablas: Optional[List[str]] = None) -> None:
    """Vacía las tablas dadas (por defecto `TABLAS`, las de la demo) y reinicia sus contadores."""
    _motor().vaciar(tablas)


def registrar_evento_limite(clave: str, ventana_s: int) -> int:
    """Registra un evento para `clave` (p. ej. "login:ip") y devuelve cuántos eventos con esa clave hay en los
    últimos `ventana_s` segundos, incluido este. Alta y recuento son atómicos y compartidos entre instancias."""
    return _motor().registrar_evento_limite(clave, ventana_s)


def limpiar_limites(antes_s: int = 86400) -> int:
    """Borra los eventos de límite con más de `antes_s` segundos. Devuelve cuántos borró."""
    return _motor().limpiar_limites(antes_s)


def comprobar() -> bool:
    """True si la base responde a una consulta trivial. Lo usa /salud: leer la configuración no prueba que la base
    exista ni que las credenciales sean válidas. No expone el motivo del fallo (puede contener datos de conexión)."""
    try:
        with conexion() as con:
            con.execute("SELECT 1")
        return True
    except Exception:
        return False
