"""
Motor PostgreSQL (Supabase) con psycopg 3. Misma semántica y firmas que el motor SQLite; ver `data.database`.

Esquema híbrido (supabase/migrations): cada tabla guarda el documento completo en `datos jsonb` y columnas tipadas
(claves, estados, FKs, fechas) que ESTE módulo deriva del documento en cada escritura, de modo que nunca divergen.

Diseño para Supabase + funciones serverless:
  * Conexiones CORTAS (una por transacción), sin estado global que importe para la corrección: cada invocación
    puede ser una instancia nueva. Si se necesitara un pool, el del pooler de Supabase (Supavisor/PgBouncer) ya lo es.
  * Compatible con el pooler en MODO TRANSACCIÓN (puerto 6543): `prepare_threshold=None` (nada de sentencias
    preparadas persistentes), sin estado de sesión entre transacciones (todo es `SET LOCAL`/`set_config(..., true)`
    dentro de la transacción) y sin LISTEN/NOTIFY ni cursores con nombre.
  * Consultas SIEMPRE parametrizadas; los nombres de tabla/columna salen de listas blancas internas, nunca de la entrada.
  * SSL obligatorio salvo servidor local; timeouts de conexión y de sentencia/bloqueo/inactividad por transacción.
  * Los errores del driver se traducen a `ErrorBaseDeDatos` SIN texto del driver (que podría incluir servidor,
    usuario o fragmentos de la cadena de conexión). Nunca se registra ni se propaga DATABASE_URL.
"""

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, Iterator, List, Optional

import psycopg
from psycopg import errors as pgerr
from psycopg.conninfo import conninfo_to_dict

import config
from data._comun import (CLAVES, TABLAS, ErrorBaseDeDatos, ErrorConexionBD, ErrorIntegridadBD, EsquemaNoAplicado,
                         clave_de, es_entera, normalizar_clave, validar_tabla, validar_vaciable)

# Constante de la cerradura de negocio ("HIDRO360" en ASCII). Cabe en bigint con signo.
LOCK_NEGOCIO = 0x4849445241333630

CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 15000     # una sentencia que tarde más se cancela (evita colgar una función serverless)
LOCK_TIMEOUT_MS = 10000          # espera máxima por un bloqueo de fila/tabla
IDLE_TX_TIMEOUT_MS = 30000       # una transacción abierta e inactiva se cierra sola (no retiene la cerradura de negocio)

_local = threading.local()       # solo la transacción ABIERTA del hilo; se limpia al terminar (no hay estado entre transacciones)
_HOSTS_LOCALES = {"", "localhost", "127.0.0.1", "::1"}


# ------------------------------------------------------------------------------------------------ errores
def _traducir(e: BaseException) -> ErrorBaseDeDatos:
    """Convierte un error de psycopg en uno propio con mensaje fijo (sin datos del servidor ni de la cadena)."""
    estado = getattr(e, "sqlstate", None) or ""
    if isinstance(e, pgerr.UndefinedTable):
        return EsquemaNoAplicado("Faltan tablas en la base de datos: aplique las migraciones "
                                 "(python -m scripts.aplicar_migraciones).", estado)
    if isinstance(e, pgerr.InsufficientPrivilege):
        return ErrorIntegridadBD("Operación denegada por la base de datos (permisos o tabla de solo anexado).", estado)
    if isinstance(e, pgerr.IntegrityError):
        partes = []
        for atributo in ("table_name", "constraint_name", "column_name"):   # identificadores del esquema, no datos
            try:
                v = getattr(e.diag, atributo) or ""
            except Exception:  # noqa: BLE001
                v = ""
            if re.fullmatch(r"[A-Za-z0-9_]{1,63}", v):
                partes.append(v)
        return ErrorIntegridadBD("Restricción de base de datos violada" + (f" ({'.'.join(partes)})." if partes else "."), estado)
    if isinstance(e, pgerr.QueryCanceled):
        return ErrorBaseDeDatos("La consulta superó el tiempo máximo permitido.", estado)
    if isinstance(e, pgerr.LockNotAvailable):
        return ErrorBaseDeDatos("La base de datos está ocupada (bloqueo no disponible a tiempo).", estado)
    if isinstance(e, (pgerr.SerializationFailure, pgerr.DeadlockDetected)):
        return ErrorBaseDeDatos("Conflicto de concurrencia en la base de datos; reintente la operación.", estado)
    if isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)):
        return ErrorConexionBD("No se pudo conectar con la base de datos o se perdió la conexión "
                               "(red, SSL, credenciales o tiempo agotado).", estado)
    return ErrorBaseDeDatos(f"Error de base de datos ({type(e).__name__}).", estado)


