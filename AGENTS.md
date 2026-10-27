# AGENTS.md — HIDRO360 (TEAM VIFY AI)

> Nombre oficial: **HIDRO360** (antes «Agua Clara AI»). Las variables de entorno usan el prefijo `HIDRO360_`; los alias antiguos `AGUA_CLARA_*` siguen funcionando (obsoletos). Roles: portal público sin cuenta + administración con login; ver `docs/SEGURIDAD.md`.

Guía para asistentes de programación y personas. Describe la arquitectura **realmente implementada**; si el código y este archivo discrepan, gana el código: corrija este archivo en el mismo cambio.

## 1. Qué es
Sistema de coordinación de reportes de falta de agua en Panamá Oeste: recibe reportes, extrae datos (reglas o Gemini), sugiere sector y duplicados, prioriza con una fórmula transparente, propone asignaciones de cisternas respetando capacidad y exige **aprobación humana**. Todo queda auditado. **Datos de la demo: ficticios.** Funciona sin internet ni claves (modo local).

Principios que no se rompen: texto original inalterable · duplicados solo sugeridos · planes solo propuestos · comunicaciones solo borradores (nada se envía) · prioridad/plan/auditoría deterministas (la IA nunca decide) · supervisión humana en lo sensible · ninguna afirmación sin evidencia.

## 2. Arquitectura
```
interface/flet_app/  UI Flet (presentación)  ──┐
api/main.py          API FastAPI (dispositivos, integraciones) ──┤──►  services/workflow.py  (ÚNICA puerta de negocio)
                                              │            │
                                              │            ├─ services/ingest.py       interpretar texto (reglas | Gemini + fallback)
                                              │            ├─ services/workflow_ia.py + orquestador.py  flujo IA verificable → Recomendación → (aprobación humana) → Misión
                                              │            ├─ services/fotos.py        validación por firma y almacenamiento en MEDIA_DIR
                                              │            ├─ services/ia_gemini.py    proveedor Gemini (SDK google-genai, cuota, reintentos)
                                              │            ├─ services/ia_tareas.py    resumen/borrador asistidos (validados)
                                              │            ├─ services/geoloc.py       sector + duplicados
                                              │            ├─ services/prioritization.py  puntaje determinista
                                              │            ├─ services/planner.py      flujo máximo lexicográfico
                                              │            ├─ services/communication.py   borradores
                                              │            ├─ services/audit.py        auditoría con cadena de hashes
                                              │            ├─ services/tracing.py      trazas JSONL con cadena de hashes
                                              │            └─ services/equidad.py      Gini, Spearman
                                              └──►  data/database.py (SQLite stdlib, transacciones)
models/schemas.py   contratos Pydantic (SCHEMA_VERSION)        config.py   parámetros y variables de entorno
evaluation/         runner de casos, experimento de impacto     scripts/    utilidades (verificar_gemini)
firmware/           ESP32-S3 (compila; sin probar en placa)     deploy/raspberry/  servicios systemd (probado por el equipo)
assets/logo.jpeg    logo                                       proyecto_info/  documentos de origen del reto (PDF)
```
- **UI → `Ctx.ejecutar()` → `services.workflow`**. Las páginas (`interface/flet_app/portal/` y `interface/flet_app/admin/`, cada una con `build(ctx)`) no tocan SQLite ni calculan reglas.
- **La UI llama a `workflow` en el mismo proceso** (no por HTTP): la API es para dispositivos e integraciones; ambas comparten la misma lógica.
- Cada operación mutante usa `@operacion(...)` de `workflow.py`: una **transacción SQLite** que incluye cambio + auditoría + traza de ejecución.
- Máquinas de estados en el backend: `TRANSICIONES_REPORTE` y `TRANSICIONES_PLAN`.

