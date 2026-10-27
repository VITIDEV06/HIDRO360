"""
Aplicador de migraciones SQL (supabase/migrations/*.sql) para entornos SIN Supabase CLI (pruebas, desarrollo local,
un Postgres cualquiera). Con la CLI de Supabase use `supabase db push`; los archivos son los mismos y son
idempotentes, de modo que ambos caminos pueden convivir.

Registra cada versión aplicada en `schema_migraciones` con el checksum del archivo: si un archivo ya aplicado se
edita, se niega a continuar (una migración publicada no se reescribe: añada una nueva).
Úsese con la conexión DIRECTA (puerto 5432) o con una de sesión; las migraciones DDL no son para el pooler en modo transacción.
"""

import hashlib
import re
from pathlib import Path
from typing import List, Optional

import config
from data import _postgres
from data._comun import ErrorBaseDeDatos

DIRECTORIO = config.BASE_DIR / "supabase" / "migrations"
_NOMBRE = re.compile(r"^(\d{14})_[a-z0-9_]+\.sql$")
_LOCK_MIGRACIONES = 0x48494452414D4947   # distinta de la cerradura de negocio


def archivos(directorio: Optional[Path] = None) -> List[Path]:
    d = directorio or DIRECTORIO
    return sorted(p for p in d.glob("*.sql") if _NOMBRE.match(p.name))


def _checksum(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def aplicar(esquema: Optional[str] = None, directorio: Optional[Path] = None) -> List[str]:
    """Aplica las migraciones pendientes (en orden) y devuelve las versiones aplicadas ahora.
    `esquema`: crea y usa ese esquema (aislamiento en pruebas); en Supabase se deja None (= `public`)."""
    if esquema is not None and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", esquema):
        raise ErrorBaseDeDatos("Nombre de esquema no válido.")
    con = _postgres.conectar(autocommit=True)
    aplicadas: List[str] = []
    try:
        con.execute(f"SELECT pg_advisory_lock({_LOCK_MIGRACIONES})")
        if esquema:
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{esquema}"')
            con.execute(f"SET search_path TO \"{esquema}\", public")
        con.execute("CREATE TABLE IF NOT EXISTS schema_migraciones (version text PRIMARY KEY, "
                    "aplicada timestamptz NOT NULL DEFAULT now(), checksum text)")
        previas = {v: c for v, c in con.execute("SELECT version, checksum FROM schema_migraciones").fetchall()}
        for p in archivos(directorio):
            version, suma = p.stem, _checksum(p)
            if version in previas:
                if previas[version] and previas[version] != suma:
                    raise ErrorBaseDeDatos(f"La migración {version} ya estaba aplicada y su contenido cambió.")
                continue
            try:
                con.execute("BEGIN")
                con.execute(p.read_text(encoding="utf-8"))
                con.execute("INSERT INTO schema_migraciones (version, checksum) VALUES (%s, %s)", (version, suma))
                con.execute("COMMIT")
            except Exception as e:  # noqa: BLE001
                try:
                    con.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                raise ErrorBaseDeDatos(f"Falló la migración {version} ({type(e).__name__}).") from None
            aplicadas.append(version)
    finally:
        con.close()
    return aplicadas
