---
name: hidro360-pruebas-evaluacion
description: Usar al escribir o ejecutar pruebas, el runner de casos de evaluación o el experimento de impacto de HIDRO360. Garantiza resultados reproducibles y honestos, distinguiendo pruebas simuladas de pruebas reales.
---

# Pruebas y evaluación

Detalle: `docs/PRUEBAS.md`.

## Comandos
```bash
pytest -q                          # todo (cifra y duración vigentes: ejecútelo). Archivos: test_flujo, test_backend, test_ingesta, test_gemini, test_equidad, test_flet
python -m evaluation.run_cases     # 15 casos + 7 adicionales; base y trazas TEMPORALES; escribe docs/RESULTADOS_EVALUACION.md
python -m evaluation.run_impacto   # experimento sintético; escribe docs/EXPERIMENTO_IMPACTO.md
```

## Reglas
1. **No invente resultados.** Un caso nunca pasa «porque no hubo excepción»: compare esperado vs obtenido (`evaluar(...)` en `evaluation/casos.py`). Si no ejecutó algo, no diga que pasó; documente el bloqueo y cómo verificarlo.
2. **Aislamiento**: `tests/conftest.py` fuerza BD y trazas temporales, `AI_PROVIDER=local` y sin clave. Ninguna prueba toca `agua_clara.db` ni la red.
3. **Simulado ≠ real**: Gemini se prueba con `ClienteFalso` (el SDK real solo se valida por construcción de objetos); la UI con manejadores invocados sin ventana; el hardware no se prueba por software. Escríbalo así en informes.
4. **Los 15 casos**: la guía solo define grupos y esperados; las entradas son del equipo. Mantenga esa advertencia en todo informe. Si ajusta un umbral mirando un caso, dígalo (riesgo de sobreajuste).
5. **Experimento de impacto**: datos SINTÉTICOS, diseño fijado antes de ejecutar, mismas condiciones para ambos métodos, reporte también donde pierde el método propuesto. No lo presente como impacto real. El Gini se interpreta junto con demanda no cubierta.
6. **Regresiones**: ante un fallo, corrija la causa; no relaje la aserción. Si la aserción estaba mal, explique por qué.

## Pasos para una funcionalidad nueva
Prueba unitaria del servicio → prueba de API/estado inválido si aplica → prueba de página Flet si hay UI → caso del runner si es regla de negocio → ejecutar todo → actualizar `docs/PRUEBAS.md`.

## Verificación
`pytest -q` sin fallos · `run_cases` sin FAIL (los NEEDS_REVIEW se explican) · informes regenerados con fecha · entorno limpio: instalar `requirements.txt` en un venv nuevo y repetir.
