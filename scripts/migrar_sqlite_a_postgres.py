"""
Migra una base SQLite de HIDRO360 (agua_clara.db) a PostgreSQL/Supabase preservando IDs, fotos (metadatos) y la
cadena de hashes de auditoría BYTE A BYTE (no se recalcula nada).

Seguridad:
  * El ORIGEN se abre en solo lectura (uri mode=ro) y NUNCA se modifica; se hace una copia de seguridad previa.
  * El DESTINO sale de DATABASE_URL (entorno o .env), jamás de un argumento (no queda en el historial de la shell).
  * Por defecto NO escribe: `--dry-run` analiza el origen y examina el destino en solo lectura.
  * `--aplicar` carga todo en UNA transacción, verifica dentro de ella y solo entonces confirma; cualquier
    discrepancia o error = ROLLBACK (el destino queda como estaba). `--ensayo` hace lo mismo y SIEMPRE revierte.
  * Se niega a escribir en un destino con datos salvo `--reanudar`.

Uso (PowerShell):
    $env:DATABASE_URL = '<cadena del pooler/conexión directa>'
    python -m scripts.migrar_sqlite_a_postgres --origen agua_clara.db                    # análisis (no escribe)
    python -m scripts.migrar_sqlite_a_postgres --origen agua_clara.db --ensayo           # carga + verificación + ROLLBACK
    python -m scripts.migrar_sqlite_a_postgres --origen agua_clara.db --aplicar --backup-dir respaldos

Requisito: las migraciones de supabase/migrations ya aplicadas en el destino (python -m scripts.aplicar_migraciones).
Códigos de salida: 0 todo correcto · 1 discrepancias/rechazado · 2 error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from data import _postgres
from data._comun import CLAVES, ErrorBaseDeDatos

# Orden de dependencias (los padres antes que los hijos). `sesiones` y `limites` NO se migran: son efímeras.
ORDEN = ["sectores", "cisternas", "reportes", "planes", "decisiones_duplicado", "auditoria", "usuarios", "fotos",
         "dispositivos", "misiones", "eventos_mision", "recomendaciones"]


class Informe:
    def __init__(self) -> None:
        self.lineas: List[str] = []
        self.discrepancias: List[str] = []
        self.avisos: List[str] = []
        self.datos: Dict[str, Any] = {}

    def p(self, texto: str = "") -> None:
        self.lineas.append(texto)
        print(texto)

    def error(self, texto: str) -> None:
        self.discrepancias.append(texto)
        self.p(f"  [DISCREPANCIA] {texto}")

    def aviso(self, texto: str) -> None:
        self.avisos.append(texto)
        self.p(f"  [aviso] {texto}")


# ------------------------------------------------------------------------------------------------ origen
def abrir_origen(ruta: Path) -> sqlite3.Connection:
    """Solo lectura: `mode=ro` impide hasta crear el archivo; SQLite no escribe nada."""
    if not ruta.exists():
        raise SystemExit(f"No existe el origen: {ruta}")
    con = sqlite3.connect(f"file:{ruta.resolve().as_posix()}?mode=ro", uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def respaldar(origen: sqlite3.Connection, ruta_origen: Path, destino_dir: Path, inf: Informe) -> Path:
    """Copia consistente con la API de backup de SQLite (no toca el original) y verificación SHA-256 de la copia."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    copia = destino_dir / f"{ruta_origen.stem}_{datetime.now():%Y%m%d_%H%M%S}.db"
    dest = sqlite3.connect(str(copia))
    try:
        origen.backup(dest)
    finally:
        dest.close()
    chk = sqlite3.connect(f"file:{copia.as_posix()}?mode=ro", uri=True)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        chk.close()
    inf.p(f"  copia de seguridad: {copia}  sha256={sha256_archivo(copia)[:16]}…  integridad={'ok' if ok else 'FALLA'}")
    if not ok:
        raise SystemExit("La copia de seguridad no pasa integrity_check: se aborta.")
    return copia


def leer_tablas(con: sqlite3.Connection, inf: Informe) -> Tuple[Dict[str, List[Tuple[Any, str]]], Dict[str, int]]:
    """{tabla: [(clave, texto_json_original)]} con el JSON CRUDO (para preservarlo exactamente)."""
    existentes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    filas: Dict[str, List[Tuple[Any, str]]] = {}
    for t in ORDEN:
        if t not in existentes:
            inf.aviso(f"el origen no tiene la tabla `{t}` (base de una versión anterior): se trata como vacía")
            filas[t] = []
            continue
        pk = CLAVES[t][0]
        filas[t] = [(r[0], r[1]) for r in con.execute(f"SELECT {pk}, datos FROM {t} ORDER BY {pk}")]
    return filas, {t: len(v) for t, v in filas.items()}


