"""Pruebas del empaquetado y la configuracion de despliegue (Vercel/Supabase). No tocan la red ni la nube."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import revisar_secretos

RAIZ = Path(__file__).resolve().parent.parent


def _nombre(requisito: str) -> str:
    return re.split(r"[\[<>=~!;\s]", requisito.strip(), maxsplit=1)[0].lower().replace("_", "-")


# ---------------------------------------------------------------- escaner de secretos
def test_repositorio_sin_secretos():
    if subprocess.run(["git", "rev-parse"], cwd=RAIZ, capture_output=True).returncode != 0:
        pytest.skip("no es un repositorio git")
    assert revisar_secretos.escanear(revisar_secretos.archivos_versionados()) == []


def test_escaner_detecta_secretos_falsos_construidos_en_la_prueba():
    google = "AI" + "za" + "Sy" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG"[:33]
    jwt = "ey" + "J" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcdefghij0123456789ABCD"
    pg = "postgres" + "ql://postgres.abc:" + "Xk39sLq02mZ8@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    srv = "SUPABASE_SERVICE_" + "ROLE_KEY=" + "q9w8e7r6t5y4u3i2o1p0aSdFgH"
    for texto in (google, jwt, pg, srv):
        assert revisar_secretos.escanear_texto(texto), texto[:12]


def test_escaner_ignora_ejemplos_falsos_y_marcador():
    ok = [
        "DATABASE_URL=postgresql://postgres.PROYECTO:CONTRASENA@aws-0-REGION.pooler.supabase.com:6543/postgres",
        "HIDRO360_API_TOKEN=reemplazar-por-64-hex-aleatorios",
        "GEMINI_API_KEY=",
        "SUPABASE_SERVICE_ROLE_KEY=",
        "TELEGRAM_BOT_TOKEN=",
        "TELEGRAM_WEBHOOK_SECRET=",
        "x = 'AI" + "zaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'  # secretos: ok",
    ]
    for linea in ok:
        assert revisar_secretos.escanear_texto(linea) == [], linea


# ---------------------------------------------------------------- .env.example y .gitignore
def test_env_example_sin_valores_reales():
    lineas = (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()
    activas = [ln for ln in lineas if ln.strip() and not ln.lstrip().startswith("#")]
    for ln in activas:
        clave, _, valor = ln.partition("=")
        assert clave.strip() in {"AI_PROVIDER", "HIDRO360_ENV"}, f"variable activa inesperada: {clave}"
    assert revisar_secretos.escanear_texto("\n".join(lineas)) == []
    for sec in ("GEMINI_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "DATABASE_URL",
                "HIDRO360_API_TOKEN", "HIDRO360_AUTH", "HIDRO360_STORAGE", "SUPABASE_URL", "SUPABASE_ANON_KEY",
                "SUPABASE_STORAGE_BUCKET", "SUPABASE_AUDIO_BUCKET", "HIDRO360_CORS_ORIGINS", "HIDRO360_TRACE", "HIDRO360_ENV",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_PATH", "HIDRO360_TRANSCRIPCION",
                "PUBLIC_APP_URL"):
        assert sec in "\n".join(lineas), f"falta documentar {sec}"


def test_gitignore_protege_secretos_pero_no_ejemplos():
    if subprocess.run(["git", "rev-parse"], cwd=RAIZ, capture_output=True).returncode != 0:
        pytest.skip("no es un repositorio git")

    def ignorado(ruta: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", ruta], cwd=RAIZ).returncode == 0

    for ruta in (".env", ".env.local", ".vercel/project.json", "supabase/.temp/x", "media/x.jpg", "x.db"):
        assert ignorado(ruta), ruta
    for ruta in (".env.example", "vercel.json", "deploy/raspberry/install.sh", "firmware/esp32s3_monitor/a.ino",
                 "supabase/migrations/0001_x.sql"):
        assert not ignorado(ruta), ruta


# ---------------------------------------------------------------- Vercel
def test_vercel_json_valido_y_coherente():
    cfg = json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["regions"] and isinstance(cfg["regions"], list)
    for archivo, opciones in cfg["functions"].items():
        assert (RAIZ / archivo).is_file(), archivo
        # 2 etapas de IA x (timeout x (1 + reintentos)) debe caber, mas margen para la base de datos
        peor_ia = 2 * 15 * (1 + 1)
        assert opciones["maxDuration"] >= peor_ia + 10
    assert "interface/**" in cfg["functions"]["api/main.py"]["excludeFiles"]


def test_pyproject_entrypoint_y_dependencias():
    tomllib = pytest.importorskip("tomllib")
    cfg = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    modulo, _, variable = cfg["tool"]["vercel"]["entrypoint"].partition(":")
    assert (RAIZ / (modulo.replace(".", "/") + ".py")).is_file()
    assert variable == "app"
    deps = {_nombre(d) for d in cfg["project"]["dependencies"]}
    assert deps.isdisjoint({"flet", "flet-desktop", "flet-web", "flet-map", "pytest"})
    servidor = {_nombre(ln) for ln in (RAIZ / "requirements-servidor.txt").read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith(("#", "-"))}
    assert deps <= servidor, f"en pyproject pero no en requirements-servidor.txt: {deps - servidor}"
    bloqueo = (RAIZ / "uv.lock").read_text(encoding="utf-8")   # Vercel ejecuta `uv sync --locked`
    for d in deps:
        assert f'name = "{d}"' in bloqueo, f"uv.lock desactualizado: falta {d} (ejecute: uv lock)"
    assert (RAIZ / ".python-version").read_text().strip() in {"3.12", "3.13", "3.14"}


def test_entrypoint_importa_sin_flet():
    """La funcion serverless no debe cargar Flet (paquete pesado, solo para la UI)."""
    codigo = ("import sys, api.main as m; "
              "assert type(m.app).__name__ == 'FastAPI'; "
              "assert not any(k == 'flet' or k.startswith('flet.') for k in sys.modules), 'flet cargado'")
    entorno = {**os.environ, "HIDRO360_ENV": "test", "PYTHONPATH": str(RAIZ)}
    r = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, env=entorno, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def test_vercelignore_excluye_lo_privado():
    texto = (RAIZ / ".vercelignore").read_text(encoding="utf-8")
    for patron in (".env", "*.db", "media/", "firmware/", "interface/"):
        assert patron in texto
