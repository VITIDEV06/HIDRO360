---
name: hidro360-backend-datos
description: Usar al tocar la API FastAPI, los servicios de negocio, el esquema Pydantic, SQLite, la auditoría o la seguridad de HIDRO360. Mantiene contratos, valida en el backend, preserva datos y exige transacciones y pruebas.
---

# Backend, base de datos y seguridad

Detalle: `docs/API.md`, `docs/BASE_DE_DATOS.md`, `docs/SEGURIDAD.md`. Esta skill es el procedimiento.

## Reglas
1. **Una sola puerta**: `services/workflow.py`. `api/main.py` y la UI solo llaman a sus funciones. No duplique reglas en endpoints.
2. **Mutaciones** = función decorada con `@operacion("nombre")` (transacción SQLite + traza). Dentro: validar → cambiar → `audit.registrar(responsable, acción, entidad, id, motivo, detalle)`. Si algo falla, todo se revierte (hay prueba: `test_auditoria_y_cambio_de_negocio_son_atomicos`).
3. **Estados**: toda transición pasa por `_mover_reporte/_mover_plan` (tablas `TRANSICIONES_*`). Añadir un estado = añadirlo a las tablas y a las pruebas de transiciones inválidas por API (409).
4. **Responsable obligatorio** (`_exigir_responsable`) y motivo en rechazos/ediciones. Es un nombre declarado, no autenticación: no lo presente como identidad verificada.
5. **Errores**: lance `ErrorDeNegocio` con mensaje comprensible; `api/main.py` lo mapea (404 «no existe», 409 «Transición…»/«ya está», 422 resto).
6. **Autorización en el backend** (`operador`, `dispositivo_u_operador`): sin tokens solo localhost; el token de dispositivo no lee reportes ni aprueba.
7. **Contratos** (`models/schemas.py`): campos nuevos con valor por defecto; cambio de contrato → subir `SCHEMA_VERSION`, actualizar `docs/API.md` y pruebas.
8. **SQLite**: tablas documento (`id` + JSON) en `data/database.py`; use `db.transaccion()`/`conexion()`; nada de SQL en servicios fuera de `database.py`. **Nunca** borre la base real; `cargar_datos_demo` es el único reinicio (la UI pide confirmación). Migración incompatible: copiar `agua_clara.db` antes, script reversible (carpeta `scripts/migraciones/`, a crear cuando haga falta) y prueba de lectura de datos antiguos.
9. **Datos sensibles**: no registrar secretos ni el texto completo del ciudadano en trazas; auditoría solo lo necesario.

## Pasos para un endpoint o regla nueva
1. Función en `workflow.py` con `@operacion` y validaciones. 2. Modelo de entrada en `api/main.py` (alias en inglés solo si la guía lo pide). 3. Prueba en `tests/test_backend.py` (camino feliz + transición/estado inválido + autorización). 4. Doc y changelog.

## Verificación
`pytest -q tests/test_backend.py tests/test_flujo.py` verde · `python -m evaluation.run_cases` sin FAIL · `audit.verificar_integridad()` verdadero tras las pruebas · ningún secreto en `git ls-files`.
