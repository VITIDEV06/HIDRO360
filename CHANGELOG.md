# Changelog


## Sin versión — 2026-10-17 (demostración local, asistente, bot y administradores)
### Añadido
- `scripts/demo_local.ps1`: arranque de la demostración con SQLite forzado (`HIDRO360_BD=sqlite`) y opciones `-CrearAdmin`, `-RestablecerAdmin`, `-ListarAdmins` y `-Bot`.
- Pantalla «Asistente» (reglas locales y, con `GEMINI_API_KEY`, Gemini con herramientas validadas; guardar un reporte exige confirmación).
- `scripts/bot_telegram.py`: bot de Telegram por sondeo con flujo guiado (probado solo con simulaciones).
- Salud: `/api/v1/salud` comprueba la base de verdad (503 si no responde); la interfaz y la API no arrancan en producción sin `DATABASE_URL`.
### Cambiado
- `scripts/crear_admin` ya no reemplaza cuentas existentes salvo `--restablecer`.
- README reescrito con el estado verificado; documentación de la Raspberry actualizada con lo que se probó.

## 0.6.2 — 2026-10-17 (limpieza física del repositorio y reorganización)
### Retirado
- **`IMPLEMENTACIONES_EJEMPLO/`** completo (laboratorio de IA «Agua Clara AI», 45 archivos): prototipo no integrado; **borrado del disco y de Git**. La decisión pasó de «documentar, no integrar» a **eliminar**, al confirmarse que no se importa ni se ejecuta en el producto.
- **`docs/IMPLEMENTACIONES_EJEMPLO.md`**, **`docs/AUDITORIA_TECNICA.md`**, **`docs/INVENTARIO_ARCHIVOS.md`**, **`docs/INVENTARIO_TECNICO.md`**: documentación histórica/auditorías previas que citaban carpetas ya inexistentes y no son necesarias para operar, desplegar ni explicar la entrega actual.
- **`docs/GUION_VIDEO.md`**: guion de un video **no grabado**; la demo viva vigente es `docs/GUIA_DEMO_EN_VIVO.md`.
- **`data/ENTREGA1.ok`**: marcador de texto de la entrega 1 sin ninguna referencia en código, pruebas ni docs.
- **`evaluation/resultados/impacto.json`** y **`evaluation/resultados/ultimo.json`**: salidas regenerables de `run_impacto`/`run_cases` que ya **no** se versionan (se regeneran con los comandos); ahora ignoradas por `.gitignore`.
- **`supabase/.temp/cli-latest`**: caché de la CLI de Supabase (versión), ya cubierta por `.gitignore`.
- **`.tools/`** (`node_modules` de `embedded-postgres`): borrado **del disco** (antes solo se había retirado del índice); se reinstala con `npm install embedded-postgres --prefix .tools/pgnode`.
### Cambiado
- Referencias rotas corregidas: `docs/DECISIONES.md` (D-01 y D-02 apuntaban a `INFO DEL PROYECTO/`, `LOGO/`, `PROTOTIPO/` y al inventario retirado), `docs/DISENO_UI.md`, `docs/BITACORA_IA.md`, `interface/flet_app/theme.py` (citaba `PROTOTIPO/hidro360_prototipo.html`) y `.claude/skills/hidro360-flet-ui/SKILL.md`.
- `.gitignore`: se añade `evaluation/resultados/*.json` (los JSON de resultados se regeneran, no se versionan; el directorio se recrea en cada ejecución).
### Conocido
- Sin cambios de comportamiento: es limpieza de repositorio. `pytest -q` y `python -m evaluation.run_cases` se re-ejecutaron tras la limpieza (ver `docs/PRUEBAS.md` y `docs/RESULTADOS_EVALUACION.md`).

