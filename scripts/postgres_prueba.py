"""
PostgreSQL LOCAL y desechable para las pruebas de la capa de datos (no toca Supabase ni DATABASE_URL).

Sin Docker, sin psql y sin instalar nada global: usa los binarios de PostgreSQL que trae el paquete npm
`embedded-postgres` (Windows/Linux/macOS), instalado en una carpeta git-ignorada del proyecto:

    npm install embedded-postgres --prefix .tools/pgnode
    node .tools/pgnode/node_modules/@embedded-postgres/windows-x64/scripts/hydrate-symlinks.js   (solo si npm
                                                  no ejecuto el postinstall; en Windows ocurre con allowScripts)
    python -m scripts.postgres_prueba            # arranca, imprime TEST_DATABASE_URL y espera (Ctrl+C = parar)

Alternativas: `--bin <carpeta con postgres/initdb>` o la variable HIDRO360_PG_BIN; o un PostgreSQL ya instalado en el PATH.

Qué hace: initdb en una carpeta temporal (autenticación `trust`, SOLO 127.0.0.1) -> arranca el servidor ->
crea la base `hidro360_test` -> ejecuta supabase/tests/00_roles_supabase_simulados.sql, que imita los roles
de Supabase (anon, authenticated, service_role, authenticator) porque en un Postgres plano no existen.
Al terminar borra la carpeta de datos. También se usa como librería: `with servidor() as url: ...`.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator, Optional

RAIZ = Path(__file__).resolve().parent.parent
ROLES_SQL = RAIZ / "supabase" / "tests" / "00_roles_supabase_simulados.sql"
BASE = "hidro360_test"


def _paquete_npm() -> str:
    sistema = platform.system().lower()
    arq = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return {"windows": f"windows-{arq}", "linux": f"linux-{arq}", "darwin": f"darwin-{arq}"}.get(sistema, "linux-x64")


def encontrar_binarios(bin_dir: Optional[str] = None) -> Path:
    candidatos = []
    if bin_dir:
        candidatos.append(Path(bin_dir))
    if os.environ.get("HIDRO360_PG_BIN"):
        candidatos.append(Path(os.environ["HIDRO360_PG_BIN"]))
    candidatos.append(RAIZ / ".tools" / "pgnode" / "node_modules" / "@embedded-postgres" / _paquete_npm() / "native" / "bin")
    exe = "postgres.exe" if os.name == "nt" else "postgres"
    for c in candidatos:
        if (c / exe).exists():
            return c
    en_path = shutil.which("postgres")
    if en_path:
        return Path(en_path).parent
    raise FileNotFoundError("No hay binarios de PostgreSQL. Ver la cabecera de scripts/postgres_prueba.py "
                            "(npm install embedded-postgres --prefix .tools/pgnode).")


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _esperar(url_admin: str, intentos: int = 60) -> None:
    import psycopg
    ultimo: Optional[Exception] = None
    for _ in range(intentos):
        try:
            with psycopg.connect(url_admin, connect_timeout=2):
                return
        except Exception as e:  # noqa: BLE001 - se reintenta mientras arranca
            ultimo = e
            time.sleep(0.5)
    raise RuntimeError(f"PostgreSQL de prueba no arrancó: {type(ultimo).__name__}")


@contextlib.contextmanager
def servidor(bin_dir: Optional[str] = None, puerto: Optional[int] = None, datadir: Optional[str] = None) -> Iterator[str]:
    """Arranca un PostgreSQL desechable y entrega la URL de la base `hidro360_test` (usuario `postgres`, sin clave)."""
    import psycopg
    binarios = encontrar_binarios(bin_dir)
    ext = ".exe" if os.name == "nt" else ""
    puerto = puerto or _puerto_libre()
    tmp = Path(datadir) if datadir else Path(tempfile.mkdtemp(prefix="hidro360_pg_"))
    data = tmp / "data"
    subprocess.run([str(binarios / f"initdb{ext}"), "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8",
                    "--locale=C"], check=True, capture_output=True)
    log = open(tmp / "postgres.log", "wb")
    proc = subprocess.Popen([str(binarios / f"postgres{ext}"), "-D", str(data), "-p", str(puerto),
                             "-c", "listen_addresses=127.0.0.1", "-c", "fsync=off", "-c", "full_page_writes=off",
                             "-c", "synchronous_commit=off", "-c", "max_connections=50"],
                            stdout=log, stderr=subprocess.STDOUT)
    admin = f"postgresql://postgres@127.0.0.1:{puerto}/postgres"
    try:
        _esperar(admin)
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(f"CREATE DATABASE {BASE}")
        url = f"postgresql://postgres@127.0.0.1:{puerto}/{BASE}"
        with psycopg.connect(url, autocommit=True) as con:
            con.execute(ROLES_SQL.read_text(encoding="utf-8"))
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if not datadir:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PostgreSQL local desechable para pruebas de HIDRO360.")
    ap.add_argument("--bin", help="carpeta con postgres e initdb")
    ap.add_argument("--puerto", type=int)
    ap.add_argument("--datadir", help="carpeta de datos (se conserva); por defecto temporal")
    a = ap.parse_args(argv)
    with servidor(a.bin, a.puerto, a.datadir) as url:
        print("PostgreSQL de prueba listo. En otra terminal:")
        print(f"  PowerShell:  $env:TEST_DATABASE_URL = '{url}'")
        print(f"  bash:        export TEST_DATABASE_URL='{url}'")
        print("Ctrl+C para detenerlo y borrarlo.", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
