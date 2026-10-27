"""
Revisa los archivos VERSIONADOS y los nuevos aun sin versionar (git ls-files --cached --others --exclude-standard) en busca de secretos: claves de Google, JWT, cadenas de conexion
PostgreSQL con contrasena, claves service_role/sb_secret, claves privadas y asignaciones de tokens largos.

    python -m scripts.revisar_secretos            # revisa git ls-files; codigo 0 = limpio, 1 = hallazgos
    python -m scripts.revisar_secretos ruta ...   # revisa solo esos archivos

Nunca imprime el valor encontrado (solo archivo, linea, tipo y los 4 primeros caracteres). Los valores de
ejemplo evidentemente falsos (CONTRASENA, reemplazar-por..., xxxx, <...>, ${...}) se ignoran. Para silenciar una
linea de prueba con un valor falso deliberado, anada al final el marcador   secretos: ok
Limites: es una busqueda por patrones; no sustituye rotar una clave que ya se subio a Git (rote siempre).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

RAIZ = Path(__file__).resolve().parent.parent
MARCADOR_OK = "secretos: ok"
TAM_MAX = 2 * 1024 * 1024
EXT_BINARIAS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2", ".ttf", ".otf",
                ".bin", ".so", ".dll", ".exe", ".db", ".sqlite", ".tar", ".gz", ".mp4", ".webp", ".pyc"}

# Fragmentos que delatan un valor de ejemplo, no real.
_FALSOS = ("contrasena", "contraseña", "password", "reemplazar", "cambiar", "ejemplo", "example", "xxxx", "****",
           "tu-clave", "su-clave", "tu_clave", "su_clave", "changeme", "dummy", "fake", "falso", "placeholder",
           "tok-", "token-de", "solo_prueba", "no_es_una", "no_deb", "canario", "centinela", "no-es-una", "usuario", "proyecto", "<", ">", "${", "{{", "[", "...")

PATRONES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("clave de Google/Gemini (AIza...)", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("JWT (eyJ...)", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("clave sb_secret de Supabase", re.compile(r"sb_secret_[A-Za-z0-9_\-]{20,}")),
    ("clave privada PEM", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("cadena PostgreSQL con contrasena",
     re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s/@'\"]+:([^@\s'\"]+)@[^\s'\"]+")),
    ("clave service_role asignada",
     re.compile(r"SERVICE_ROLE_KEY\s*[=:]\s*['\"]?([A-Za-z0-9_\-\.]{20,})")),
    ("token/secreto largo asignado",
     re.compile(r"(?i)\b[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|APIKEY|PASSWORD)\b\s*[=:]\s*['\"]?([A-Za-z0-9+/_\-]{32,})")),
]


def _es_falso(valor: str) -> bool:
    v = valor.lower()
    if any(f in v for f in _FALSOS):
        return True
    return len(set(v)) <= 3 or v in ("clave", "pass", "secret")  # 'aaaa...', '0000...'


# En tests/ se construyen a proposito JWT y cadenas con contrasena para probar la redaccion de secretos:
# alli no se exigen esos dos tipos (si siguen vigilados AIza, PEM, service_role, sb_secret y tokens largos).
TIPOS_RELAJADOS_EN_PRUEBAS = ("JWT", "cadena PostgreSQL")


def escanear_texto(texto: str, en_pruebas: bool = False) -> List[Tuple[int, str, str]]:
    """Devuelve [(linea, tipo, inicio_enmascarado)]. Pura (la usan las pruebas)."""
    hallazgos: List[Tuple[int, str, str]] = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if MARCADOR_OK in linea:
            continue
        for tipo, patron in PATRONES:
            if en_pruebas and tipo.startswith(TIPOS_RELAJADOS_EN_PRUEBAS):
                continue
            for m in patron.finditer(linea):
                valor = m.group(1) if m.groups() else m.group(0)
                if not tipo.startswith("clave privada") and _es_falso(valor):
                    continue
                hallazgos.append((n, tipo, valor[:4] + "..."))
    return hallazgos


def archivos_versionados() -> List[Path]:
    salida = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=RAIZ, capture_output=True, check=True).stdout
    return [RAIZ / p for p in salida.decode("utf-8", "replace").split("\0") if p]


def escanear(rutas: Iterable[Path]) -> List[Tuple[Path, int, str, str]]:
    todo: List[Tuple[Path, int, str, str]] = []
    for ruta in rutas:
        try:
            if not ruta.is_file() or ruta.suffix.lower() in EXT_BINARIAS or ruta.stat().st_size > TAM_MAX:
                continue
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        es_prueba = "tests" in ruta.relative_to(RAIZ).parts[:1] if ruta.is_relative_to(RAIZ) else False
        for n, tipo, ini in escanear_texto(texto, es_prueba):
            todo.append((ruta, n, tipo, ini))
    return todo


def main(argv: List[str]) -> int:
    rutas = [Path(a).resolve() for a in argv] if argv else archivos_versionados()
    hallazgos = escanear(rutas)
    for ruta, n, tipo, ini in hallazgos:
        try:
            rel = ruta.relative_to(RAIZ)
        except ValueError:
            rel = ruta
        print(f"{rel}:{n}: {tipo} (empieza por {ini})")
    if hallazgos:
        print(f"\n{len(hallazgos)} posible(s) secreto(s). Retirelos y ROTE la clave si ya estaba en Git.")
        return 1
    print(f"Sin secretos detectados en {len(rutas)} archivo(s) versionados.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
