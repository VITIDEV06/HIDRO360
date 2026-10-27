#!/usr/bin/env bash
# Copia de seguridad de HIDRO360: base SQLite (copia CONSISTENTE con la API de backup de SQLite, válida aunque
# los servicios estén escribiendo), fotos (media/) y trazas. Nunca sobrescribe copias anteriores; rota las antiguas.
# Lo ejecuta hidro360-backup.timer (diario) o a mano:  sudo -u hidro360 env $(...)  /  systemctl start hidro360-backup
# Variables: HIDRO360_DB, HIDRO360_MEDIA_DIR, HIDRO360_TRACE, BACKUP_DIR, BACKUP_KEEP (copias a conservar por tipo, 14).
# SIN PROBAR en una Raspberry (solo `bash -n`).
set -euo pipefail

DB="${HIDRO360_DB:-${AGUA_CLARA_DB:-/var/lib/hidro360/db/hidro360.db}}"
MEDIA="${HIDRO360_MEDIA_DIR:-/var/lib/hidro360/media}"
TRACE="${HIDRO360_TRACE:-${AGUA_CLARA_TRACE:-/var/lib/hidro360/logs/trazas.jsonl}}"
DEST="${BACKUP_DIR:-/var/lib/hidro360/backups}"
KEEP="${BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

[ -f "$DB" ] || { echo "No existe la base: $DB" >&2; exit 1; }
mkdir -p "$DEST" && chmod 700 "$DEST"

# 1) Base de datos: copia a un temporal, verificación de integridad y renombrado sin pisar nada.
final="$DEST/hidro360-$STAMP.db"
tmp="$final.parcial"
[ ! -e "$final" ] || { echo "Ya existe $final; no se sobrescribe." >&2; exit 1; }
python3 - "$DB" "$tmp" <<'PY'
import sqlite3, sys
origen = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
destino = sqlite3.connect(sys.argv[2])
origen.backup(destino)
resultado = destino.execute("PRAGMA integrity_check").fetchone()[0]
destino.close(); origen.close()
if resultado != "ok":
    sys.exit(f"La copia no pasa integrity_check: {resultado}")
PY
mv -n "$tmp" "$final"

# 2) Fotos (si hay) y trazas.
if [ -d "$MEDIA" ] && [ -n "$(ls -A "$MEDIA" 2>/dev/null)" ]; then
  tar -C "$MEDIA" -cf "$DEST/media-$STAMP.tar" .
fi
[ -f "$TRACE" ] && cp -n "$TRACE" "$DEST/trazas-$STAMP.jsonl" || true

# 3) Rotación: conserva las KEEP más recientes de cada tipo (solo archivos con nuestro patrón).
for patron in 'hidro360-*.db' 'media-*.tar' 'trazas-*.jsonl'; do
  ls -1t "$DEST"/$patron 2>/dev/null | tail -n +"$((KEEP + 1))" | while IFS= read -r viejo; do rm -f -- "$viejo"; done
done
find "$DEST" -name '*.parcial' -mmin +60 -delete 2>/dev/null || true

echo "Copia creada en $DEST ($STAMP); se conservan las últimas $KEEP de cada tipo."
