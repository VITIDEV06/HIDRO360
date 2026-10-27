# HIDRO360 · TEAM VIFY AI

Sistema de coordinación de **reportes de falta de agua** y propuesta de distribución de cisternas en Panamá Oeste, con
**supervisión humana obligatoria** y **auditoría verificable**. Entrega al reto HIDRA — Reto 01.

> **Datos ficticios.** Sectores, cisternas y reportes de la demostración son inventados. El experimento de impacto es
> **sintético**: no es evidencia de impacto real.
> **Principio:** la IA propone, las reglas deterministas calculan, la persona aprueba; nada sensible ocurre solo.

**Equipo:** Victor David Montes (@VITIDEV06) · Frida Leis (@yukki2126) · Yohany Kavacila (@YOHADEV21)

## Qué es y qué problema resuelve

En Panamá Oeste los reportes de falta de agua llegan por canales informales: incompletos, duplicados y sin rastro de quién
decidió qué. HIDRO360 recibe los reportes, sugiere sector y duplicados, **prioriza con una fórmula transparente**
(40 % horas sin servicio · 30 % población afectada · 30 % vulnerabilidad), propone la **asignación de cisternas
respetando capacidad** y exige **aprobación humana**. Todo queda auditado con cadena de hashes. Funciona **sin Internet ni
claves** (modo local); Gemini es opcional y, si falla, se usa un modo local declarado.

## Tecnologías

| Capa | Tecnología |
|---|---|
| Interfaz | **Flet 1.0.4** (portal público sin cuenta + panel de administración con login) |
| API | **FastAPI** + Uvicorn (`api/main.py`) |
| Negocio | Python en `services/` (única puerta: `services/workflow.py`) |
| Datos | **SQLite** (demostración local) · PostgreSQL/Supabase (`DATABASE_URL`, preparado) · Pydantic |
| IA (opcional) | **Gemini** (SDK `google-genai`) con llamadas a herramientas y respaldo local |
| Mensajería | Bot de **Telegram** por sondeo (`scripts/bot_telegram.py`) |
| Hardware | **ESP32-S3** (terminal de cisterna, `firmware/`) · Raspberry Pi 4 (`deploy/raspberry/`) |

## Requisitos e instalación local

Windows con PowerShell y Python 3.10+ (probado con 3.14):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Iniciar la demostración

`scripts/demo_local.ps1` **fuerza SQLite** aunque `.env` tenga una `DATABASE_URL` de Supabase. Los datos quedan en
`datos_demo\hidro360_demo.db` (ignorado por Git). Desde la carpeta del proyecto:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/demo_local.ps1
```

Muestra la URL (normalmente `http://127.0.0.1:8501/`; si el puerto está ocupado usa el siguiente, sin detener procesos
ajenos). La primera carga de Flet puede tardar 15–30 s.

## Administradores y bot de Telegram

```powershell
powershell -ExecutionPolicy Bypass -File scripts/demo_local.ps1 -CrearAdmin       # primer administrador (clave oculta)
powershell -ExecutionPolicy Bypass -File scripts/demo_local.ps1 -ListarAdmins     # usuario, rol y estado (nunca claves)
powershell -ExecutionPolicy Bypass -File scripts/demo_local.ps1 -RestablecerAdmin # cambiar la clave de una cuenta existente
powershell -ExecutionPolicy Bypass -File scripts/demo_local.ps1 -Bot              # bot de Telegram (otra terminal)
```

No existen cuentas por defecto. `-CrearAdmin` **no reemplaza** cuentas existentes.
**Si al escribir el usuario o la contraseña se queda esperando:** use una ventana de PowerShell normal (no Git Bash ni un panel integrado). En terminales sin consola real el script avisa y la clave **se verá** al escribirla. Opción sin preguntas, solo para esa terminal: `$env:HIDRO360_ADMIN_USUARIO="admin"; $env:HIDRO360_ADMIN_CLAVE="SuClave123"` y después `-CrearAdmin`. Entre en el portal por «Acceso
administración»; en el menú *Usuarios* los administradores crean más cuentas. La clave mínima: 10 caracteres con
mayúscula, minúscula y número.

## Variables de entorno

