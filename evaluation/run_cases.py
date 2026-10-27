"""
Runner reproducible de los casos de evaluación.

    python -m evaluation.run_cases              # tabla en consola + JSON + informe Markdown
    python -m evaluation.run_cases --solo C05   # un caso
    python -m evaluation.run_cases --sin-informe

Usa una base y trazas TEMPORALES (no toca agua_clara.db). Termina con código 1 si hay algún FAIL.
Salidas: evaluation/resultados/ultimo.json y docs/RESULTADOS_EVALUACION.md.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from evaluation.casos import CASOS, Resultado  # noqa: E402


def ejecutar(solo=None):
    resultados = []
    for caso in CASOS:
        if solo and caso.id not in solo:
            continue
        t0 = time.perf_counter()
        try:
            r = caso.fn(caso)
        except Exception as e:  # un error del programa NO es un PASS
            r = Resultado(id=caso.id, grupo=caso.grupo, descripcion=caso.descripcion, criterio=caso.criterio,
                          estado="FAIL", explicacion=f"Excepción: {type(e).__name__}: {e}")
        r.metricas["segundos"] = round(time.perf_counter() - t0, 2)
        r.metricas["oficial"] = caso.oficial
        resultados.append(r)
    return resultados


def indicadores(res):
    por_id = {r.id: r for r in res}
    out = {}
    campos = [v for i in ("C01", "C02", "C03", "C04") if i in por_id
              for v in por_id[i].metricas.get("campos_clave", {}).values()]
    if campos:
        out["Campos clave correctamente extraídos (C01-C04)"] = f"{sum(campos)}/{len(campos)} ({sum(campos) / len(campos):.0%})"
    if "C05" in por_id and "C06" in por_id:
        ok5 = por_id["C05"].metricas.get("duplicado_correcto")
        falso6 = por_id["C06"].metricas.get("falso_agrupamiento")
        out["Duplicados sugeridos correctamente (C05)"] = f"{int(bool(ok5))}/1"
        out["Falsos agrupamientos (C06)"] = f"{int(bool(falso6))}/1 (0 es lo deseado)"
    cap = [por_id[i].metricas.get("capacidad_respetada") for i in ("C12", "C13") if i in por_id]
    if cap:
        out["Cumplimiento de capacidad (C12-C13)"] = f"{sum(bool(c) for c in cap)}/{len(cap)}"
    if "C15" in por_id:
        m = por_id["C15"].metricas
        out["Decisiones con justificación y registro (C15)"] = f"{m.get('decisiones_con_registro', 0)}/{m.get('decisiones_totales', 0)}"
    return out


def tabla_md(res):
    filas = ["| Caso | Grupo | Descripción | Esperado | Obtenido | Criterio | Estado | Explicación |",
             "|---|---|---|---|---|---|---|---|"]
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    for r in res:
        filas.append(f"| {r.id} | {esc(r.grupo)} | {esc(r.descripcion)} | {esc(r.esperado)} | {esc(r.obtenido)} | "
                     f"{esc(r.criterio)} | **{r.estado}** | {esc(r.explicacion)} |")
    return "\n".join(filas)


def informe_md(res, ind):
    oficiales = [r for r in res if r.metricas.get("oficial")]
    cuenta = lambda lst, e: sum(r.estado == e for r in lst)  # noqa: E731
    return f"""# Resultados de evaluación — HIDRO360

Generado: {datetime.now().isoformat(timespec='seconds')} · versión {config.APP_VERSION} · reglas `{config.VERSION_REGLAS}`
Comando: `python -m evaluation.run_cases` (base y trazas temporales; intérprete por reglas; sin red).

> **Procedencia de los casos.** La guía oficial (Guía de Programación, p. 8) solo define los 15 casos por
> grupo y resultado esperado; **no incluye sus entradas**. Las entradas fueron redactadas por el equipo
> siguiendo esos grupos y los resultados esperados se derivan de las reglas escritas en la guía.
> Esto **no es** la evaluación oficial completa: está pendiente de contrastar con los casos oficiales.

## Resumen
- Casos de los 15 por grupo: PASS {cuenta(oficiales, 'PASS')} · NEEDS_REVIEW {cuenta(oficiales, 'NEEDS_REVIEW')} · FAIL {cuenta(oficiales, 'FAIL')} (de {len(oficiales)})
- Casos adicionales (seguridad, robustez, equidad): PASS {cuenta([r for r in res if not r.metricas.get('oficial')], 'PASS')} de {len(res) - len(oficiales)}

## Indicadores de demo (Propuesta, p. 6)
""" + "\n".join(f"- {k}: **{v}**" for k, v in ind.items()) + f"""

## Tabla de resultados

{tabla_md(res)}

## Criterios y límites
- PASS: todos los criterios se cumplen. NEEDS_REVIEW: solo falla un criterio no crítico. FAIL: falla un criterio crítico o hay excepción.
- Un caso nunca se aprueba solo porque no hubo excepción: cada uno compara valores esperados y obtenidos.
- Los cálculos de prioridad esperados (p. ej. 50.0, 25.0) se derivan a mano de la fórmula 40/30/30 con los topes de `config.py`.
- Las reglas de extracción son regex: los resultados no generalizan a cualquier redacción.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo", nargs="*")
    ap.add_argument("--sin-informe", action="store_true")
    args = ap.parse_args()
    res = ejecutar(args.solo)
    ind = indicadores(res)
    iconos = {"PASS": "PASS ", "FAIL": "FAIL ", "NEEDS_REVIEW": "REVIEW"}
    print(f"{'Caso':<5} {'Estado':<7} Descripción")
    for r in res:
        print(f"{r.id:<5} {iconos[r.estado]:<7} {r.descripcion}" + ("" if r.estado == "PASS" else f"\n        -> {r.explicacion}"))
    print()
    for k, v in ind.items():
        print(f"{k}: {v}")
    fallos = sum(r.estado == "FAIL" for r in res)
    print(f"\nPASS {sum(r.estado == 'PASS' for r in res)} · REVIEW {sum(r.estado == 'NEEDS_REVIEW' for r in res)} · FAIL {fallos}")
    if not args.sin_informe and not args.solo:
        (ROOT / "evaluation" / "resultados").mkdir(exist_ok=True)
        (ROOT / "evaluation" / "resultados" / "ultimo.json").write_text(
            json.dumps({"generado": datetime.now().isoformat(timespec="seconds"), "version": config.APP_VERSION,
                        "indicadores": ind, "resultados": [r.dict() for r in res]}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        (ROOT / "docs").mkdir(exist_ok=True)
        (ROOT / "docs" / "RESULTADOS_EVALUACION.md").write_text(informe_md(res, ind), encoding="utf-8")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
