# Instalación, configuración y primera demo (Windows y Raspberry Pi)

La API en Vercel + Supabase (nube) se describe en **`docs/DESPLIEGUE.md`**. Las secciones 1-8 son para Windows (desarrollo y demo local); la **sección 9** es el despliegue en Raspberry Pi. Todo en una terminal, desde la raíz del repositorio. **La demo básica no necesita internet, claves ni hardware** (modo local).

## 1. Requisitos
Python 3.10+ (probado con 3.14.7). Comprobar: `python --version`.

## 2. Instalar
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -c "import flet, pydantic, fastapi, google.genai; print('imports ok')"
```
`requirements.txt` instala el entorno de desarrollo completo (incluye `flet-desktop` y las pruebas) a partir de `requirements-servidor.txt`, que es lo único que necesita un servidor (sección 9).

**Primera ejecución de escritorio: necesita internet UNA vez.** `flet-desktop` es un paquete pequeño; al primer arranque Flet muestra «Preparing Flet … one-time operation» y descarga su cliente a `%USERPROFILE%\.flet\client\` (se comprobó que esa carpeta se crea). Después funciona sin internet. Para un equipo sin internet, copie esa carpeta `.flet\client` desde otro equipo con la misma versión (no probado).

**Atajo:** `scripts\dev.ps1` automatiza esto (venv, dependencias, `.env` sin pisar, administrador, lanzar API/UI): `.\scripts\dev.ps1 instalar`, `admin`, `ui`, `web`, `api`, `todo`, `pruebas`, `secretos`. Si PowerShell bloquea scripts: `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 instalar`.

## 3. Probar el entorno
```bash
pytest -q                      # usa bases y trazas temporales (no toca agua_clara.db)
```

## 4. Iniciar
```bash
python app.py                  # ventana de escritorio (funciona sin internet)
python app.py --web            # navegador en http://127.0.0.1:8501 (el navegador carga Flet desde internet: ver docs/DECISIONES.md D-04; la 1.ª carga tarda 15-20 s)
uvicorn api.main:app --port 8000   # API opcional, en otra terminal → http://localhost:8000/docs
```
Comprobar la API: abrir `http://localhost:8000/api/v1/salud` → `{"ok": true, ...}`.

