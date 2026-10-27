# Planificador de cisternas

Implementación: `services/planner.py`. Alcance: asignar litros de cisternas a sectores. **No** modela rutas, distancias, ventanas horarias ni ETA, y no promete horas de llegada.

## Modelo
- Oferta de cada cisterna = `min(litros_disponibles, capacidad_litros)` si su estado es `disponible`, si no 0.
- Demanda de un sector = `personas_afectadas × 15 L` redondeada a 100 L (parámetro de demostración en `config.py`).
- Arco cisterna→sector solo si la cisterna es elegible (`sectores_elegibles`; `None` = todos).
- Un sector puede usar varias cisternas; una cisterna puede servir a varios sectores mientras no exceda su oferta.

## Objetivo y restricciones
- **Objetivo lexicográfico**: maximizar la cobertura del sector de mayor prioridad; luego la del siguiente, etc.
- **Restricciones**: litros por cisterna ≤ oferta; solo cisternas disponibles y elegibles; cobertura ≤ demanda.
- Se resuelve con **flujo máximo incremental** (Edmonds-Karp): se añaden los sectores por orden de prioridad y se aumenta el flujo; el flujo hacia sectores ya añadidos nunca disminuye. Es óptimo para ese objetivo, también con elegibilidad.
- **Desempate estable**: prioridad desc., nombre del sector; para elegir cisterna: sede en el mismo sector, más litros libres, id. Misma entrada y misma versión de reglas → mismo plan (probado).
- Razones de lo no asignado: sin cisternas disponibles, ninguna elegible, capacidad agotada por sectores de mayor prioridad, cobertura parcial, o falta el dato de personas.

## ¿OR-Tools?
No se usa. El problema es una asignación de transporte pequeña (decenas de nodos) y el algoritmo propio la resuelve de forma exacta para el objetivo declarado, sin una dependencia de decenas de MB en una Raspberry Pi. Se reconsideraría si se añaden rutas con ventanas de tiempo (VRP), donde sí aporta valor.
La prueba `test_flujo_supera_al_voraz_con_elegibilidad` muestra un caso donde el reparto voraz deja a un sector sin agua que el método de flujo sí cubre.

## Límites
Sin rutas/tiempos; la elegibilidad es una lista estática; los litros por persona son un supuesto; el plan es una propuesta que una persona aprueba, edita o rechaza.
