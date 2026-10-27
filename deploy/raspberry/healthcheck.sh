#!/usr/bin/env bash
# Comprobación de salud de HIDRO360: unidades systemd, GET /api/v1/salud, panel web y espacio en disco.
# Código de salida 0 = todo bien; 1 = algo falla. Puede ejecutarse sin sudo.
# SIN PROBAR en una Raspberry (solo `bash -n`).
API="${API_URL:-http://127.0.0.1:8000}"
UI="${UI_URL:-http://127.0.0.1:8501}"
DATA="${DATA_DIR:-/var/lib/hidro360}"
fallo=0

if command -v systemctl >/dev/null; then
  for u in hidro360-api hidro360-ui; do
    if systemctl is-active --quiet "$u"; then echo "unidad $u: activa"; else echo "unidad $u: NO activa"; fallo=1; fi
  done
  systemctl is-active --quiet hidro360-backup.timer && echo "unidad hidro360-backup.timer: activa" || echo "AVISO: hidro360-backup.timer inactivo (sin copias automáticas)"
else
  echo "systemctl no disponible: solo se comprueba HTTP"
fi

if resp="$(curl -fsS --max-time 5 "$API/api/v1/salud")"; then echo "API ok: $resp"; else echo "API NO responde en $API"; fallo=1; fi
if curl -fsS --max-time 5 -o /dev/null "$UI/"; then echo "Panel ok"; else echo "Panel NO responde en $UI"; fallo=1; fi

if [ -d "$DATA" ]; then
  uso="$(df --output=pcent "$DATA" | tail -n 1 | tr -dc '0-9')"
  if [ -n "$uso" ] && [ "$uso" -ge 90 ]; then echo "Disco $DATA al ${uso}%: NO hay espacio suficiente"; fallo=1; else echo "Disco $DATA: ${uso:-?}% usado"; fi
fi
exit $fallo