## 3. Comandos (Windows, desde la raíz)
```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
python app.py                         # ventana de escritorio (offline tras la 1.ª ejecución, que descarga el cliente de Flet)
python app.py --web                   # navegador http://127.0.0.1:8501 (el navegador necesita internet: ver docs/DECISIONES.md D-04)
uvicorn api.main:app --port 8000      # API (/docs)
pytest -q                             # pruebas (el resultado vigente lo da este comando)
python -m evaluation.run_cases        # casos de la guía + adicionales → docs/RESULTADOS_EVALUACION.md
python -m evaluation.run_impacto      # experimento sintético → docs/EXPERIMENTO_IMPACTO.md
python -m scripts.verificar_gemini [--sin-llamada]
```

## 4. Variables de entorno (ver `.env.example`; `config.py` lee `.env` sin pisar el entorno)
`AI_PROVIDER` (auto|local|gemini), `GEMINI_API_KEY`, `GEMINI_MODEL`, `AI_TIMEOUT_SECONDS`, `AI_MAX_REINTENTOS`, `HIDRO360_DB`, `HIDRO360_TRACE`, `HIDRO360_MEDIA_DIR` (fotos), `HIDRO360_API_TOKEN`, `HIDRO360_DEVICE_TOKEN` (alias `AGUA_CLARA_*`). **Nunca** claves en código, tests, README, HTML ni trazas. `.env` está en `.gitignore`.

## 5. Reglas para modificar el código
1. Inspeccione antes de editar (`git status`, buscar referencias). No sobrescriba cambios sin confirmar del usuario.
2. La lógica de negocio va en `services/`; la UI y la API solo la llaman. No duplique reglas en componentes.
3. Todo cambio de reglas deterministas (pesos, topes, fórmula) sube `VERSION_REGLAS` en `config.py` y actualiza docs/pruebas.
4. Todo cambio de contrato (`models/schemas.py`) sube `SCHEMA_VERSION` y se documenta en `docs/API.md`.
5. Toda función que cambia datos: validar en el backend, registrar auditoría con responsable y motivo, dentro de `@operacion`.
6. Añada pruebas con el cambio; ejecute `pytest -q` antes de dar algo por terminado. No diga «pasa» si no se ejecutó.
7. Mensajes de usuario en español claro; sin promesas de hora de llegada.

## 6. Gemini / IA (resumen; detalle en `docs/IA_GEMINI.md`)
Proveedor único: `services/ia_gemini.py` (SDK oficial `google-genai`). `AI_PROVIDER=auto` usa Gemini solo con `GEMINI_API_KEY`. Si falla (sin red, 429, 401/403, JSON inválido, timeout) → **modo local**, declarado como `reglas(fallback)` y con traza `fallback`; nunca se presenta como Gemini. Texto del ciudadano = dato no confiable: frases tipo instrucción se excluyen, teléfonos/correos se redactan antes de enviar, la salida se valida con `SalidaIA` (extra=forbid) y se revalida con código (sector del catálogo, evidencia literal). Gemini **no** aprueba, cierra, asigna ni calcula prioridad. La confianza del modelo es subjetiva, no calibrada.

## 7. API (resumen; detalle en `docs/API.md`)
`/api/v1/...` Público (sin credenciales): `POST /publico/reportes`, `GET /publico/reportes/{codigo}`, `GET /publico/avisos`, `POST /auth/login`, `GET /salud`. Administrador (sesión `Bearer` o token de servicio `HIDRO360_API_TOKEN`): todo lo demás (reportes, fotos, mapa, confirmar, rechazar, duplicados, planes, misiones, flota, recomendaciones IA, auditoría, trazas). Terminal de cisterna (`X-Device-Token`, un token por vehículo, identidad derivada del token): solo `GET /dispositivo/mision`, `POST /dispositivo/eventos` (idempotente por `uid`) y `POST /dispositivo/latido`. El token compartido antiguo solo sirve para `GET /estado` y `POST /dispositivos/confirmaciones`. El responsable de la auditoría es el usuario autenticado.

## 8. Base de datos (detalle en `docs/BASE_DE_DATOS.md`)
SQLite, tablas documento (`id` + JSON) creadas con `CREATE TABLE IF NOT EXISTS`. **Migraciones:** como el contenido es JSON validado por Pydantic, los campos nuevos deben tener valor por defecto (lectura retrocompatible); un cambio incompatible exige un script de migración reversible (carpeta `scripts/migraciones/`, a crear cuando haga falta) y copia previa de `agua_clara.db`. Nunca borre la base real; `cargar_datos_demo` es la única vía de reinicio y la UI pide confirmación.

