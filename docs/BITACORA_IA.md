# Bitácora de uso de IA — HIDRA — Reto 01 (HIDRO360)

> **Nota de documentación (17-oct-2026):** este registro es histórico. Algunos archivos que cita (`docs/AUDITORIA_TECNICA.md`, `docs/INVENTARIO_ARCHIVOS.md`, `docs/INVENTARIO_TECNICO.md`, `docs/GUION_VIDEO.md`, `docs/IMPLEMENTACIONES_EJEMPLO.md`) se retiraron en una limpieza posterior; su contenido sigue en el historial de Git.

> **Qué es este documento.** Registra **cómo se usó IA** para construir y revisar HIDRO360, qué produjo, quién lo revisó y con qué prueba se aceptó. No es un registro de conversaciones ni una lista de «cosas que la IA hizo sola»: la IA propone, una persona revisa y aprueba.
>
> **Procedencia de cada entrada.** Las entradas anteriores al **2026-10-17 (revisión de entrega)** están **reconstruidas a partir de evidencia del propio repositorio** (mensajes de commit, `CHANGELOG.md`, `docs/DECISIONES.md` y los archivos afectados). La herramienta de IA exacta de esas entradas tempranas **no quedó registrada** y se marca como *no registrada*. Las entradas de la revisión de entrega corresponden a trabajo observado en la sesión de consolidación. **No se inventan conversaciones ni resultados de prueba.**

## Convenciones

- **Resultado**: qué se obtuvo (artefacto verificable), no la narración.
- **Revisión manual**: `sí` = una persona leyó/validó el resultado · `parcial` = se revisó el efecto en la demo/pruebas, no línea por línea · `pendiente`.
- **Pruebas**: comando o evidencia concreta. `—` = no aplica.
- **Decisión**: `aceptado` · `ajustado` (se cambió algo tras la revisión) · `descartado`.

## Herramientas de IA presentes en el repositorio

| Evidencia | Qué implica |
|---|---|
| `AGENTS.md` | Guía para asistentes de programación (describe la arquitectura real). |
| `CLAUDE.md` | Configuración para un asistente concreto. |
| `.claude/skills/hidro360-*` (6 skills) | Instrucciones especializadas por dominio (arquitectura, UI Flet, backend/datos, IA Gemini, pruebas/evaluación, hardware). |
| `docs/IA_GEMINI.md` | La IA **del producto** (Gemini) es distinta de la IA **asistente de desarrollo**. No confundir. |

## Bitácora