## 0.6.1 — 2026-10-17 (entrega HIDRA — Reto 01: documentación y limpieza)
### Añadido
- `README.md` reescrito con las secciones de la entrega (equipo, problema, impacto, arquitectura de agentes, tecnologías, instalación, evals, robustez, bitácora, demo, estado Supabase/Vercel/ESP32 y limitaciones). Aclara que **HIDRO360** es el producto y **HIDRA — Reto 01** la entrega.
- `docs/ARQUITECTURA.md`: **arquitectura de 8 roles** (ingesta; interpretación local/Gemini + fallback; sector y duplicados; priorización determinista; planificador; orquestador de recomendaciones; supervisor humano; terminal ESP32-S3) con entrada/salida/herramientas/autonomía/guardrails/límites/aprobación humana y diagrama Mermaid.
- `docs/BITACORA_IA.md` (bitácora de uso de IA con tabla de decisiones) y `docs/GUIA_DEMO_EN_VIVO.md` (demo de 5–7 min en 8 pasos + plan B offline).
### Cambiado
- Estado de **Gemini** reconciliado: verificado contra el servicio real el 2026-10-17 (`docs/IA_GEMINI.md`); se corrigieron las afirmaciones «no probado» en `README.md`, `AGENTS.md` y `docs/PRUEBAS.md`.
- `docs/PRUEBAS.md` actualizado: 19 archivos de prueba (antes 6) y resultados de la entrega (`pytest`: 638 passed, 46 skipped; `run_cases`: 22 PASS, 0 FAIL).
- `.tools/` (1499 archivos, ≈ 97 MB de `node_modules` de `embedded-postgres`) **retirado del control de versiones** con `git rm -r --cached .tools`; se conserva en disco y `.gitignore` ya lo excluía. Las instrucciones (`scripts/postgres_prueba.py`, `docs/BASE_DE_DATOS.md`) siguen válidas (se reinstala con `npm install`).
- Referencias a carpetas inexistentes (`LOGO/`, `PROTOTIPO/`) corregidas en `README.md` y `AGENTS.md`.
### Conocido
- Sin cambios en las limitaciones: hardware sin probar en placa, Raspberry sin probar, Supabase/Vercel sin desplegar, datos de la demo ficticios.

## 0.6.0 — 2026-10-17 (MVP integrado: fotos, mapa, IA orquestada, flota)
### Añadido
- Reportes con **coordenadas reales** (mapa) y hasta 3 **fotos** validadas por firma binaria, guardadas en `HIDRO360_MEDIA_DIR` (fuera de Git); solo el administrador las ve.
- **Flota**: un token por vehículo (`X-Device-Token`, solo se guarda el hash), misiones con paradas, eventos de terminal idempotentes por `uid`, `/api/v1/mapa`.
- **Orquestación IA** (`services/orquestador.py`, `workflow_ia.py`): validación → duplicados → clasificación → prioridad → recomendación → asignación; procedencia por etapa (`gemini`/`reglas`/`reglas(fallback)`); la misión solo nace con aprobación humana.
- Interfaz: mapa en el formulario y en administración, fotos, «Recomendaciones IA», «Misiones y flota».
- Firmware de terminal de cisterna (misión, botones táctiles, cola de eventos en NVS, reintentos); compila con `arduino-cli`; `scripts/simular_terminal.py`.
- Raspberry: `requirements-servidor.txt`, `install.sh` idempotente, usuario sin privilegios, datos en `/var/lib/hidro360`, timer de copias, `hidro360-cli.sh`.
- Prueba extremo a extremo con servidor uvicorn real y reinicio (`tests/test_e2e.py`).
### Cambiado
- Variables `HIDRO360_*` (alias `AGUA_CLARA_*` obsoletos). SCHEMA_VERSION 1.3.
### Conocido
- Gemini real y Raspberry/systemd sin probar; firmware sin probar en placa.

## 0.5.0 — 2026-10-17 (reorganización y roles)
### Cambiado
- **Nombre oficial HIDRO360** (equipo TEAM VIFY AI) en interfaz, configuración, documentación, deploy y firmware. Las variables de entorno conservan el prefijo `AGUA_CLARA_` por compatibilidad.
- **Interfaz separada en dos experiencias**: portal público (reportar, consultar con código de seguimiento, avisos) y consola de administración tras login (`interface/flet_app/portal/` y `admin/`). Las herramientas técnicas (trazas, evaluación, verificación de Gemini, carga de datos ficticios) viven en «Diagnóstico y desarrollo», solo para administradores.
- Servicios systemd de la Raspberry renombrados a `hidro360-*`.
### Añadido
- **Autenticación y roles** (`services/auth.py`): scrypt con sal, sesiones con hash, bloqueo por intentos, sin cuentas por defecto (`python -m scripts.crear_admin`). Autorización en el backend (`@requiere_admin`) y en cada endpoint de la API; portal público con límites de tasa; `tests/test_auth.py`.
- Tabla de pines del ESP32-S3 con fuentes y correcciones del firmware (aviso de confirmación separado, timeouts).
### Conocido
- Sesiones y límites de tasa en memoria; sin HTTPS ni MFA; firmware sin compilar ni probar en placa; Gemini sin probar con el servicio real.

> Nombre oficial: **HIDRO360** (TEAM VIFY AI); antes «Agua Clara AI». Las entradas históricas conservan el contexto de su fecha.

