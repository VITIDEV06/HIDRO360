# Experimento de impacto — línea base vs. HIDRO360

Generado: 2026-10-17T07:17:13 · reglas `reglas-2026.10.a` · 200 escenarios por celda · reproducible: `python -m evaluation.run_impacto`

> **DATOS SINTÉTICOS.** Escenarios generados con semillas fijas y verdad de terreno inventada. Estos resultados describen el comportamiento de los algoritmos bajo los supuestos de abajo y **no son evidencia de impacto real sobre comunidades**.

## Diseño (fijado antes de ejecutar)
- Mismos escenarios, mismas cisternas, mismo planificador y misma estimación de demanda para ambos métodos; solo cambia el orden de prioridad (número de quejas vs. fórmula 40/30/30 sin contar quejas).
- Familias: `sesgo_visibilidad` (sectores de vulnerabilidad alta/crítica reportan menos: supuesto del equipo, **favorable** a HIDRO360) y `neutro` (propensión a reportar independiente de la vulnerabilidad).
- Oferta de agua = 30 %, 50 % u 80 % de la demanda real total.
- Posterior a la entrega (supuesto simplificador): horas residuales = horas reales × (1 − fracción de demanda cubierta).
- Gini: unidad = sector; variable = horas residuales; 0 h es dato real; ver `services/equidad.py`.
- Sectores sin reportes no reciben agua en ningún método (demanda desconocida); HIDRO360 los marca para verificación proactiva.

## Familia `sesgo_visibilidad` · oferta 30 % de la demanda

| Métrica | Línea base (quejas) | HIDRO360 |
|---|---|---|
| Demanda real satisfecha (↑ mejor) | 0.2888 | 0.2921 |
| Demanda no cubierta, litros (↓) | 27907.25 | 27778.1 |
| Sectores con necesidad que reciben agua (↑) | 3.145 | 3.26 |
| Sectores completamente cubiertos (↑) | 1.77 | 1.355 |
| Persona-horas sin agua residuales (↓) | 116695.2815 | 97018.3525 |
| Gini de horas residuales entre sectores (↓ = más equitativo) | 0.5361 | 0.5331 |
| Correlación orden vs necesidad real, Spearman (↑) | 0.3132 | 0.8573 |
| Demanda cubierta en sectores con ≤2 reportes (↑) | 0.0 | 0.1571 |

- Escenarios por persona-horas residuales: HIDRO360 mejor en **183**, igual en 5, peor en **12**.
- Gini de horas ANTES de entregar agua (igual para ambos): 0.4254.
- Sectores con necesidad real y sin ningún reporte: 356; marcados para verificación proactiva por HIDRO360: 345 (ningún método les asigna agua automáticamente).

## Familia `sesgo_visibilidad` · oferta 50 % de la demanda

| Métrica | Línea base (quejas) | HIDRO360 |
|---|---|---|
| Demanda real satisfecha (↑ mejor) | 0.4815 | 0.4853 |
| Demanda no cubierta, litros (↓) | 20349.5 | 20198 |
| Sectores con necesidad que reciben agua (↑) | 5.44 | 5.53 |
| Sectores completamente cubiertos (↑) | 3.24 | 2.69 |
| Persona-horas sin agua residuales (↓) | 85552.073 | 65275.5935 |
| Gini de horas residuales entre sectores (↓ = más equitativo) | 0.63 | 0.6215 |
| Correlación orden vs necesidad real, Spearman (↑) | 0.3132 | 0.8573 |
| Demanda cubierta en sectores con ≤2 reportes (↑) | 0.0221 | 0.2678 |

- Escenarios por persona-horas residuales: HIDRO360 mejor en **187**, igual en 1, peor en **12**.
- Gini de horas ANTES de entregar agua (igual para ambos): 0.4254.
- Sectores con necesidad real y sin ningún reporte: 356; marcados para verificación proactiva por HIDRO360: 345 (ningún método les asigna agua automáticamente).

## Familia `sesgo_visibilidad` · oferta 80 % de la demanda

| Métrica | Línea base (quejas) | HIDRO360 |
|---|---|---|
| Demanda real satisfecha (↑ mejor) | 0.7423 | 0.7434 |
| Demanda no cubierta, litros (↓) | 10025.2 | 9977.4 |
| Sectores con necesidad que reciben agua (↑) | 9.035 | 9.025 |
| Sectores completamente cubiertos (↑) | 4.88 | 4.675 |
| Persona-horas sin agua residuales (↓) | 41735.9515 | 37666.4805 |
| Gini de horas residuales entre sectores (↓ = más equitativo) | 0.7512 | 0.7423 |
| Correlación orden vs necesidad real, Spearman (↑) | 0.3132 | 0.8573 |
| Demanda cubierta en sectores con ≤2 reportes (↑) | 0.3519 | 0.4304 |