## 9. Auditoría y trazas
Auditoría (`services/audit.py`): quién, cuándo, qué, motivo, antes/después; cadena de hashes (`verificar_integridad()`), en la misma transacción que el cambio. Trazas (`services/tracing.py`): JSONL con cadena de hashes, sin texto completo del ciudadano ni secretos; explican la ejecución (proveedor, duración, estado). Detectan alteración intermedia; **no** impiden editar el archivo ni detectan truncar el final.

## 10. Seguridad
Roles: **portal público sin cuenta** (reportar/consultar con código de seguimiento) y **administración con login** (`services/auth.py`: scrypt, sesiones con hash, bloqueo por intentos). Sin cuentas por defecto: `python -m scripts.crear_admin`. La autorización se comprueba en el backend (`@requiere_admin` en `workflow`, dependencias de la API), no solo en la interfaz; **toda función administrativa nueva debe llevar `@requiere_admin` y una prueba en `tests/test_auth.py`**. Las sesiones, bloqueos y límites de tasa se guardan en la base (`sesiones`, `limites`). Límites: sin HTTPS, MFA ni recuperación de contraseña. Dispositivos no aprueban ni leen reportes. Detalle en `docs/SEGURIDAD.md`.

## 11. Skills (`.claude/skills/`, formato de Claude Code)
`hidro360-arquitectura`, `hidro360-flet-ui`, `hidro360-backend-datos`, `hidro360-ia-gemini`, `hidro360-pruebas-evaluacion`, `hidro360-hardware`. Claude Code las descubre al abrir el proyecto en una sesión nueva y las invoca por descripción o con `/nombre`. Otras herramientas pueden leerlas como documentación; no son automáticas fuera de Claude Code.

## 12. Retirar código antiguo
Solo si: (1) `git grep --untracked` sin referencias ni imports (también indirectos), (2) su responsabilidad está cubierta y probada en otro sitio, (3) no es parte de una integración futura documentada, (4) los recursos importantes están preservados (copia verificada por SHA-256 si es un duplicado), (5) docs, dependencias y pruebas actualizadas. Ante la duda, conserve y documente en `docs/DECISIONES.md`.

## 13. Estilo y documentación
Python 3.10+; tipado con Pydantic; nombres de dominio en español; docstrings que explican el *porqué*; sin comentarios obvios. Docs en `docs/` (español). Actualice `CHANGELOG.md` y la doc afectada con cada cambio relevante.

## 14. Criterios de aceptación
`pytest -q` verde · `python -m evaluation.run_cases` sin FAIL · demo reproducible desde base vacía (`python app.py` → «Cargar datos de ejemplo») · modo local funciona sin Gemini · ningún secreto en Git (`git ls-files | findstr .env`) · docs coinciden con el comportamiento · lo no probado (hardware, Gemini real, escritorio en otros SO) está marcado como pendiente.

## 15. Limitaciones conocidas
Sin HTTPS, MFA ni recuperación de contraseña (las cuentas se crean desde el panel «Usuarios» o con `scripts/demo_local.ps1 -CrearAdmin`) · Gemini verificado contra el servicio real el 2026-10-17 (ver `docs/IA_GEMINI.md`); las pruebas automáticas usan cliente simulado · la app de escritorio descarga su cliente una vez (necesita internet la 1.ª vez) · modo `--web` necesita internet en el navegador (CanvasKit/fuentes) · firmware ESP32-S3 compilado pero sin probar en placa · Raspberry: el equipo la instaló y la API y el panel respondieron (ver `docs/INSTALACION.md`) · producción (Vercel+Supabase) no operativa: la conexión con la base falla · Telegram no probado con el servicio real · extracción por regex limitada · casos de evaluación redactados por el equipo (la guía no los enumera) · el modelo `gemini-3.5-flash-lite` por defecto se tomó de la documentación consultada el 2026-10-16: verifíquelo con `scripts.verificar_gemini`.
