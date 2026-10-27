import math

import pytest

from evaluation import impacto
from services import equidad


def test_gini_valores_conocidos():
    assert equidad.gini([5, 5, 5, 5]) == 0.0
    assert equidad.gini([0, 0, 0, 10]) == pytest.approx(0.75)      # máximo para n=4: (n-1)/n
    assert equidad.gini([1, 2, 3, 4]) == pytest.approx(0.25)       # valor clásico
    assert equidad.gini([0, 0, 0, 0]) == 0.0                        # sin privación en ningún sector
    assert equidad.gini([3, 1, 2]) == equidad.gini([1, 2, 3])        # independiente del orden
    assert equidad.gini([2, 4, 6]) == pytest.approx(equidad.gini([20, 40, 60]))  # invariante a la escala


def test_gini_cero_es_dato_y_faltante_no_es_cero():
    assert equidad.gini([0, 0, 10]) > 0
    with pytest.raises(ValueError):
        equidad.gini([0, None, 10])
    with pytest.raises(ValueError):
        equidad.gini([1, float("nan")])
    with pytest.raises(ValueError):
        equidad.gini([1, -2])
    assert equidad.gini([7]) is None and equidad.gini([]) is None


def test_gini_acotado():
    g = equidad.gini([0, 1, 5, 20, 100, 3])
    assert 0 <= g <= (6 - 1) / 6


def test_spearman():
    assert equidad.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert equidad.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert equidad.spearman([1, 1, 1], [1, 2, 3]) is None
    assert equidad.spearman([1, 2], [1, 2]) is None


def test_experimento_reproducible_y_mismas_condiciones():
    a = impacto.evaluar_escenario(impacto.generar_escenario(7, "sesgo_visibilidad", 0.5))
    b = impacto.evaluar_escenario(impacto.generar_escenario(7, "sesgo_visibilidad", 0.5))
    assert a == b
    # ambos métodos usan la misma oferta y la misma demanda real; ninguno excede la oferta
    assert a["linea_base"]["oferta_total"] == a["agua_clara"]["oferta_total"]
    assert a["linea_base"]["demanda_real_total"] == a["agua_clara"]["demanda_real_total"]
    for m in ("linea_base", "agua_clara"):
        assert sum(a[m]["litros_por_sector"].values()) <= a[m]["oferta_total"]
        assert a[m]["demanda_cubierta"] <= a[m]["demanda_real_total"]
    assert a["gini_horas_antes"] is not None


def test_resumen_pequeno():
    r = impacto.resumen("neutro", 0.5, n=10)
    assert r["n_escenarios"] == 10 and "proporcion_satisfecha" in r["metricas"]
    total = sum(r["escenarios_agua_clara_mejor_en_persona_horas"].values())
    assert total == 10