- Escenarios por persona-horas residuales: HIDRO360 mejor en **105**, igual en 78, peor en **17**.
- Gini de horas ANTES de entregar agua (igual para ambos): 0.4254.
- Sectores con necesidad real y sin ningún reporte: 356; marcados para verificación proactiva por HIDRO360: 345 (ningún método les asigna agua automáticamente).

## Familia `neutro` · oferta 30 % de la demanda

| Métrica | Línea base (quejas) | HIDRO360 |
|---|---|---|
| Demanda real satisfecha (↑ mejor) | 0.2876 | 0.2889 |
| Demanda no cubierta, litros (↓) | 27721.8 | 27667.6 |
| Sectores con necesidad que reciben agua (↑) | 2.86 | 3.14 |
| Sectores completamente cubiertos (↑) | 1.605 | 1.68 |
| Persona-horas sin agua residuales (↓) | 113336.979 | 91728.5405 |
| Gini de horas residuales entre sectores (↓ = más equitativo) | 0.5396 | 0.5492 |
| Correlación orden vs necesidad real, Spearman (↑) | 0.4723 | 0.8629 |
| Demanda cubierta en sectores con ≤2 reportes (↑) | 0.0 | 0.0408 |

- Escenarios por persona-horas residuales: HIDRO360 mejor en **183**, igual en 6, peor en **11**.
- Gini de horas ANTES de entregar agua (igual para ambos): 0.4402.
- Sectores con necesidad real y sin ningún reporte: 19; marcados para verificación proactiva por HIDRO360: 10 (ningún método les asigna agua automáticamente).

## Familia `neutro` · oferta 50 % de la demanda

| Métrica | Línea base (quejas) | HIDRO360 |
|---|---|---|
| Demanda real satisfecha (↑ mejor) | 0.4784 | 0.4811 |
| Demanda no cubierta, litros (↓) | 20305.75 | 20192.25 |
| Sectores con necesidad que reciben agua (↑) | 4.67 | 5.2 |
| Sectores completamente cubiertos (↑) | 3.02 | 3.065 |
| Persona-horas sin agua residuales (↓) | 83959.5125 | 56715.1655 |
| Gini de horas residuales entre sectores (↓ = más equitativo) | 0.6166 | 0.6327 |
| Correlación orden vs necesidad real, Spearman (↑) | 0.4723 | 0.8629 |
| Demanda cubierta en sectores con ≤2 reportes (↑) | 0.0 | 0.1065 |

- Escenarios por persona-horas residuales: HIDRO360 mejor en **196**, igual en 1, peor en **3**.
- Gini de horas ANTES de entregar agua (igual para ambos): 0.4402.
- Sectores con necesidad real y sin ningún reporte: 19; marcados para verificación proactiva por HIDRO360: 10 (ningún método les asigna agua automáticamente).

## Familia `neutro` · oferta 80 % de la demanda

| Métrica | Línea base (quejas) | HIDRO360 |
|---|---|---|
| Demanda real satisfecha (↑ mejor) | 0.7679 | 0.7701 |
| Demanda no cubierta, litros (↓) | 9041.7 | 8951.7 |
| Sectores con necesidad que reciben agua (↑) | 8.105 | 8.695 |
| Sectores completamente cubiertos (↑) | 5.2 | 5.12 |
| Persona-horas sin agua residuales (↓) | 35984.7895 | 21295.453 |
| Gini de horas residuales entre sectores (↓ = más equitativo) | 0.7536 | 0.74 |
| Correlación orden vs necesidad real, Spearman (↑) | 0.4723 | 0.8629 |
| Demanda cubierta en sectores con ≤2 reportes (↑) | 0.0307 | 0.3284 |

- Escenarios por persona-horas residuales: HIDRO360 mejor en **190**, igual en 3, peor en **7**.
- Gini de horas ANTES de entregar agua (igual para ambos): 0.4402.
- Sectores con necesidad real y sin ningún reporte: 19; marcados para verificación proactiva por HIDRO360: 10 (ningún método les asigna agua automáticamente).

## Limitaciones
- La verdad de terreno y la propensión a reportar son inventadas; otras distribuciones podrían cambiar el resultado.
- El modelo de horas residuales es una simplificación; no modela rutas, tiempos de viaje ni reabastecimiento.
- Un Gini menor no demuestra por sí solo una mejor asignación: se reporta junto con demanda no cubierta y persona-horas.
- Sectores sin reportes siguen invisibles para la asignación: la marca de verificación proactiva solo ayuda si alguien actúa sobre ella.
- Donde `peor` > 0 el método propuesto perdió en esos escenarios; se reporta sin ocultarlo.