MODELOS = {"sectores": "Sector", "cisternas": "Cisterna", "reportes": "Reporte", "planes": "PropuestaPlan",
           "auditoria": "EntradaAuditoria", "fotos": "Foto", "misiones": "Mision", "dispositivos": "Dispositivo",
           "eventos_mision": "RegistroEvento", "recomendaciones": "Recomendacion"}


def validar_esquema(docs: Dict[str, List[Tuple[Any, Dict]]], inf: Informe) -> int:
    """Comprueba que cada documento se lee con los modelos ACTUALES (SCHEMA_VERSION). Devuelve cuántos fallan."""
    from models import schemas
    inf.p(f"  versión de contrato de los modelos: SCHEMA_VERSION={schemas.SCHEMA_VERSION}")
    malos = 0
    for tabla, nombre in MODELOS.items():
        modelo = getattr(schemas, nombre)
        for clave, d in docs.get(tabla, []):
            try:
                modelo(**{**d, "token_hash": d.get("token_hash", "")} if tabla == "dispositivos" else d)
            except Exception as e:  # noqa: BLE001
                malos += 1
                if malos <= 10:
                    inf.error(f"{tabla}[{clave}] no cumple el modelo actual ({type(e).__name__})")
    return malos


# ------------------------------------------------------------------------------------------------ demo / reales
def es_ficticio_tabla(docs: Dict[str, List[Tuple[Any, Dict]]]) -> Dict[str, set]:
    """Claves de registros ficticios por tabla. Los derivados (recomendaciones, eventos, fotos, decisiones,
    dispositivos, planes) heredan la condición del registro del que dependen."""
    fic: Dict[str, set] = {t: set() for t in ORDEN}
    for t in ("sectores", "cisternas", "reportes", "misiones"):
        fic[t] = {k for k, d in docs[t] if d.get("ficticio") is True}
    for k, d in docs["recomendaciones"]:
        if d.get("reporte_id") in fic["reportes"]:
            fic["recomendaciones"].add(k)
    for k, d in docs["eventos_mision"]:
        if d.get("mision_id") in fic["misiones"]:
            fic["eventos_mision"].add(k)
    for k, d in docs["fotos"]:
        if d.get("reporte_id") in fic["reportes"]:
            fic["fotos"].add(k)
    for k, d in docs["decisiones_duplicado"]:
        if d.get("reporte_id") in fic["reportes"]:
            fic["decisiones_duplicado"].add(k)
    for k, d in docs["dispositivos"]:
        if d.get("cisterna_id") in fic["cisternas"]:
            fic["dispositivos"].add(k)
    for k, d in docs["planes"]:
        cis = {a.get("cisterna_id") for a in d.get("asignaciones", [])}
        if cis and cis <= fic["cisternas"]:
            fic["planes"].add(k)
    return fic


def verificar_cadena_origen(docs: List[Tuple[Any, Dict]]) -> Optional[str]:
    """Mismo algoritmo que services/audit.verificar_integridad, sobre los documentos del origen. None = íntegra."""
    from services import audit
    previo = audit.GENESIS
    for k, d in docs:
        cuerpo = {c: d[c] for c in ("fecha", "responsable", "accion", "entidad", "entidad_id", "motivo", "detalle")}
        if d["hash_previo"] != previo or d["hash"] != audit._hash(previo, cuerpo):
            return f"la cadena se rompe en la entrada id={k}"
        previo = d["hash"]
    return None


# ------------------------------------------------------------------------------------------------ destino
def contar_destino(con) -> Dict[str, int]:
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ORDEN}