## 5. Primera demostración (≈5 min)
1. **Cree el primer administrador** (no hay cuentas por defecto): `python -m scripts.crear_admin` (pide usuario y contraseña de 10+ caracteres con mayúsculas, minúsculas y número).
2. Abra la app: verá el **portal público** (Inicio · Reportar · Consultar mi reporte · Avisos). **Reportar** → escriba «En Vista Alegre no tenemos agua desde ayer» → se guarda con su texto original y muestra un **código de seguimiento** `HID-…`; **Consultar mi reporte** con ese código muestra solo el estado público.
3. **Acceso administración** → inicie sesión. Con la base vacía: **Diagnóstico y desarrollo → Cargar datos de ejemplo** y confirme (ficticios; 19 reportes, 15 sectores, 6 cisternas).
4. **Revisión y validación** → corrija/complete (p. ej. personas = 120) → **Confirmar**. Confirme otros reportes (el #19 de la demo no trae sector: elíjalo).
5. **Ubicación y duplicados** → decida «sí/no» en los candidatos (nada se fusiona solo).
6. **Priorización** → ranking con desglose; **Explicar con IA** (modo local: texto de reglas).
7. **Planificación y aprobación** → **Generar propuesta**, edite una asignación (pide motivo), **Aprobar** (diálogo de confirmación) o **Rechazar** (exige motivo).
8. **Comunicación** (borradores, no se envían) · **Auditoría** (cadena íntegra) · **Métricas operativas**; en *Diagnóstico y desarrollo*: **Trazas**, **Evaluación e impacto**, **Verificar Gemini**.

## 6. Configuración opcional
Copie `.env.example` a `.env` (`copy .env.example .env`). **No hace falta tocar nada para la primera prueba.** `config.py` lee `.env` al iniciar; una variable ya definida en el entorno tiene prioridad sobre `.env`.
| Variable | Para qué | Obligatoria |
|---|---|---|
| `AI_PROVIDER` | `auto` (Gemini solo con clave) · `local` · `gemini` | no |
| `GEMINI_API_KEY` | activar Gemini | solo para Gemini |
| `GEMINI_MODEL` | cambiar de modelo sin editar código | no |
| `AI_TIMEOUT_SECONDS`, `AI_MAX_REINTENTOS` | límites de tiempo/reintentos | no |
| `HIDRO360_API_TOKEN` | token de servicio de la API (administración desde otros sistemas) | solo para usar la API desde la red |
| `HIDRO360_DEVICE_TOKEN` | token de dispositivo **heredado** (pantalla de monitoreo). Las terminales de cisterna usan un token propio por vehículo, creado con `python -m scripts.registrar_dispositivo CIS-01` | no |
| `HIDRO360_DB`, `HIDRO360_TRACE` | rutas de la base SQLite y de las trazas JSONL | no |
| `HIDRO360_ENV` | perfil `dev` (defecto) · `test` (no lee `.env`) · `prod` | no |
| `DATABASE_URL` | **secreta.** PostgreSQL/Supabase en lugar de SQLite (opcional también en la Raspberry); pooler de sesión 5432 para procesos persistentes | no |
| `HIDRO360_AUTH`, `HIDRO360_STORAGE`, `SUPABASE_*`, `HIDRO360_CORS_ORIGINS` | modo nube (Supabase Auth/Storage). `SUPABASE_SERVICE_ROLE_KEY` y `SUPABASE_JWT_SECRET` son **secretas** | solo en la nube |
| `HIDRO360_MEDIA_DIR` | carpeta de fotografías de los reportes (la comparten la interfaz y la API; por defecto `media/`) | no |

Los límites de fotos (3 por reporte, 3 MB cada una) son constantes de `config.py`, no variables de entorno.

**Compatibilidad / obsolescencia:** los nombres antiguos `AGUA_CLARA_DB`, `AGUA_CLARA_TRACE`, `AGUA_CLARA_API_TOKEN` y `AGUA_CLARA_DEVICE_TOKEN` siguen funcionando como alias **obsoletos**. Si existen ambos nombres, gana `HIDRO360_*`. No los use en instalaciones nuevas.

### Configurar `GEMINI_API_KEY` localmente
1. Cree una clave en <https://aistudio.google.com/apikey>.
2. En `.env` (nunca en el código ni en Git) escriba una línea `GEMINI_API_KEY=` seguida de su clave. Alternativa solo para la terminal actual: `set GEMINI_API_KEY=...` (cmd) o `$env:GEMINI_API_KEY="..."` (PowerShell).
3. Compruebe: `python -m scripts.verificar_gemini --sin-llamada` y luego sin el parámetro. En la Raspberry la línea va en `/etc/hidro360/hidro360.env` y se aplica con `sudo systemctl restart hidro360-api hidro360-ui`.

### Verificar Gemini
```bash
python -m scripts.verificar_gemini --sin-llamada   # solo configuración (no usa red ni cuota)
python -m scripts.verificar_gemini                  # lista modelos y hace una llamada mínima
```
Sin clave el comando responde «Gemini no está configurado: el sistema usa el modo local» (código 2) y la app sigue funcionando. Guía completa: `docs/IA_GEMINI.md`.

### Forzar el modo local
`AI_PROVIDER=local` (en `.env` o `set AI_PROVIDER=local` antes de iniciar).

## 7. Detener y reiniciar la demo
Cierre la ventana (o `Ctrl+C` en la terminal). Si la ventana no aparece en la primera ejecución, revise la conexión (descarga del cliente). **Reiniciar datos**: botón «Cargar datos de ejemplo» (borra reportes, planes y auditoría de la base local; copie antes `agua_clara.db` si necesita conservarla).

## 8. Errores frecuentes
| Síntoma | Causa y solución |
|---|---|
| `ModuleNotFoundError: flet` | Falta activar el venv o `pip install -r requirements.txt`. |
| `--web`: página en blanco 15-20 s | Primera carga de CanvasKit desde internet; espere. Sin internet use `python app.py`. |
| `--web`: página en blanco para siempre | Sin acceso a internet en el navegador. Use la app de escritorio. |
| Los mapas salen en blanco | Las teselas de OpenStreetMap las descarga el navegador o la ventana del cliente (necesita internet; el servidor no). El resto de la pantalla funciona. |
| Puerto 8501/8000 ocupado | Use `--port 8502` / `--port 8001`. |
| La API devuelve 403 desde otra máquina | Sin tokens solo acepta localhost: defina `HIDRO360_API_TOKEN` (o registre una terminal de dispositivo). |
| «Gemini no estuvo disponible… modo local» en un reporte | Falta clave, cuota agotada (429), modelo inexistente o sin red: ver Trazas (estado `fallback`) y `scripts.verificar_gemini`. |
| «Elija un sector válido» al confirmar | El sector debe ser del catálogo; elíjalo en el desplegable. |
| «Transición no permitida» | El reporte/plan ya está en un estado final (rechazado, duplicado, aprobado…). |

## 9. Despliegue en Raspberry Pi (servidor en la red local)
> **Estado:** el equipo ejecutó `install.sh` en una Raspberry Pi (Raspberry Pi OS Lite 64-bit, Python 3.13): las unidades `hidro360-api` y `hidro360-ui` quedaron activas y `healthcheck.sh` informó **API ok** y **Panel ok** **después de un ajuste manual** (ver «Panel con error 500» más abajo). Las copias de seguridad y el resto de scripts no se probaron en la Pi.
>
> **Panel con error 500 («Web root path not found: …/flet_web/web»):** en esa instalación `pip` descargó un `flet_web-1.0.4` de **25 kB**, sin la carpeta `web`, mientras que el wheel oficial de PyPI pesa **27,3 MB** y sí la incluye (comprobado en Windows: 85 archivos, idénticos por SHA-256 a los del wheel oficial). Causa **probable, no confirmada**: el índice `piwheels` que Raspberry Pi OS añade por defecto sirvió un wheel recortado. Solución **verificada** (copiar la carpeta desde un venv de Windows y dar lectura al usuario del servicio): `scp -r .venv\Lib\site-packageslet_web\web USUARIO@PI:/tmp/flet_web_web`, luego en la Pi `sudo cp -r /tmp/flet_web_web /opt/hidro360/.venv/lib/python3.13/site-packages/flet_web/web`, `sudo chmod -R a+rX` sobre esa carpeta y `sudo systemctl restart hidro360-ui`. Alternativa **sin probar**: reinstalar solo desde PyPI con `pip install --force-reinstall --no-cache-dir --index-url https://pypi.org/simple flet-web==1.0.4`. Si `pip check` se queja de `websockets`, ya está fijado en `requirements-servidor.txt` (`>=13,<17`).

Requisitos: Raspberry Pi 4, Raspberry Pi OS **64-bit** (probado con Python 3.13; Bookworm trae 3.11), red local, acceso `sudo`. El servidor usa `requirements-servidor.txt` (flet, flet-web, flet-map, google-genai, pydantic, fastapi, uvicorn): **sin** `flet-desktop` (ventana nativa, innecesaria) **ni** pytest.

### 9.1 Instalar
```bash
sudo apt update && sudo apt install -y git
sudo git clone <URL-del-repositorio> /opt/hidro360
cd /opt/hidro360
sudo bash deploy/raspberry/install.sh
```
El instalador es idempotente (puede ejecutarse de nuevo tras `git pull` para actualizar):
- crea el usuario de sistema sin privilegios `hidro360` y los directorios `/var/lib/hidro360/{db,media,logs,backups}` (los datos viven **fuera** de la carpeta del código);
- crea el venv `/opt/hidro360/.venv` e instala `requirements-servidor.txt`;
- genera `/etc/hidro360/hidro360.env` (permisos `600`; solo root/systemd lo leen) con rutas y **tokens aleatorios** (`openssl rand`), sin claves de Gemini; **nunca lo sobrescribe**;
- instala y arranca `hidro360-api` (uvicorn, puerto 8000), `hidro360-ui` (panel web Flet, puerto 8501) y `hidro360-backup.timer` (copia diaria a las 03:15);
- ofrece crear el primer administrador (interactivo; no existe contraseña por defecto).

Con `NO_START=1 sudo bash deploy/raspberry/install.sh` solo instala, sin arrancar.

### 9.2 Primer administrador y terminales de cisterna
```bash
sudo /opt/hidro360/deploy/raspberry/hidro360-cli.sh scripts.crear_admin
sudo /opt/hidro360/deploy/raspberry/hidro360-cli.sh scripts.registrar_dispositivo CIS-01
```
`hidro360-cli.sh` ejecuta el módulo como el usuario `hidro360` con la configuración de producción (así la base conserva el dueño correcto). `registrar_dispositivo` entrega el **token propio de ese vehículo**: cópielo en `firmware/esp32s3_monitor/config_dispositivo.h` (la terminal lo envía en la cabecera `X-Device-Token`). Repita por cada cisterna. En Windows el mismo comando es `python -m scripts.registrar_dispositivo CIS-01`.

### 9.3 Acceder desde otro dispositivo de la red local
Averigüe la IP con `hostname -I`. Desde otro equipo de la misma red: panel `http://<IP>:8501`, API `http://<IP>:8000/docs`. El navegador del cliente necesita internet (Flet y mapas de OpenStreetMap). Los reportes y fotos se guardan en la Raspberry.

### 9.4 Firewall y exposición
Los servicios escuchan en `0.0.0.0` **solo para atender a la red local**. **No haga port-forwarding ni abra 8000/8501 en el router**: el tráfico es HTTP sin cifrar (ver `docs/SEGURIDAD.md`). El instalador no toca el firewall; restrinja a su subred (ajuste `192.168.1.0/24` y mantenga el acceso SSH antes de activar):
```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 8501 proto tcp
sudo ufw enable
```

### 9.5 Logs, estado y copias
```bash
journalctl -u hidro360-api -f          # logs de la API (journald)
journalctl -u hidro360-ui -f           # logs del panel
systemctl status hidro360-api hidro360-ui
/opt/hidro360/deploy/raspberry/healthcheck.sh      # unidades + GET /api/v1/salud + panel + disco (código 0 = ok)
sudo systemctl start hidro360-backup               # copia ahora; el temporizador la hace a diario
ls /var/lib/hidro360/backups
```
`backup.sh` hace una copia **consistente** de SQLite (API de backup + `integrity_check`), un `.tar` de las fotos y una copia de las trazas; nunca sobrescribe y conserva las últimas 14 de cada tipo (`BACKUP_KEEP`). Las copias quedan en la misma tarjeta SD: cópielas periódicamente a otro medio. Restaurar: detenga los servicios, copie `hidro360-<fecha>.db` a `/var/lib/hidro360/db/hidro360.db` (dueño `hidro360`) y arranque de nuevo.

### 9.6 Configuración y Gemini
Edite `/etc/hidro360/hidro360.env` (`sudo nano`) y reinicie: `sudo systemctl restart hidro360-api hidro360-ui`. Para Gemini añada una línea `GEMINI_API_KEY=` con su clave; la Raspberry solo necesita salida a internet para eso.

### 9.7 Limitaciones del despliegue
- La opción «Evaluación e impacto» del panel regenera archivos en `docs/`, que el servicio no puede escribir (`ProtectSystem=strict`; el código es de solo lectura): ejecute esas evaluaciones en el equipo de desarrollo.
- Desinstalar: `sudo systemctl disable --now hidro360-api hidro360-ui hidro360-backup.timer` y borre las unidades de `/etc/systemd/system`; los datos de `/var/lib/hidro360` no se tocan.

### 9.8 Raspberry con Supabase (opcional)
La Raspberry puede usar la base de Supabase en lugar de SQLite: añada `DATABASE_URL` (pooler de sesión, 5432) y las variables `HIDRO360_STORAGE`/`SUPABASE_*` a `/etc/hidro360/hidro360.env` y reinicie los servicios. Sin `DATABASE_URL` nada cambia (SQLite en `/var/lib/hidro360/db`). El instalador sigue generando el archivo desde `.env.example` (todas las variables de nube están comentadas). Esta combinación **no se ha probado**. Las copias de `backup.sh` cubren solo SQLite y fotos locales.