## Sin versión (en curso)
### Cambiado
- La evaluación funcional fuerza el intérprete local determinista: ya no depende de una clave Gemini configurada en el entorno de quien la ejecuta ni envía sus casos a la red.
- Identidad unificada a HIDRO360: documentación, skills (`.claude/skills/hidro360-*`), servicios systemd (`hidro360-api.service`, `hidro360-ui.service`; rutas `/opt/hidro360`, `/etc/hidro360`, `/var/lib/hidro360`, usuario `hidro360`). Las variables de entorno conservan el prefijo `AGUA_CLARA_` por compatibilidad.
- Firmware ESP32-S3: texto en pantalla HIDRO360; los fallos al confirmar recepción ya no muestran «SIN DATOS» (aviso aparte); `setConnectTimeout`; comprobación de `http.begin`. **Compila** (ver `docs/HARDWARE.md` §6.3); sigue **sin probarse en placa**.
- `docs/HARDWARE.md`: tabla de pines con fuentes; pines libres marcados «no verificado» donde no hay evidencia.

## 0.4.0 — 2026-10-16
### Cambiado
- **Interfaz migrada de Streamlit a Flet 1.0.4** (12 páginas, identidad del prototipo y logo, componentes reutilizables, confirmaciones en acciones sensibles). Streamlit y sus dependencias se retiraron tras verificar la cobertura (el detalle del inventario se retiró en la limpieza posterior; ver `docs/DECISIONES.md` D-01).
- **Gemini como proveedor de IA** (`google-genai`): `AI_PROVIDER=auto|local|gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL`; fallback local declarado, corte por cuota, reintento prudente, redacción de teléfonos/correos, `scripts/verificar_gemini`. Se retiraron `INTERPRETE` y `LLM_*`.
- `config.py` lee `.env` (sin pisar el entorno). `app.py` arranca Flet (`--web` para navegador).
### Añadido
- Resumen explicable y borrador de comunicación asistidos por IA (validados; informativos).
- `AGENTS.md`, `CLAUDE.md`, 6 skills en `.claude/skills/`, y documentación: `IA_GEMINI`, `DECISIONES`, `DISENO_UI`, `API`, `BASE_DE_DATOS`, `SEGURIDAD`, `INSTALACION`, `PRUEBAS` (más `INVENTARIO_ARCHIVOS`, retirado en la limpieza de 0.6.2).
- `tests/test_flet.py` y `tests/test_gemini.py` (el total vigente lo da `pytest -q`).
### Retirado
- `PANTALLA_DE_MONITOREO/` (librerías idénticas por SHA-256 a las de Arduino; el sketch del LED se conservó en `firmware/pruebas/led_rgb/`).
### Conocido
- Gemini sin probar contra el servicio real; la app de escritorio descarga su cliente de Flet en la primera ejecución; `--web` necesita internet en el navegador; hardware sin probar.

## 0.3.0 — 2026-10-16
### Añadido
- Trazas JSONL con cadena de hashes (`services/tracing.py`) y página «Trazas».
- Ingesta con evidencia, tipo de incidencia, vulnerabilidades y servicio esencial; intérprete LLM opcional con esquema Pydantic estricto, revalidación en código, fallback a reglas y defensa contra prompt injection.
- Sector difuso/ambiguo con opciones para el operador; referencias coloquiales; duplicados con tiempo y tipo de incidencia.
- Máquina de estados en el backend, idempotencia de envío, transacciones SQLite (cambio + auditoría atómicos), API de escritura completa con tokens de operador y dispositivo.
- Planificador por flujo máximo lexicográfico con elegibilidad y capacidad.
- Visibilidad de sectores (sin reportes ≠ sin necesidad), Gini y experimento línea base vs HIDRO360 (datos sintéticos).
- Runner de 15 casos + 7 adicionales (`python -m evaluation.run_cases`).
- Interfaz rediseñada (12 secciones), firmware ESP32-S3 (sin compilar), scripts Raspberry Pi (sin probar), documentación.
### Cambiado
- Prioridad: vulnerabilidad elevada por servicio esencial/personas vulnerables confirmados; aviso de horas contradictorias. Reglas `reglas-2026.10.a`.
- Contrato de esquemas 1.1 (campos nuevos, retrocompatible por lectura).
### Conocido
- Responsable sin autenticación; HTTP sin cifrar; casos de evaluación redactados por el equipo (la guía no los enumera).

## 0.2.0
- MVP local: ingesta por reglas, duplicados sugeridos, prioridad, plan, aprobación, auditoría y API de lectura.