Se leen de `.env` (ignorado por Git; copie `.env.example`) o del entorno de la terminal (`$env:NOMBRE = "..."`). **Nunca**
suba valores reales.

| Variable | Para qué | Dónde obtenerla |
|---|---|---|
| `GEMINI_API_KEY` | IA opcional del asistente y la interpretación | https://aistudio.google.com/apikey |
| `GEMINI_MODEL`, `AI_PROVIDER` | modelo y modo (`auto`/`local`/`gemini`) | — |
| `TELEGRAM_BOT_TOKEN` | bot de Telegram | BotFather → `/newbot` (`/revoke` si se expuso) |
| `HIDRO360_BD` | `sqlite` fuerza SQLite (el lanzador lo fija) | — |
| `HIDRO360_DB`, `HIDRO360_ENV` | archivo SQLite y perfil (`dev`/`test`/`prod`) | — |
| `DATABASE_URL`, `SUPABASE_*` | PostgreSQL/Supabase (solo producción) | panel de Supabase |

Sin claves la plataforma funciona: el asistente responde con reglas locales.

## Funcionalidades y verificación real (17-oct-2026)

| Función | Estado |
|---|---|
| Portal: reporte (texto, mapa, fotos), código de seguimiento y consulta por código | **Verificado** en navegador (local) y con pruebas |
| Panel de administración: bandeja, prioridades, plan, usuarios | Pruebas automatizadas; recorrido visual solo parcial |
| Administradores guardados en SQLite, con hash y permisos | **Verificado** (pruebas, reinicio de proceso y `-ListarAdmins`) |
| Asistente: reglas locales | **Verificado** (pruebas) |
| Asistente con Gemini y herramientas | Llamada real **verificada por script** (consulta de un reporte); el envío desde la pantalla no se probó en el navegador |
| Bot de Telegram (`/start`, `/ayuda`, `/reportar`, `/consultar`) | Solo pruebas **simuladas**. **No probado con Telegram real** |
| Firmware ESP32-S3 | Compila y su protocolo se verificó con un simulador. **No probado en la placa** |
| Raspberry Pi | Instalada por el equipo: API y panel respondieron (`healthcheck.sh`); ver `docs/INSTALACION.md` |
| Supabase | Migraciones aplicadas (15 tablas con RLS); la aplicación **no está operativa en producción** |
| Vercel | API desplegada, pero `/api/v1/salud` responde 503: no conecta con la base de datos |

**Pruebas:** `pytest -q`. Última ejecución en la sesión de cierre: **414 pasaron** (administradores, asistente y bot,
autorización, interfaz, despliegue y salud). Detalle y evaluación de casos: `docs/PRUEBAS.md`,
`python -m evaluation.run_cases`, `python -m scripts.revisar_secretos`. Regla del proyecto: **si no se ejecutó, no se afirma**.

## Limitaciones conocidas

- **Producción no operativa:** la conexión de Vercel con Supabase falla. La demostración usa SQLite local.
- **Telegram** nunca se probó contra el servicio real; **ESP32** nunca en una placa física (sin GPS).
- **Credenciales expuestas durante el desarrollo** (token de Telegram, clave de Gemini, contraseña de Supabase): deben rotarse.
- Datos y experimento de impacto ficticios/sintéticos; extracción por regex limitada; el plan no modela rutas ni ETA.
- No existe el estado «resuelto»: los estados son recibido/por revisar, confirmado, en atención, agrupado y no procede.
- Sin HTTPS, MFA ni recuperación de contraseña. El mapa necesita Internet (OpenStreetMap).
- El logo es un JPEG sin transparencia.

## Más documentación

`docs/INSTALACION.md` · `docs/ARQUITECTURA.md` · `docs/SEGURIDAD.md` · `docs/API.md` · `docs/BASE_DE_DATOS.md` ·
`docs/IA_GEMINI.md` · `docs/HARDWARE.md` (ESP32-S3 y Raspberry) · `docs/DESPLIEGUE.md` · `docs/PRUEBAS.md` ·
`docs/GUIA_DEMO_EN_VIVO.md` · `docs/BITACORA_IA.md` · `CHANGELOG.md` · `AGENTS.md`