def cargar(con, tabla: str, filas: List[Tuple[Any, str, Dict]]) -> int:
    """Inserta conservando ids. `datos_texto` de auditoría = el texto crudo del origen. Idempotente (`--reanudar`)."""
    pk, entera = CLAVES[tabla]
    n = 0
    for clave, crudo, doc in filas:
        valor = int(clave) if entera else str(clave)
        cols = _postgres._fila(tabla, valor, doc)
        if tabla == "auditoria":
            cols = [(c, m, crudo if c == "datos_texto" else v) for c, m, v in cols]   # byte a byte
            cols = [(c, m, (v if c != "datos" else crudo)) for c, m, v in cols]
        nombres = ", ".join([pk] + [c for c, _, _ in cols])
        marcadores = ", ".join(["%s::" + ("bigint" if entera else "text")] + [m for _, m, _ in cols])
        if tabla == "auditoria":       # append-only: el trigger prohíbe UPDATE, así que reanudar = ignorar lo ya cargado
            conflicto = f"ON CONFLICT ({pk}) DO NOTHING"
        else:
            conflicto = f"ON CONFLICT ({pk}) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c, _, _ in cols)
        con.execute(f"INSERT INTO {tabla} ({nombres}) VALUES ({marcadores}) {conflicto}", [valor] + [v for _, _, v in cols])
        n += 1
    if entera:
        con.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), GREATEST((SELECT COALESCE(MAX(id), 0) FROM " + tabla +
                    "), 1), (SELECT COUNT(*) > 0 FROM " + tabla + "))", (tabla,))
    return n


def igual(a: Any, b: Any) -> bool:
    return a == b


def verificar_destino(con, esperado: Dict[str, List[Tuple[Any, str, Dict]]], reanudar: bool, inf: Informe) -> None:
    """Se ejecuta DENTRO de la transacción de carga (aún sin confirmar)."""
    from services import audit
    inf.p("\nVerificación en el destino (antes de confirmar):")
    # 1. conteos y contenido documento a documento
    for t in ORDEN:
        pk, entera = CLAVES[t]
        destino = {(r[0]): r[1] for r in con.execute(f"SELECT {pk}, {'datos_texto' if t == 'auditoria' else 'datos'} FROM {t}")}
        faltan, distintos = 0, 0
        for clave, crudo, doc in esperado[t]:
            k = int(clave) if entera else str(clave)
            if k not in destino:
                faltan += 1
                continue
            leido = json.loads(destino[k]) if t == "auditoria" else destino[k]
            if t == "auditoria":
                if destino[k] != crudo:                      # texto idéntico, no solo equivalente
                    distintos += 1
            elif not igual(leido, doc):
                distintos += 1
        extra = len(destino) - (len(esperado[t]) - faltan)
        estado = "ok" if not (faltan or distintos) else "DISCREPANCIA"
        inf.p(f"  {t:<22} origen={len(esperado[t]):>6}  destino={len(destino):>6}  faltan={faltan}  distintos={distintos}  [{estado}]")
        if faltan:
            inf.error(f"{t}: faltan {faltan} filas en el destino")
        if distintos:
            inf.error(f"{t}: {distintos} documentos difieren del origen")
        if extra > 0:
            (inf.aviso if reanudar else inf.error)(f"{t}: el destino tiene {extra} filas que no vienen del origen")
    # 2. relaciones sin huérfanos
    consultas = {
        "misiones sin cisterna": "SELECT count(*) FROM misiones m LEFT JOIN cisternas c ON c.id = m.cisterna_id WHERE c.id IS NULL",
        "dispositivos sin cisterna": "SELECT count(*) FROM dispositivos d LEFT JOIN cisternas c ON c.id = d.cisterna_id WHERE c.id IS NULL",
        "recomendaciones sin reporte": "SELECT count(*) FROM recomendaciones r LEFT JOIN reportes p ON p.id = r.reporte_id WHERE p.id IS NULL",
        "eventos con mision declarada pero sin enlazar": "SELECT count(*) FROM eventos_mision e WHERE e.mision_id IS NULL "
                                                         "AND (e.datos->>'mision_id') IS NOT NULL AND EXISTS "
                                                         "(SELECT 1 FROM misiones m WHERE m.id = (e.datos->>'mision_id')::bigint)",
        "reportes duplicado_de inexistente": "SELECT count(*) FROM reportes r WHERE r.duplicado_de IS NOT NULL AND NOT EXISTS "
                                             "(SELECT 1 FROM reportes o WHERE o.id = r.duplicado_de)",
        "decisiones con reporte inexistente": "SELECT count(*) FROM decisiones_duplicado d WHERE d.reporte_id IS NOT NULL AND NOT EXISTS "
                                              "(SELECT 1 FROM reportes o WHERE o.id = d.reporte_id)",
    }
    for nombre, sql in consultas.items():
        n = con.execute(sql).fetchone()[0]
        inf.p(f"  {nombre:<46} {n}  [{'ok' if n == 0 else 'DISCREPANCIA'}]")
        if n:
            inf.error(f"{nombre}: {n}")
    huerfanas = con.execute("SELECT count(*) FROM fotos f WHERE f.reporte_id IS NOT NULL AND NOT EXISTS "
                            "(SELECT 1 FROM reportes r WHERE r.id = f.reporte_id)").fetchone()[0]
    if huerfanas:
        inf.aviso(f"{huerfanas} fotos apuntan a reportes que ya no existen (permitido por diseño: las fotos sobreviven al reinicio de la demo)")
    # 3. registros críticos
    admins = con.execute("SELECT count(*) FROM usuarios WHERE rol = 'administrador' AND coalesce(activo, true)").fetchone()[0]
    origen_admins = sum(1 for _, _, d in esperado["usuarios"] if d.get("rol") == "administrador" and d.get("activo", True))
    inf.p(f"  administradores activos: origen={origen_admins} destino={admins}  [{'ok' if admins == origen_admins else 'DISCREPANCIA'}]")
    if admins != origen_admins:
        inf.error("el número de administradores activos no coincide")
    tokens = {d.get("token_hash") for _, _, d in esperado["dispositivos"] if d.get("token_hash")}
    en_dest = {r[0] for r in con.execute("SELECT token_hash FROM dispositivos WHERE token_hash IS NOT NULL AND token_hash <> ''")}
    if tokens - en_dest:
        inf.error(f"{len(tokens - en_dest)} token_hash de dispositivos no llegaron al destino")
    codigos = {d.get("codigo_seguimiento") for _, _, d in esperado["reportes"] if d.get("codigo_seguimiento")}
    en_dest = {r[0] for r in con.execute("SELECT codigo_seguimiento FROM reportes WHERE codigo_seguimiento IS NOT NULL")}
    if codigos - en_dest:
        inf.error(f"{len(codigos - en_dest)} códigos de seguimiento no llegaron al destino")
    inf.p(f"  códigos de seguimiento={len(codigos)}  tokens de dispositivos={len(tokens)}  [{'ok' if not inf.discrepancias else 'revisar'}]")
    # 4. contadores IDENTITY
    for t in ORDEN:
        if CLAVES[t][1]:
            maximo = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0]
            siguiente = con.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (t,)).fetchone()[0]
            if siguiente <= maximo:
                inf.error(f"{t}: el contador IDENTITY ({siguiente}) no supera el id máximo ({maximo})")
            con.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), GREATEST(%s, 1), %s)", (t, maximo, maximo > 0))
    # 5. cadena de hashes con la MISMA función de la aplicación, leyendo la transacción abierta
    with _postgres.adoptar_transaccion(con):
        integra = audit.verificar_integridad()
    inf.p(f"  cadena de hashes de auditoría (services.audit.verificar_integridad): {'ÍNTEGRA' if integra else 'ROTA'}")
    if not integra:
        inf.error("la cadena de hashes de auditoría NO se verifica en el destino")
    if esperado["auditoria"]:
        ultimo_origen = esperado["auditoria"][-1][2]["hash"]
        ultimo_dest = con.execute("SELECT hash FROM auditoria ORDER BY id DESC LIMIT 1").fetchone()
        if not ultimo_dest or ultimo_dest[0] != ultimo_origen:
            inf.error("el último hash de auditoría del destino no coincide con el del origen")
        else:
            inf.p(f"  último hash idéntico al del origen: {ultimo_origen[:16]}…")


