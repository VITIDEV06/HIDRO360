#!/usr/bin/env bash
# Instalación (y actualización) de HIDRO360 en Raspberry Pi OS 64-bit (Bookworm) u otro Linux con systemd.
# Idempotente: puede ejecutarse varias veces; NO sobrescribe /etc/hidro360/hidro360.env, ni la base, ni las fotos.
# ESCRITO PERO NO EJECUTADO EN UNA RASPBERRY NI CON SYSTEMD REAL (solo validado con `bash -n`).
#
# Uso (desde el repositorio ya clonado, p. ej. en /opt/hidro360):
#     sudo bash deploy/raspberry/install.sh
# Variables opcionales:  APP_DIR=/opt/hidro360   NO_START=1 (no arrancar servicios)   CREAR_ADMIN=1 (forzar la pregunta)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR="${APP_DIR:-/opt/hidro360}"
DATA_DIR=/var/lib/hidro360
ENV_DIR=/etc/hidro360
ENV_FILE="$ENV_DIR/hidro360.env"
USER_SVC=hidro360
VENV="$APP_DIR/.venv"
UNIDADES=(hidro360-api.service hidro360-ui.service hidro360-backup.service hidro360-backup.timer)

msg() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "ejecute con sudo (la instalación es lo único que corre como root)"
command -v systemctl >/dev/null || die "se requiere systemd"
[ -f "$SRC_DIR/app.py" ] && [ -f "$SRC_DIR/requirements-servidor.txt" ] || die "no parece la raíz del repositorio HIDRO360: $SRC_DIR"

# --- 1. Paquetes del sistema ---------------------------------------------------------------
msg "Paquetes del sistema"
faltan=()
command -v python3 >/dev/null || faltan+=(python3)
python3 -c "import venv, ensurepip" 2>/dev/null || faltan+=(python3-venv)
command -v openssl >/dev/null || faltan+=(openssl)
command -v curl >/dev/null || faltan+=(curl)
if [ "${#faltan[@]}" -gt 0 ]; then
  command -v apt-get >/dev/null || die "faltan ${faltan[*]} y no hay apt-get: instálelos a mano"
  apt-get update && apt-get install -y "${faltan[@]}"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "se requiere Python 3.10 o superior"

# --- 2. Usuario de sistema sin privilegios y directorios -----------------------------------
msg "Usuario y directorios"
if ! id "$USER_SVC" &>/dev/null; then
  useradd --system --user-group --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$USER_SVC"
fi
install -d -m 750 -o "$USER_SVC" -g "$USER_SVC" "$DATA_DIR" "$DATA_DIR/db" "$DATA_DIR/media" "$DATA_DIR/logs" "$DATA_DIR/backups"
install -d -m 755 -o root -g root "$ENV_DIR"

# --- 3. Código en /opt/hidro360 (copia si el repositorio está en otra ruta) -----------------
msg "Código en $APP_DIR"
if [ "$SRC_DIR" != "$(cd "$APP_DIR" 2>/dev/null && pwd || echo '')" ]; then
  install -d -m 755 "$APP_DIR"
  tar -C "$SRC_DIR" \
      --exclude=.git --exclude='.venv*' --exclude='venv*' --exclude=__pycache__ --exclude='*.db' --exclude='*.db-*' \
      --exclude=media --exclude=logs --exclude=backups --exclude=.env --exclude='config_dispositivo.h' \
      -cf - . | tar -C "$APP_DIR" -xf -
fi
chmod -R go-w,a+rX "$APP_DIR"   # el servicio solo LEE el código; escribe únicamente en $DATA_DIR

# --- 4. Entorno virtual y dependencias de SERVIDOR (sin flet-desktop ni pytest) -------------
msg "Entorno virtual y dependencias (requirements-servidor.txt)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$APP_DIR/requirements-servidor.txt"
"$VENV/bin/pip" check || die "dependencias inconsistentes (pip check)"
(cd "$APP_DIR" && "$VENV/bin/python" -c "import api.main, flet, flet_web, google.genai" ) || die "no se pueden importar los módulos de la aplicación"

