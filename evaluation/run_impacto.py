"""
    python -m evaluation.run_impacto [--n 200]

Ejecuta el experimento sintético (línea base vs. HIDRO360) y escribe
evaluation/resultados/impacto.json y docs/EXPERIMENTO_IMPACTO.md. Datos SINTÉTICOS.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import impacto  # noqa: E402

ETIQ = {
    "proporcion_satisfecha": "Demanda real satisfecha (↑ mejor)",
    "demanda_no_cubierta": "Demanda no cubierta, litros (↓)",
    "sectores_con_algo_de_agua": "Sectores con necesidad que reciben agua (↑)",
    "sectores_completamente_cubiertos": "Sectores completamente cubiertos (↑)",
    "persona_horas_residuales": "Persona-horas sin agua residuales (↓)",
    "gini_horas_residuales": "Gini de horas residuales entre sectores (↓ = más equitativo)",
    "spearman_orden_vs_necesidad": "Correlación orden vs necesidad real, Spearman (↑)",
    "proporcion_cubierta_sectores_pocos_reportes": "Demanda cubierta en sectores con ≤2 reportes (↑)",
}


def md(exp):
    L = [f"# Experimento de impacto — línea base vs. HIDRO360",
         "",
         f"Generado: {datetime.now().isoformat(timespec='seconds')} · reglas `{exp['version_reglas']}` · "
         f"{exp['n_escenarios']} escenarios por celda · reproducible: `python -m evaluation.run_impacto`",
         "",
         "> **DATOS SINTÉTICOS.** Escenarios generados con semillas fijas y verdad de terreno inventada. "
         "Estos resultados describen el comportamiento de los algoritmos bajo los supuestos de abajo y "
         "**no son evidencia de impacto real sobre comunidades**.",
         "",
         "## Diseño (fijado antes de ejecutar)",
         "- Mismos escenarios, mismas cisternas, mismo planificador y misma estimación de demanda para ambos métodos; "
         "solo cambia el orden de prioridad (número de quejas vs. fórmula 40/30/30 sin contar quejas).",
         "- Familias: `sesgo_visibilidad` (sectores de vulnerabilidad alta/crítica reportan menos: supuesto del equipo, "
         "**favorable** a HIDRO360) y `neutro` (propensión a reportar independiente de la vulnerabilidad).",
         "- Oferta de agua = 30 %, 50 % u 80 % de la demanda real total.",
         "- Posterior a la entrega (supuesto simplificador): horas residuales = horas reales × (1 − fracción de demanda cubierta).",
         "- Gini: unidad = sector; variable = horas residuales; 0 h es dato real; ver `services/equidad.py`.",
         "- Sectores sin reportes no reciben agua en ningún método (demanda desconocida); HIDRO360 los marca para verificación proactiva.",
         ""]
    for r in exp["resumenes"]:
        L += [f"## Familia `{r['familia']}` · oferta {int(r['ratio_oferta'] * 100)} % de la demanda", "",
              "| Métrica | Línea base (quejas) | HIDRO360 |", "|---|---|---|"]
        for k, v in r["metricas"].items():
            L.append(f"| {ETIQ.get(k, k)} | {v['linea_base']} | {v['agua_clara']} |")
        e = r["escenarios_agua_clara_mejor_en_persona_horas"]
        L += ["",
              f"- Escenarios por persona-horas residuales: HIDRO360 mejor en **{e['mejor']}**, igual en {e['igual']}, peor en **{e['peor']}**.",
              f"- Gini de horas ANTES de entregar agua (igual para ambos): {r['gini_horas_antes_media']}.",
              f"- Sectores con necesidad real y sin ningún reporte: {r['sectores_invisibles_con_necesidad_total']}; "
              f"marcados para verificación proactiva por HIDRO360: {r['marcados_para_verificacion_proactiva']} "
              "(ningún método les asigna agua automáticamente).", ""]
    L += ["## Limitaciones",
          "- La verdad de terreno y la propensión a reportar son inventadas; otras distribuciones podrían cambiar el resultado.",
          "- El modelo de horas residuales es una simplificación; no modela rutas, tiempos de viaje ni reabastecimiento.",
          "- Un Gini menor no demuestra por sí solo una mejor asignación: se reporta junto con demanda no cubierta y persona-horas.",
          "- Sectores sin reportes siguen invisibles para la asignación: la marca de verificación proactiva solo "
          "ayuda si alguien actúa sobre ella.",
          "- Donde `peor` > 0 el método propuesto perdió en esos escenarios; se reporta sin ocultarlo.", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=impacto.N_SEMILLAS)
    args = ap.parse_args()
    exp = impacto.experimento_completo(args.n)
    (ROOT / "evaluation" / "resultados").mkdir(exist_ok=True)
    (ROOT / "evaluation" / "resultados" / "impacto.json").write_text(
        json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "EXPERIMENTO_IMPACTO.md").write_text(md(exp), encoding="utf-8")
    print(md(exp))


if __name__ == "__main__":
    main()