def hook_fotos(docs_fotos: List[Dict], media_dir: Path, hook: Optional[str], inf: Informe) -> None:
    """Los ARCHIVOS de las fotos no viajan por SQL. Aquí solo se comprueba qué hay en disco y se llama al gancho
    opcional `paquete.modulo:funcion(metadatos, media_dir)` (p. ej. la subida al bucket privado de Supabase Storage,
    que pertenece al servicio de almacenamiento del backend)."""
    presentes = faltan = 0
    for d in docs_fotos:
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(d.get("tipo_mime", ""), "")
        if any((media_dir / f"{d['id']}.{e}").exists() for e in ([ext] if ext else ["jpg", "png", "webp"])):
            presentes += 1
        else:
            faltan += 1
    inf.p(f"  fotos: {len(docs_fotos)} metadatos; archivos en {media_dir}: {presentes} presentes, {faltan} ausentes")
    if faltan:
        inf.aviso(f"{faltan} archivos de fotos no están en {media_dir} (solo se migran sus metadatos)")
    if docs_fotos and not hook:
        inf.aviso("los archivos de fotos NO se copiaron: súbalos con el paso de Storage del backend o use --hook-fotos")
    if hook and docs_fotos:
        modulo, _, funcion = hook.partition(":")
        import importlib
        getattr(importlib.import_module(modulo), funcion)(docs_fotos, media_dir)
        inf.p(f"  gancho {hook} ejecutado")