# ------------------------------------------------------------------------------------------------ conexión
def _esquema_prueba() -> Optional[str]:
    """SOLO PRUEBAS: HIDRO360_DB_SCHEMA fija un esquema por prueba (aislamiento). En producción no se define."""
    esq = os.environ.get("HIDRO360_DB_SCHEMA", "").strip()
    if not esq:
        return None
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", esq):
        raise ErrorBaseDeDatos("HIDRO360_DB_SCHEMA no es un nombre de esquema válido.")
    return esq


def _parametros_conexion() -> Dict[str, Any]:
    url = config.DATABASE_URL()
    try:
        p = conninfo_to_dict(url)
    except Exception:  # noqa: BLE001 - el mensaje del driver podría citar la cadena
        raise ErrorConexionBD("DATABASE_URL no es una cadena de conexión PostgreSQL válida.") from None
    host = (p.get("host") or "").split(",")[0]
    local = host in _HOSTS_LOCALES or host.startswith("/")
    if not local and p.get("sslmode", "prefer") in ("disable", "allow", "prefer"):
        p["sslmode"] = "require"          # nunca en claro hacia un servidor remoto
    elif "sslmode" not in p:
        p["sslmode"] = "prefer"           # servidor local de pruebas
    p.setdefault("connect_timeout", CONNECT_TIMEOUT_S)
    p.setdefault("application_name", "hidro360")
    return p


def conectar(autocommit: bool = True) -> "psycopg.Connection":
    """Abre una conexión corta. Para quien necesite una conexión propia (migraciones, scripts)."""
    try:
        return psycopg.connect(**_parametros_conexion(), autocommit=autocommit, prepare_threshold=None)
    except ErrorBaseDeDatos:
        raise
    except psycopg.Error as e:
        raise _traducir(e) from None


def _sentencia_inicio(escritura: bool, bloqueo_negocio: bool) -> str:
    """Una sola ida y vuelta: BEGIN + timeouts locales + esquema de prueba + cerradura. Todo `LOCAL`: desaparece
    al terminar la transacción, así que no deja estado en una conexión del pooler compartida con otros clientes."""
    partes = ["BEGIN" if escritura else "BEGIN READ ONLY",
              f"SELECT set_config('statement_timeout', '{STATEMENT_TIMEOUT_MS}', true), "
              f"set_config('lock_timeout', '{LOCK_TIMEOUT_MS}', true), "
              f"set_config('idle_in_transaction_session_timeout', '{IDLE_TX_TIMEOUT_MS}', true)"]
    esq = _esquema_prueba()
    if esq:
        partes.append(f"SELECT set_config('search_path', '\"{esq}\", public', true)")
    if bloqueo_negocio:
        partes.append(f"SELECT pg_advisory_xact_lock({LOCK_NEGOCIO})")
    return "; ".join(partes)


# Reutilización OPCIONAL de conexiones: una lista corta y acotada de conexiones inactivas. No afecta a la corrección
# (cada transacción empieza con BEGIN y todo su estado es LOCAL; si la conexión envejeció, se reabre y se reintenta).
# HIDRO360_DB_REUSO_S=0 la desactiva (una conexión nueva por transacción); HIDRO360_DB_REUSO_MAX acota las inactivas.
_libres: List[tuple] = []          # (conexion, clave_de_parametros, instante_de_devolucion)
_cerradura_libres = threading.Lock()