# --- 5. Configuración /etc/hidro360/hidro360.env (600 root:hidro360; NUNCA se sobrescribe) --
msg "Configuración $ENV_FILE"
if [ -f "$ENV_FILE" ]; then
  echo "Ya existe: no se modifica. Revíselo si cambió .env.example."
else
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  {
    cat "$APP_DIR/.env.example"
    echo
    echo "# --- Generado por deploy/raspberry/install.sh el $(date -Is) ---"
    echo "HIDRO360_DB=$DATA_DIR/db/hidro360.db"
    echo "HIDRO360_TRACE=$DATA_DIR/logs/trazas.jsonl"
    echo "HIDRO360_MEDIA_DIR=$DATA_DIR/media"
    echo "HIDRO360_API_TOKEN=$(openssl rand -hex 32)"
    echo "HIDRO360_DEVICE_TOKEN=$(openssl rand -hex 32)"
    echo "# GEMINI_API_KEY: añádala aquí (una línea GEMINI_API_KEY=...) solo si desea usar Gemini; sin ella funciona el modo local."
  } > "$tmp"
  install -m 600 -o root -g "$USER_SVC" "$tmp" "$ENV_FILE"
  echo "Creado con tokens aleatorios. Solo root puede leerlo (systemd lo carga al arrancar los servicios)."
fi

# --- 6. Unidades systemd -------------------------------------------------------------------
msg "Unidades systemd"
for u in "${UNIDADES[@]}"; do install -m 644 -o root -g root "$APP_DIR/deploy/raspberry/$u" /etc/systemd/system/"$u"; done
systemctl daemon-reload
chmod 755 "$APP_DIR"/deploy/raspberry/*.sh

# --- 7. Primer administrador (interactivo; no hay contraseña por defecto) ---------------------
if [ ! -f "$DATA_DIR/db/hidro360.db" ] || [ "${CREAR_ADMIN:-0}" = "1" ]; then
  if [ -t 0 ]; then
    read -r -p "¿Crear ahora el primer administrador? [S/n] " r || r=n
    case "${r:-S}" in
      [nN]*) echo "Omitido. Luego: sudo $APP_DIR/deploy/raspberry/hidro360-cli.sh scripts.crear_admin" ;;
      *) "$APP_DIR/deploy/raspberry/hidro360-cli.sh" scripts.crear_admin ;;
    esac
  else
    echo "Sin terminal interactiva: cree el administrador luego con  sudo $APP_DIR/deploy/raspberry/hidro360-cli.sh scripts.crear_admin"
  fi
fi

# --- 8. Arranque ---------------------------------------------------------------------------
if [ "${NO_START:-0}" = "1" ]; then
  echo "NO_START=1: servicios instalados pero no iniciados."
else
  msg "Habilitando e iniciando servicios"
  systemctl enable --now hidro360-api.service hidro360-ui.service hidro360-backup.timer
  systemctl try-restart hidro360-api.service hidro360-ui.service   # aplica el código nuevo en una actualización
fi

ip_lan="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

Listo. Desde otro equipo de la RED LOCAL:
  Panel web:  http://${ip_lan:-<IP-de-la-Raspberry>}:8501
  API:        http://${ip_lan:-<IP-de-la-Raspberry>}:8000/docs
Estado:   $APP_DIR/deploy/raspberry/healthcheck.sh        Logs:  journalctl -u hidro360-api -f
IMPORTANTE: el tráfico es HTTP sin cifrar. No haga port-forwarding ni exponga estos puertos a Internet.
Firewall sugerido (ajuste la subred; ejecútelo usted, el instalador no toca el firewall):
  sudo apt install ufw && sudo ufw default deny incoming && sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
  sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp && sudo ufw allow from 192.168.1.0/24 to any port 8501 proto tcp && sudo ufw enable
EOF