# ------------------------------------------------------------------------------------------------ principal
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Migra agua_clara.db (SQLite) a PostgreSQL/Supabase (DATABASE_URL del entorno).")
    ap.add_argument("--origen", required=True, help="ruta de la base SQLite (se abre en solo lectura)")
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="(por defecto) analiza; no escribe en el destino")
    modo.add_argument("--ensayo", action="store_true", help="carga y verifica en una transacción que SIEMPRE se revierte")
    modo.add_argument("--aplicar", action="store_true", help="carga, verifica y confirma si todo coincide")
    dem = ap.add_mutually_exclusive_group()
    dem.add_argument("--incluir-demo", action="store_true", help="(por defecto) migra también los registros ficticios")
    dem.add_argument("--solo-reales", action="store_true", help="omite los registros con ficticio=true y sus derivados")
    ap.add_argument("--backup-dir", help="carpeta para la copia de seguridad del origen (recomendado)")
    ap.add_argument("--reanudar", action="store_true", help="permite un destino con datos (reintento idempotente)")
    ap.add_argument("--media-dir", help="carpeta de fotos del origen (por defecto config.MEDIA_DIR)")
    ap.add_argument("--hook-fotos", help="paquete.modulo:funcion(metadatos, media_dir) para subir los archivos")
    ap.add_argument("--permitir-cadena-rota", action="store_true", help="migra aunque la cadena de auditoría del ORIGEN ya esté rota")
    ap.add_argument("--informe", help="guarda el informe de texto en este archivo")
    a = ap.parse_args(argv)
    inf = Informe()
    try:
        return _ejecutar(a, inf)
    except ErrorBaseDeDatos as e:
        inf.p(f"ERROR: {e}")
        return 2
    finally:
        if a.informe:
            Path(a.informe).write_text("\n".join(inf.lineas), encoding="utf-8")


