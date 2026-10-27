"""
Registra (o rota el token de) la terminal ESP32-S3 de un vehículo desde la terminal local del servidor.

    python -m scripts.registrar_dispositivo CIS-001 --nombre "Camión 1"
    python -m scripts.registrar_dispositivo CIS-001 --rotar        # token nuevo; el anterior deja de servir
    python -m scripts.registrar_dispositivo --listar

El token se imprime UNA sola vez (en la base solo queda su SHA-256). Cópielo al firmware/NVS del dispositivo y no
lo guarde en Git. Requiere acceso local a la base de datos; queda auditado como «servicio:script-local».
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import auth, workflow  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cisterna_id", nargs="?", help="id de la cisterna (p. ej. CIS-001)")
    ap.add_argument("--nombre", default="", help="nombre legible del vehículo")
    ap.add_argument("--rotar", action="store_true", help="genera un token nuevo para un dispositivo ya registrado")
    ap.add_argument("--listar", action="store_true", help="lista los dispositivos (sin tokens)")
    a = ap.parse_args()
    actor = auth.Actor("script-local", auth.Rol.ADMIN, "servicio")
    try:
        with auth.como(actor):
            if a.listar:
                for d in workflow.listar_dispositivos():
                    print(f"{d.cisterna_id}\t{'activo' if d.activo else 'INACTIVO'}\t{d.nombre}\t"
                          f"última conexión: {d.ultima_conexion or 'nunca'}")
                return 0
            if not a.cisterna_id:
                ap.error("indique cisterna_id (o --listar)")
            if a.rotar:
                d, token = workflow.rotar_token(a.cisterna_id, "script-local")
            else:
                d, token = workflow.registrar_dispositivo(a.cisterna_id, a.nombre, "script-local")
    except workflow.ErrorDeNegocio as e:
        print(f"No se pudo: {e}")
        return 1
    print(f"Dispositivo de {d.cisterna_id} listo. Token (se muestra solo ahora):\n{token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
