"""
Aplica supabase/migrations/*.sql a la base de DATABASE_URL (sin Supabase CLI). Idempotente.

    set DATABASE_URL=...   (variable de entorno; la cadena NO se pasa como argumento)
    python -m scripts.aplicar_migraciones            # aplica las pendientes
    python -m scripts.aplicar_migraciones --listar   # solo muestra los archivos y el estado

Con Supabase CLI el equivalente es `supabase db push`. Use la conexión directa (5432) o de sesión, no el pooler de transacción.
"""

import argparse
import sys

import config
from data import migraciones
from data._comun import ErrorBaseDeDatos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aplica las migraciones SQL de supabase/migrations.")
    ap.add_argument("--listar", action="store_true", help="solo lista los archivos de migración")
    a = ap.parse_args(argv)
    if a.listar:
        for p in migraciones.archivos():
            print(p.name)
        return 0
    if not config.modo_postgres():
        print("DATABASE_URL no está definida: nada que migrar (SQLite crea su esquema solo).")
        return 1
    try:
        hechas = migraciones.aplicar()
    except ErrorBaseDeDatos as e:
        print(f"ERROR: {e}")
        return 2
    print("Aplicadas: " + (", ".join(hechas) if hechas else "ninguna (ya estaba al día)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