def _ejecutar(a, inf: Informe) -> int:
    aplicar, ensayo = a.aplicar, a.ensayo
    ruta = Path(a.origen)
    inf.p(f"HIDRO360 · migración SQLite -> PostgreSQL · {datetime.now():%Y-%m-%d %H:%M:%S}")
    inf.p(f"modo: {'APLICAR' if aplicar else 'ENSAYO (rollback)' if ensayo else 'DRY-RUN (no escribe)'} · "
          f"{'solo reales' if a.solo_reales else 'incluye demo'}")
    if not config.modo_postgres():
        inf.p("ERROR: DATABASE_URL no está definida (el destino se toma del entorno, no de argumentos).")
        return 2

    inf.p("\n1. Origen")
    origen = abrir_origen(ruta)
    inf.p(f"  {ruta}  sha256={sha256_archivo(ruta)[:16]}…  (abierto en solo lectura)")
    ic = origen.execute("PRAGMA integrity_check").fetchone()[0]
    inf.p(f"  PRAGMA integrity_check: {ic}")
    if ic != "ok":
        inf.p("ERROR: el origen está dañado; no se migra.")
        return 1
    if a.backup_dir:
        respaldar(origen, ruta, Path(a.backup_dir), inf)
    elif aplicar:
        inf.aviso("sin --backup-dir: no se hizo copia de seguridad del origen")
    crudo, _ = leer_tablas(origen, inf)
    origen.close()
    docs = {t: [(k, json.loads(txt)) for k, txt in v] for t, v in crudo.items()}
    inf.p("  filas en el origen: " + ", ".join(f"{t}={len(v)}" for t, v in docs.items()))

    inf.p("\n2. Validación del origen")
    if validar_esquema(docs, inf):
        inf.p("ERROR: hay documentos que no cumplen los modelos actuales; haga una migración de formato antes.")
        return 1
    rota = verificar_cadena_origen(docs["auditoria"])
    inf.p(f"  cadena de auditoría del origen: {'íntegra' if rota is None else 'ROTA (' + rota + ')'}")
    if rota and not a.permitir_cadena_rota:
        inf.p("ERROR: la cadena del origen ya está rota; investigue antes de migrar (o use --permitir-cadena-rota).")
        return 1

    fic = es_ficticio_tabla(docs)
    inf.p("  registros ficticios detectados: " + (", ".join(f"{t}={len(fic[t])}" for t in ORDEN if fic[t]) or "ninguno"))
    esperado: Dict[str, List[Tuple[Any, str, Dict]]] = {}
    for t in ORDEN:
        omitir = fic[t] if a.solo_reales and t != "auditoria" else set()    # la auditoría se migra COMPLETA: filtrarla rompería la cadena
        esperado[t] = [(k, txt, doc) for (k, txt), (_, doc) in zip(crudo[t], docs[t]) if k not in omitir]
    if a.solo_reales:
        inf.aviso("--solo-reales: la auditoría se migra completa para no romper la cadena de hashes")
        # integridad referencial del subconjunto: nada puede apuntar a algo omitido
        ids_cis = {k for k, _, _ in esperado["cisternas"]}
        esperado["dispositivos"] = [x for x in esperado["dispositivos"] if x[2].get("cisterna_id") in ids_cis]
        esperado["misiones"] = [x for x in esperado["misiones"] if x[2].get("cisterna_id") in ids_cis]
        ids_mis = {k for k, _, _ in esperado["misiones"]}
        esperado["eventos_mision"] = [x for x in esperado["eventos_mision"]
                                      if x[2].get("mision_id") in ids_mis or x[2].get("resultado") == "rechazado"]
    inf.p("  a migrar: " + ", ".join(f"{t}={len(esperado[t])}" for t in ORDEN))

    inf.p("\n3. Destino")
    con = _postgres.conectar(autocommit=True)
    try:
        con.execute("SELECT set_config('application_name', 'hidro360-migracion', false)")
        esquema = _postgres._esquema_prueba()          # solo pruebas (HIDRO360_DB_SCHEMA); en Supabase real es None => public
        if esquema:
            con.execute(f'SET search_path TO "{esquema}", public')
        tablas = {r[0] for r in con.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")}
        faltan = [t for t in ORDEN if t not in tablas]
        if faltan:
            inf.p(f"ERROR: faltan tablas en el destino ({', '.join(faltan)}). Ejecute: python -m scripts.aplicar_migraciones")
            return 2
        previas = contar_destino(con)
        no_vacias = {t: n for t, n in previas.items() if n}
        inf.p("  filas actuales en el destino: " + (", ".join(f"{t}={n}" for t, n in no_vacias.items()) or "ninguna (vacío)"))
        if no_vacias and not a.reanudar:
            inf.p("RECHAZADO: el destino tiene datos. No se escribe (use --reanudar solo si es un reintento de esta misma migración).")
            return 1
        if not (aplicar or ensayo):
            inf.p("\nDRY-RUN: nada se escribió. Revise lo anterior y use --ensayo (verifica y revierte) o --aplicar.")
            return 0

        inf.p("\n4. Carga (UNA transacción)")
        media = Path(a.media_dir) if a.media_dir else config.MEDIA_DIR
        con.execute("BEGIN; SELECT set_config('statement_timeout', '0', true), set_config('lock_timeout', '30000', true); "
                    f"SELECT pg_advisory_xact_lock({_postgres.LOCK_NEGOCIO})")
        try:
            con.execute("SET CONSTRAINTS ALL DEFERRED")
            for t in ORDEN:
                n = cargar(con, t, esperado[t])
                inf.p(f"  {t:<22} {n} filas")
            verificar_destino(con, esperado, a.reanudar, inf)
            hook_fotos([d for _, _, d in esperado["fotos"]], media, a.hook_fotos, inf)
            if inf.discrepancias:
                con.execute("ROLLBACK")
                inf.p(f"\nREVERTIDO: {len(inf.discrepancias)} discrepancias. El destino quedó como estaba.")
                return 1
            if ensayo:
                con.execute("ROLLBACK")
                inf.p("\nENSAYO COMPLETADO sin discrepancias; se revirtió (no se escribió nada).")
                return 0
            con.execute("COMMIT")
            inf.p("\nCONFIRMADO: migración aplicada y verificada.")
            inf.p("Para deshacer (solo porque el destino estaba vacío): ver docs/BASE_DE_DATOS.md > «Revertir una migración».")
            return 0
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