def _entero_env(nombre: str, defecto: int) -> int:
    try:
        return max(0, int(os.environ.get(nombre, defecto)))
    except ValueError:
        return defecto


def _clave_parametros() -> str:
    import hashlib
    p = _parametros_conexion()
    return hashlib.sha256(repr(sorted(p.items())).encode()).hexdigest()


def _tomar(clave: str):
    """(conexión, reutilizada). Una conexión reutilizada solo se acepta si es sana, reciente y de los mismos parámetros."""
    reuso = _entero_env("HIDRO360_DB_REUSO_S", 30)
    ahora = time.monotonic()
    while reuso:
        with _cerradura_libres:
            if not _libres:
                break
            con, k, desde = _libres.pop()
        if k == clave and ahora - desde <= reuso and not con.closed                 and con.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
            return con, True
        _cerrar(con)
    return conectar(autocommit=True), False


def _cerrar(con) -> None:
    try:
        con.close()
    except Exception:  # noqa: BLE001
        pass


def _devolver(con, clave: str, sana: bool) -> None:
    if sana and _entero_env("HIDRO360_DB_REUSO_S", 30) and not con.closed             and con.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
        with _cerradura_libres:
            if len(_libres) < _entero_env("HIDRO360_DB_REUSO_MAX", 4):
                _libres.append((con, clave, time.monotonic()))
                return
    _cerrar(con)


def cerrar_conexiones_libres() -> None:
    """Cierra las conexiones inactivas guardadas (pruebas, apagado ordenado)."""
    with _cerradura_libres:
        pendientes, _libres[:] = list(_libres), []
    for con, _, _ in pendientes:
        _cerrar(con)


@contextmanager
def _tx(escritura: bool, bloqueo_negocio: bool) -> Iterator["psycopg.Connection"]:
    activa = getattr(_local, "con", None)
    if activa is not None:           # reentrante: la confirma el contexto externo
        yield activa
        return
    clave = _clave_parametros()
    con, sana = None, False
    try:
        for intento in (1, 2):
            con, reutilizada = _tomar(clave)
            try:
                con.execute(_sentencia_inicio(escritura, bloqueo_negocio))
                break
            except (psycopg.OperationalError, psycopg.InterfaceError):
                _cerrar(con)
                if reutilizada and intento == 1:      # la conexión guardada murió en reposo: una nueva y se reintenta
                    continue
                raise
        _local.con = con
        yield con
        con.execute("COMMIT")
        sana = True
    except BaseException as e:
        try:
            if con is not None and not con.closed and con.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                con.execute("ROLLBACK")
                sana = True
        except Exception:  # noqa: BLE001 - conexión ya perdida: el servidor revierte solo
            sana = False
        if isinstance(e, psycopg.Error):
            raise _traducir(e) from None
        raise
    finally:
        _local.con = None
        if con is not None:
            _devolver(con, clave, sana)


@contextmanager
def adoptar_transaccion(con: "psycopg.Connection") -> Iterator["psycopg.Connection"]:
    """Hace que las funciones de `data.database` y de los servicios (p. ej. audit.verificar_integridad) usen la
    transacción ABIERTA `con` (que el llamador gestiona). Lo usa scripts/migrar_sqlite_a_postgres.py para verificar
    lo cargado antes de confirmar."""
    if getattr(_local, "con", None) is not None:
        raise ErrorBaseDeDatos("Ya hay una transacción activa en este hilo.")
    _local.con = con
    try:
        yield con
    finally:
        _local.con = None


@contextmanager
def transaccion() -> Iterator["psycopg.Connection"]:
    """Agrupa varias operaciones (cambio de negocio + auditoría + traza) en UNA transacción atómica. Reentrante.

    La transacción RAÍZ toma `pg_advisory_xact_lock(LOCK_NEGOCIO)`: es el equivalente de `BEGIN IMMEDIATE` de SQLite.
    Hace falta porque la auditoría encadena hashes (cada entrada incluye el hash de la anterior): quien lee «la última
    entrada» y escribe la siguiente debe estar serializado respecto de los demás escritores, o dos transacciones
    leerían el mismo `hash_previo` y bifurcarían la cadena. La cerradura es de transacción: se libera sola al hacer
    COMMIT/ROLLBACK (también si la conexión muere), funciona con el pooler en modo transacción y no bloquea lecturas.
    (UNIQUE(hash_previo) en `auditoria` es la segunda barrera: una bifurcación fallaría en vez de corromper.)"""
    with _tx(escritura=True, bloqueo_negocio=True) as con:
        yield con


