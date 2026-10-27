# Pruebas y evaluación

## Qué se ejecuta

| Comando | Qué cubre | Tipo |
|---|---|---|
| `pytest -q` | Todas las pruebas automatizadas (sin red, bases temporales) | Automatizado |
| `python -m evaluation.run_cases` | Casos por grupo de la guía + adicionales (ver el informe generado) | Automatizado, base temporal, modo local |
| `python -m evaluation.run_impacto` | Experimento sintético línea base vs HIDRO360 | Simulación con semillas fijas |
| `python -m scripts.verificar_gemini` | Conexión **real** con Gemini | **Manual**, requiere clave |
| `python -m scripts.revisar_secretos` | Que no haya claves versionadas en Git | Automatizado |

## Última ejecución (entrega HIDRA — Reto 01, 2026-10-17)

| Comando | Resultado |
|---|---|
| `pytest -q` | **638 passed, 46 skipped, 0 failed** (≈ 9 min) |
| `python -m evaluation.run_cases` | **PASS 22 · REVIEW 0 · FAIL 0** |
| `python -m scripts.revisar_secretos` | Sin secretos detectados |

Los 46 `skipped` son pruebas que requieren un servicio externo (Gemini real sin clave en el entorno, Supabase/Postgres real, etc.) y se **omiten** cuando no está disponible; se marcan como `skip`, **no** como aprobadas.

## Archivos de prueba (19)

| Archivo | Cubre |
|---|---|
| `tests/test_flujo.py` | Ingesta, duplicados, prioridad, capacidad, borradores, edición/aprobación/rechazo, API |
| `tests/test_backend.py` | Máquina de estados, responsable, idempotencia, atomicidad con auditoría, trazas, planificador (flujo vs voraz), visibilidad, autenticación de la API |
| `tests/test_ingesta.py` | Ejemplos del enunciado, sector difuso/ambiguo, inyecciones, Gemini simulado y fallbacks |
| `tests/test_gemini.py` | Proveedor: selección de proveedor, reintento 503, 429/403 con corte, respuesta vacía, clave no filtrada, redacción de PII, tareas de IA, compatibilidad con el SDK real |
| `tests/test_orquestador.py` | 6 etapas, procedencia por etapa (`gemini`/`reglas`/`reglas(fallback)`), aprobación humana de la recomendación; `-k gemini_real` se omite sin clave |
| `tests/test_ia_robustez.py` | La IA no importa ni llama funciones de escritura; 7 fallos de Gemini terminan en fallback sin cambiar reportes/planes/misiones/usuarios/flota |
| `tests/test_equidad.py` | Gini (valores conocidos, ceros vs faltantes), Spearman, reproducibilidad del experimento |
| `tests/test_flet.py` | Las páginas construyen; acciones reales (recibir, doble clic, confirmar, duplicados, plan, edición, aprobar/rechazar, borrador) |
| `tests/test_flet_roles.py` | Portal público vs administración; visibilidad por rol |
| `tests/test_auth.py` | Login, sesiones, bloqueo por intentos, `@requiere_admin` |
| `tests/test_auth_supabase.py` | Verificación de JWT de Supabase (`HIDRO360_AUTH=supabase`) |
| `tests/test_database_contrato.py` | Contrato de la base, tablas documento, migración retrocompatible |
| `tests/test_datos_postgres.py` | Fachada PostgreSQL (roles imitados sobre PostgreSQL local) |
| `tests/test_storage_supabase.py` | Almacenamiento de fotos en Supabase Storage (cliente simulado) |
| `tests/test_serverless.py` | Modo Vercel: sin disco de escritura, trazas a `stdout` |
| `tests/test_despliegue.py` | Coherencia `vercel.json`/`pyproject.toml`/`uv.lock`/`requirements-servidor.txt` y ausencia de secretos |
| `tests/test_flota.py` | Flota, misiones, eventos idempotentes por `uid`, tokens por vehículo |
| `tests/test_fotos.py` | Validación por firma binaria y almacenamiento en `HIDRO360_MEDIA_DIR` |
| `tests/test_e2e.py` | Extremo a extremo con servidor uvicorn real y reinicio |

## Qué NO está probado (no lo afirme)

- **Hardware**: el firmware ESP32-S3 **compila** (ver `docs/HARDWARE.md` §6.3) pero **no se probó en una placa** (WiFi, táctil, pantalla, NVS, TLS). La **Raspberry Pi** y sus servicios systemd tampoco.
- **Supabase y Vercel reales**: no desplegados; lo probado es PostgreSQL 18 local (roles imitados), `vercel build` local y clientes simulados.
- **Interfaz de escritorio en otros SO**: las pruebas de Flet no abren ventana; la revisión visual se hizo en navegador (`--web`) y a mano.
- **Casos de evaluación**: los **15 de la guía no existen como lista**; los casos del runner los redactó el equipo y no son ciegos.
- **Impacto**: el experimento es **sintético**; no demuestra impacto real.
- **Gemini**: verificado contra el servicio real (2026-10-17, ver `docs/IA_GEMINI.md`), pero con **volumen bajo** (decenas de llamadas) y **sin** medir calidad semántica frente a un conjunto etiquetado; 5xx/timeouts reales solo simulados.

## Entorno limpio

Para comprobar `requirements.txt` desde cero:

```bash
python -m venv .venv2
.venv2\Scripts\activate
pip install -r requirements.txt
pytest -q
```

## Regla

**Si no se ejecutó, no se afirma.** Un caso falla si no se cumple el criterio, aunque no haya excepción.
