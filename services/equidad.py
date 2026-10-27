"""
Indicadores de equidad. Funciones puras, deterministas y probadas (tests/test_equidad.py).

COEFICIENTE DE GINI DE HORAS SIN AGUA
  Unidad de análisis: sector. Variable: horas sin suministro de cada sector en el período evaluado
  (p. ej. al momento de la evaluación). 0 = todos los sectores sufren lo mismo; valores altos = la
  privación se concentra en pocos sectores. Máximo teórico con n sectores: (n-1)/n.
  Fórmula (valores ordenados ascendentemente x_1<=...<=x_n, i=1..n):
      G = 2 * sum(i * x_i) / (n * sum(x)) - (n + 1) / n
  Tratamiento de datos:
    - 0 horas es un valor REAL (sector sin interrupción) y entra al cálculo.
    - Dato faltante (None/NaN) NO es 0: la función lanza ValueError; quien llama decide excluir
      el sector del análisis y debe decirlo explícitamente.
    - Si todas las horas son 0 no hay desigualdad que medir: se devuelve 0.0.
    - Con menos de 2 sectores no está definido: devuelve None.
  Interpretación: un Gini menor sugiere un reparto más equitativo, pero NO prueba por sí solo que la
  asignación sea mejor: hay que mirar también la gravedad de la necesidad, la demanda no cubierta y
  las restricciones operativas.
"""

import math
from typing import List, Optional, Sequence


def gini(valores: Sequence[float]) -> Optional[float]:
    vals: List[float] = []
    for v in valores:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            raise ValueError("Dato faltante: un valor desconocido no es 0 horas; exclúyalo explícitamente.")
        if v < 0:
            raise ValueError("Las horas sin agua no pueden ser negativas.")
        vals.append(float(v))
    n = len(vals)
    if n < 2:
        return None
    total = sum(vals)
    if total == 0:
        return 0.0
    vals.sort()
    acumulado = sum((i + 1) * x for i, x in enumerate(vals))
    return round(2 * acumulado / (n * total) - (n + 1) / n, 6)


def _rangos(xs: Sequence[float]) -> List[float]:
    orden = sorted(range(len(xs)), key=lambda i: xs[i])
    rangos = [0.0] * len(xs)
    i = 0
    while i < len(orden):
        j = i
        while j + 1 < len(orden) and xs[orden[j + 1]] == xs[orden[i]]:
            j += 1
        for k in range(i, j + 1):
            rangos[orden[k]] = (i + j) / 2 + 1  # rango medio para empates
        i = j + 1
    return rangos


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Correlación de rangos de Spearman (con rangos medios en empates). None si no está definida."""
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _rangos(x), _rangos(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return round(num / den, 6) if den else None