@contextmanager
def conexion(escritura: bool = True) -> Iterator["psycopg.Connection"]:
    """Dentro de `transaccion()` reutiliza la activa; fuera, abre una transacción propia (con la cerradura de negocio
    si escribe, para que una escritura suelta tampoco se solape con la cadena de auditoría)."""
    with _tx(escritura=escritura, bloqueo_negocio=escritura) as con:
        yield con


# ------------------------------------------------------------------------------------------------ zona horaria
@lru_cache(maxsize=8)
def _zona(nombre: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(nombre)
    except Exception:  # noqa: BLE001
        if nombre == "America/Panama":   # sin base tzdata (p. ej. Windows sin el paquete tzdata): UTC-5 fijo, sin DST
            return timezone(timedelta(hours=-5))
        raise ErrorBaseDeDatos("HIDRO360_TZ no es una zona horaria conocida.") from None


def zona_negocio() -> tzinfo:
    """Zona en la que se interpretan las fechas INGENUAS de los modelos (Panamá por defecto)."""
    return _zona(os.environ.get("HIDRO360_TZ", "").strip() or "America/Panama")


def a_timestamptz(v: Any) -> Optional[datetime]:
    """Instante UTC con zona a partir de un valor de documento. Ingenuo => hora local de negocio. Inválido => None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.strip())
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=zona_negocio())
    return v.astimezone(timezone.utc)


# ------------------------------------------------------------------------------------------------ columnas tipadas
def _txt(v):
    if isinstance(v, Enum):
        v = v.value
    return v if isinstance(v, str) else (str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)


def _int(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v if -2**63 <= v < 2**63 else None
    if isinstance(v, float):
        return int(v) if v == int(v) and abs(v) < 2**62 else None
    if isinstance(v, str) and re.fullmatch(r"-?\d{1,18}", v.strip()):
        return int(v)
    return None


def _flt(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _bool(v):
    return v if isinstance(v, bool) else None


_CONV = {"t": (_txt, "%s::text"), "i": (_int, "%s::bigint"), "f": (_flt, "%s::double precision"),
         "b": (_bool, "%s::boolean"), "ts": (a_timestamptz, "%s::timestamptz"),
         # FK condicional: NULL si la fila referida no existe (eventos rechazados por apuntar a una misión inexistente)
         "fkmision": (_int, "(SELECT id FROM misiones WHERE id = %s::bigint)")}

# tabla -> [(columna, tipo, campos candidatos del documento)]. El nombre de columna sale de AQUÍ, no de la entrada.
_COLUMNAS: Dict[str, List[tuple]] = {
    "sectores": [("poblacion_estimada", "i", ("poblacion_estimada",)), ("vulnerabilidad", "t", ("vulnerabilidad",)),
                 ("lat", "f", ("lat",)), ("lng", "f", ("lng",)), ("ficticio", "b", ("ficticio",), True)],
    "cisternas": [("capacidad_litros", "i", ("capacidad_litros",)), ("litros_disponibles", "i", ("litros_disponibles",)),
                  ("estado", "t", ("estado",)), ("sector_base", "t", ("sector_base",)), ("ficticio", "b", ("ficticio",), True)],
    "reportes": [("estado", "t", ("estado",)), ("sector", "t", ("sector",)), ("severidad", "t", ("severidad",)),
                 ("fecha_creacion", "ts", ("fecha_creacion",)), ("codigo_seguimiento", "t", ("codigo_seguimiento",)),
                 ("clave_idempotencia", "t", ("clave_idempotencia",)), ("canal", "t", ("canal",)),
                 ("duplicado_de", "i", ("duplicado_de",)), ("lat", "f", ("lat",)), ("lng", "f", ("lng",)),
                 ("ficticio", "b", ("ficticio",), False)],
    "planes": [("estado", "t", ("estado",)), ("version", "i", ("version",)), ("fecha_creacion", "ts", ("fecha_creacion",)),
               ("metodo", "t", ("metodo",))],
    "decisiones_duplicado": [("reporte_id", "i", ("reporte_id",)), ("otro_id", "i", ("otro_id",)),
                             ("es_duplicado", "b", ("es_duplicado",)), ("responsable", "t", ("responsable",)),
                             ("fecha", "ts", ("fecha",))],
    "auditoria": [("fecha", "ts", ("fecha",)), ("responsable", "t", ("responsable",)), ("accion", "t", ("accion",)),
                  ("entidad", "t", ("entidad",)), ("entidad_id", "t", ("entidad_id",)),
                  ("hash_previo", "t", ("hash_previo",)), ("hash", "t", ("hash",))],
    "usuarios": [("usuario", "t", ("usuario",)), ("rol", "t", ("rol",)), ("activo", "b", ("activo",)),
                 ("creado", "ts", ("creado",))],
    "sesiones": [("usuario", "t", ("usuario",)), ("expira", "ts", ("expira", "expira_en", "expiracion", "expires_at"))],
    "fotos": [("reporte_id", "i", ("reporte_id",)), ("tipo_mime", "t", ("tipo_mime",)), ("bytes", "i", ("bytes",)),
              ("sha256", "t", ("sha256",)), ("fecha", "ts", ("fecha",))],
    "dispositivos": [("cisterna_id", "t", ("cisterna_id",)), ("token_hash", "t", ("token_hash",)),
                     ("activo", "b", ("activo",)), ("ultima_conexion", "ts", ("ultima_conexion",))],
    "misiones": [("cisterna_id", "t", ("cisterna_id",)), ("estado", "t", ("estado",)), ("plan_id", "i", ("plan_id",)),
                 ("recomendacion_id", "i", ("recomendacion_id",)), ("creada", "ts", ("creada",)),
                 ("ficticio", "b", ("ficticio",), False)],
    "eventos_mision": [("mision_id", "fkmision", ("mision_id",)), ("uid", "t", ("uid",)),
                       ("cisterna_id", "t", ("cisterna_id",)), ("tipo", "t", ("tipo",)),
                       ("resultado", "t", ("resultado",)), ("parada_orden", "i", ("parada_orden",)),
                       ("fecha_servidor", "ts", ("fecha_servidor",))],
    "recomendaciones": [("reporte_id", "i", ("reporte_id",)), ("estado", "t", ("estado",)),
                        ("proveedor", "t", ("proveedor",)), ("fecha", "ts", ("fecha",)), ("mision_id", "i", ("mision_id",))],
    "audios": [("reporte_id", "i", ("reporte_id",)), ("tipo_mime", "t", ("tipo_mime",)), ("bytes", "i", ("bytes",)),
               ("sha256", "t", ("sha256",)), ("fecha", "ts", ("fecha",))],
    "telegram_updates": [("chat_id", "t", ("chat_id",)), ("procesado", "ts", ("procesado",))],
    "telegram_sesiones": [("chat_id", "t", ("chat_id",)), ("estado", "t", ("estado",)),
                          ("actualizado", "ts", ("actualizado",))],
}
assert set(_COLUMNAS) == set(CLAVES), "tabla sin columnas definidas"


def _fila(tabla: str, clave: Any, datos: Dict[str, Any]) -> List[tuple]:
    """[(columna, marcador, valor)] sin la clave primaria. Incluye datos (jsonb) y, en auditoría, datos_texto."""
    texto = _dump(datos)
    filas = []
    for col, tipo, campos, *defecto in _COLUMNAS[tabla]:
        conv, marcador = _CONV[tipo]
        valor = None
        for c in campos:
            if datos.get(c) is not None:
                valor = conv(datos.get(c))
                break
        if valor is None and tabla == "dispositivos" and col == "cisterna_id":
            valor = _txt(clave)                  # id del dispositivo == cisterna_id
        if valor is None and defecto:
            valor = defecto[0]                   # columnas NOT NULL con valor por defecto del modelo
        filas.append((col, marcador, valor))
    filas.append(("datos", "%s::jsonb", texto))
    if tabla == "auditoria":
        filas.append(("datos_texto", "%s::text", texto))
    return filas


_NUL = re.compile(r"(?<!\\)(?:\\\\)*\\u0000")


def _dump(d: Dict[str, Any]) -> str:
    texto = json.dumps(d, ensure_ascii=False, default=str)
    if _NUL.search(texto):
        raise ValueError("El documento contiene el carácter NUL (U+0000), que PostgreSQL no admite en jsonb.")
    return texto


def _leer_doc(tabla: str, fila) -> Dict[str, Any]:
    if tabla == "auditoria":
        return json.loads(fila[0])               # texto EXACTO que se hasheó (jsonb normaliza números y orden)
    return fila[0]


def _sel(tabla: str) -> str:
    return "datos_texto" if tabla == "auditoria" else "datos"


def _orden(tabla: str) -> str:
    pk, entera = clave_de(tabla)
    return pk if entera else f'{pk} COLLATE "C"'      # mismo orden binario que SQLite


# ------------------------------------------------------------------------------------------------ API pública
def insertar(tabla: str, datos: Dict[str, Any], clave: Optional[str] = None) -> Any:
    validar_tabla(tabla)
    pk, entera = clave_de(tabla)
    with conexion(True) as con:
        if clave is not None:
            valor = normalizar_clave(tabla, clave)
            if valor is None:
                raise ValueError("Clave no válida para la tabla.")
            cols = _fila(tabla, valor, datos)
            nombres = ", ".join([pk] + [c for c, _, _ in cols])
            marcadores = ", ".join(["%s::" + ("bigint" if entera else "text")] + [m for _, m, _ in cols])
            actualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c, _, _ in cols)
            con.execute(f"INSERT INTO {tabla} ({nombres}) VALUES ({marcadores}) "
                        f"ON CONFLICT ({pk}) DO UPDATE SET {actualiza}", [valor] + [v for _, _, v in cols])
            return clave
        if not entera:
            raise ValueError("Las tablas con clave de texto exigen `clave`.")
        nuevo_id = con.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (tabla,)).fetchone()[0]
        datos = {**datos, "id": nuevo_id}
        cols = _fila(tabla, nuevo_id, datos)
        nombres = ", ".join(["id"] + [c for c, _, _ in cols])
        marcadores = ", ".join(["%s::bigint"] + [m for _, m, _ in cols])
        con.execute(f"INSERT INTO {tabla} ({nombres}) VALUES ({marcadores})", [nuevo_id] + [v for _, _, v in cols])
        return nuevo_id


def insertar_si_nuevo(tabla: str, datos: Dict[str, Any], clave: Any) -> bool:
    """Inserta el documento solo si la clave aún no existe. Devuelve True si insertó, False si ya estaba.
    Atómico (ON CONFLICT DO NOTHING): sirve para idempotencia entre instancias (p. ej. update_id de Telegram)."""
    validar_tabla(tabla)
    pk, entera = clave_de(tabla)
    valor = normalizar_clave(tabla, clave)
    if valor is None:
        raise ValueError("Clave no válida para la tabla.")
    cols = _fila(tabla, valor, datos)
    nombres = ", ".join([pk] + [c for c, _, _ in cols])
    marcadores = ", ".join(["%s::" + ("bigint" if entera else "text")] + [m for _, m, _ in cols])
    with conexion(True) as con:
        cur = con.execute(f"INSERT INTO {tabla} ({nombres}) VALUES ({marcadores}) ON CONFLICT ({pk}) DO NOTHING",
                          [valor] + [v for _, _, v in cols])
        return cur.rowcount == 1


def actualizar(tabla: str, clave: Any, datos: Dict[str, Any]) -> None:
    validar_tabla(tabla)
    pk, entera = clave_de(tabla)
    valor = normalizar_clave(tabla, clave)
    if valor is None:
        return
    cols = _fila(tabla, valor, datos)
    asigna = ", ".join(f"{c} = {m}" for c, m, _ in cols)
    with conexion(True) as con:
        con.execute(f"UPDATE {tabla} SET {asigna} WHERE {pk} = %s::{'bigint' if entera else 'text'}",
                    [v for _, _, v in cols] + [valor])


def obtener(tabla: str, clave: Any) -> Optional[Dict[str, Any]]:
    validar_tabla(tabla)
    pk, entera = clave_de(tabla)
    valor = normalizar_clave(tabla, clave)
    if valor is None:
        return None
    with conexion(False) as con:
        fila = con.execute(f"SELECT {_sel(tabla)} FROM {tabla} WHERE {pk} = %s::{'bigint' if entera else 'text'}",
                           (valor,)).fetchone()
    return _leer_doc(tabla, fila) if fila else None


def listar(tabla: str) -> List[Dict[str, Any]]:
    validar_tabla(tabla)
    with conexion(False) as con:
        filas = con.execute(f"SELECT {_sel(tabla)} FROM {tabla} ORDER BY {_orden(tabla)}").fetchall()
    return [_leer_doc(tabla, f) for f in filas]


def ultimo(tabla: str) -> Optional[Dict[str, Any]]:
    validar_tabla(tabla)
    pk, _ = clave_de(tabla)
    with conexion(False) as con:
        fila = con.execute(f"SELECT {_sel(tabla)} FROM {tabla} ORDER BY {pk} DESC LIMIT 1").fetchone()
    return _leer_doc(tabla, fila) if fila else None


def borrar(tabla: str, clave: Any) -> int:
    validar_tabla(tabla)
    pk, entera = clave_de(tabla)
    valor = normalizar_clave(tabla, clave)
    if valor is None:
        return 0
    with conexion(True) as con:
        return con.execute(f"DELETE FROM {tabla} WHERE {pk} = %s::{'bigint' if entera else 'text'}", (valor,)).rowcount


def vaciar(tablas: Optional[List[str]] = None) -> None:
    """Borra todas las filas de las tablas dadas (por defecto, las de la demo) y reinicia sus contadores.
    `auditoria` es append-only: solo se vacía fuera de producción y abriendo la excepción local del trigger."""
    lista = tablas or TABLAS
    for t in lista:
        validar_vaciable(t)
    if "auditoria" in lista and config.ENTORNO() == "prod":
        raise ErrorBaseDeDatos("La auditoría no se puede vaciar en producción.")
    with conexion(True) as con:
        for t in lista:
            if t == "auditoria":
                con.execute("SELECT set_config('hidro360.permitir_reinicio', 'si', true)")
            con.execute(f"DELETE FROM {t}")
            if t == "auditoria":
                con.execute("SELECT set_config('hidro360.permitir_reinicio', '', true)")
            if es_entera(t):
                con.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), 1, false)", (t,))


def registrar_evento_limite(clave: str, ventana_s: int) -> int:
    """Alta atómica de un evento y recuento en la ventana (incluido este). Se serializa POR CLAVE con una cerradura
    de transacción (no con la de negocio), de modo que dos instancias que reciben el mismo evento a la vez cuentan
    de forma exacta y las claves distintas no se esperan entre sí."""
    with _tx(escritura=True, bloqueo_negocio=False) as con:
        con.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("limite:" + clave,))
        con.execute("INSERT INTO limites (clave) VALUES (%s)", (clave,))
        return con.execute("SELECT count(*) FROM limites WHERE clave = %s "
                           "AND instante > clock_timestamp() - make_interval(secs => %s::double precision)",
                           (clave, float(ventana_s))).fetchone()[0]


def limpiar_limites(antes_s: int = 86400) -> int:
    with _tx(escritura=True, bloqueo_negocio=False) as con:
        return con.execute("DELETE FROM limites WHERE instante < clock_timestamp() - make_interval(secs => %s::double precision)",
                           (float(antes_s),)).rowcount
