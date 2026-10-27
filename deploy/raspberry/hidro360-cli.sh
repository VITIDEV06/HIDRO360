#!/usr/bin/env bash
# Ejecuta un módulo de scripts/ como el usuario `hidro360` con la configuración de /etc/hidro360/hidro360.env
# (que solo root puede leer). Sirve para tareas administrativas que escriben en la base de producción:
#     sudo /opt/hidro360/deploy/raspberry/hidro360-cli.sh scripts.crear_admin
#     sudo /opt/hidro360/deploy/raspberry/hidro360-cli.sh scripts.registrar_dispositivo CIS-01
# SIN PROBAR en una Raspberry (solo `bash -n`).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/hidro360}"
ENV_FILE="${ENV_FILE:-/etc/hidro360/hidro360.env}"
[ "$(id -u)" -eq 0 ] || { echo "Ejecute con sudo." >&2; exit 1; }
[ $# -ge 1 ] || { echo "Uso: $0 <modulo> [argumentos]   p. ej. scripts.crear_admin" >&2; exit 2; }
[ -f "$ENV_FILE" ] || { echo "No existe $ENV_FILE (¿ejecutó install.sh?)" >&2; exit 1; }

# Se lee KEY=VALUE línea a línea (sin ejecutar el archivo como shell).
while IFS= read -r linea || [ -n "$linea" ]; do
  case "$linea" in ''|\#*) continue ;; esac
  clave="${linea%%=*}"; valor="${linea#*=}"
  [[ "$clave" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && export "$clave=$valor"
done < "$ENV_FILE"

cd "$APP_DIR"
exec runuser -u hidro360 -- env PYTHONDONTWRITEBYTECODE=1 HOME=/var/lib/hidro360 "$APP_DIR/.venv/bin/python" -m "$@"