| Fecha | Objetivo / pregunta | Herramienta IA | Resultado | Archivos afectados | Revisión manual | Pruebas | Decisión |
|---|---|---|---|---|---|---|---|
| 2026-10-16 | Arrancar la idea: interpretar mensajes de falta de agua y priorizar | no registrada | Primer MVP local (ingesta por reglas, duplicados, prioridad, plan, aprobación, auditoría, API de lectura) | `services/`, `api/main.py`, `data/`, `config.py`, docs iniciales | sí | `pytest -q` (conjunto inicial) | aceptado |
| 2026-10-16 | Extraer datos con un modelo de lenguaje, no solo regex | no registrada | `services/ia_gemini.py`: proveedor Gemini con fallback local declarado, validación Pydantic estricta y defensa contra inyección | `services/ia_gemini.py`, `services/ingest.py`, `config.py`, `docs/IA_GEMINI.md` | sí | `pytest tests/test_gemini.py`, `pytest tests/test_ingesta.py` | ajustado (se añadió revalidación en código y redacción de PII) |
| 2026-10-16 | Migrar la interfaz de Streamlit a Flet 1.0.4 | no registrada | 12 páginas Flet, componentes reutilizables, confirmaciones en acciones sensibles; Streamlit retirado | `interface/flet_app/`, `app.py`, `requirements*.txt` | sí | `pytest tests/test_flet.py` | aceptado |
| 2026-10-17 | Separar portal público (sin cuenta) y administración (con login) | no registrada | `interface/flet_app/{portal,admin}/`; autenticación con scrypt, sesiones con hash, bloqueo por intentos | `interface/flet_app/`, `services/auth.py`, `scripts/crear_admin.py`, `tests/test_auth.py` | sí | `pytest tests/test_auth.py` | aceptado |
| 2026-10-17 | Unificar la identidad a HIDRO360 y conservar compatibilidad | no registrada | Renombrado en interfaz, config, docs, deploy y firmware; aliases `AGUA_CLARA_*` obsoletos | `config.py`, `deploy/`, `firmware/`, docs | sí | `pytest -q` | ajustado (se conservaron los alias por compatibilidad) |
| 2026-10-17 | Coordinar las etapas de IA de forma verificable y con aprobación humana | no registrada | `services/orquestador.py` + `workflow_ia.py`: validación → duplicados → clasificación → prioridad → recomendación → asignación, con procedencia por etapa (`gemini`/`reglas`/`reglas(fallback)`) | `services/orquestador.py`, `services/workflow_ia.py`, `models/schemas.py` | sí | `pytest tests/test_orquestador.py` | aceptado |
| 2026-10-17 | Planificar cisternas por capacidad con criterio explicable | no registrada | Flujo máximo lexicográfico (Edmonds-Karp) con desempate estable y alternativa voraz | `services/planner.py`, `tests/test_backend.py`, `docs/PLANIFICADOR.md` | sí | `pytest tests/test_backend.py` | aceptado |
| 2026-10-17 | Preparar el despliegue Supabase + Vercel sin inventar estado | no registrada | Contrato de configuración y dependencias; `vercel build` local; **nada desplegado** | `vercel.json`, `pyproject.toml`, `uv.lock`, `supabase/`, `docs/DESPLIEGUE.md` | sí | `pytest tests/test_despliegue.py`; `vercel build` (local) | ajustado (la UI Flet **no** va en Vercel; se documentó por qué) |
| 2026-10-17 | Revisar el laboratorio `IMPLEMENTACIONES_EJEMPLO` y decidir qué hacer con él | asistente de código (esta sesión) | Informe de comparación por módulo; decisión inicial **documentar, no integrar**; la carpeta se **eliminó físicamente** en la limpieza posterior (no era producto; ver `CHANGELOG.md`) | `docs/BITACORA_IA.md` | sí | revisión cruzada con `services/` y `tests/` | aceptado |
| 2026-10-17 | Revisión de entrega: ordenar el repositorio y documentar el estado real | asistente de código (esta sesión) | README con secciones de entrega, arquitectura de 8 roles, guía de demo y esta bitácora | `README.md`, `docs/ARQUITECTURA.md`, `docs/GUIA_DEMO_EN_VIVO.md`, `docs/BITACORA_IA.md` | sí | `pytest -q`, `python -m evaluation.run_cases`, `python -m scripts.revisar_secretos` (resultados en el informe final) | pendiente de cierre |
| 2026-10-17 | Limpieza física del repositorio: retirar código no producto, artefactos y docs históricas | asistente de código (esta sesión) | `IMPLEMENTACIONES_EJEMPLO/` (laboratorio IA), `docs/IMPLEMENTACIONES_EJEMPLO.md`, `docs/AUDITORIA_TECNICA.md`, `docs/INVENTARIO_ARCHIVOS.md`, `docs/INVENTARIO_TECNICO.md`, `docs/GUION_VIDEO.md`, `data/ENTREGA1.ok`, `evaluation/resultados/*.json` y `supabase/.temp/cli-latest` **borrados del disco y de Git**; referencias rotas corregidas | `CHANGELOG.md`, `docs/DECISIONES.md`, `docs/DISENO_UI.md`, `docs/BITACORA_IA.md`, `.gitignore` | sí | `pytest -q`, `python -m evaluation.run_cases` (re-ejecutados tras la limpieza) | aceptado |

## Reglas de la bitácora

1. **Una entrada por objetivo**, no por prompt. Si no hay artefacto verificable, no es una entrada.
2. **La IA nunca decide.** Prioridad, plan y auditoría son deterministas; la IA solo propone y el humano aprueba.
3. **Nada sensible.** No se registran claves, `.env`, URLs privadas ni texto íntegro de ciudadanos.
4. **Si no se ejecutó, no se afirma.** Cada fila cita su prueba real o queda como `pendiente`.
5. **Las entradas tempranas son reconstruidas.** Se basan en el historial de Git y el `CHANGELOG`; la herramienta de IA de esas fechas no se registró y así se declara.
