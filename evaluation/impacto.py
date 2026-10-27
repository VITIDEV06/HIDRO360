"""
Experimento de impacto: HIDRO360 vs. línea base "ordenar por número de quejas".

*** DATOS SINTÉTICOS. Los resultados NO son evidencia de impacto real sobre comunidades. ***

DISEÑO (fijado ANTES de ejecutar; no se ajusta a mitad del experimento)
  - Cada escenario (semilla) define la VERDAD DE TERRENO: por sector, horas reales sin agua,
    personas afectadas y demanda real (15 L/persona). Luego genera reportes ruidosos.
  - Ambos métodos reciben EXACTAMENTE los mismos reportes, las mismas cisternas y el mismo
    planificador (services.planner, método de flujo); solo cambia el ORDEN de prioridad:
      * Línea base: sectores ordenados por número de reportes recibidos.
      * HIDRO360: services.prioritization (horas, población, vulnerabilidad; las quejas no suman).
    La estimación de demanda es la misma para ambos (máximo de personas declaradas por sector).
  - Sectores sin reportes no reciben agua en ninguno de los dos métodos (se desconoce su demanda).
    HIDRO360 los marca para verificación proactiva (services.workflow.visibilidad_sectores);
    se mide cuántos sectores con necesidad real quedaron marcados.
  - Familias de escenarios:
      * "sesgo_visibilidad": los sectores de vulnerabilidad alta/crítica reportan menos (supuesto
        del equipo, FAVORABLE al método propuesto).
      * "neutro": la propensión a reportar no depende de la vulnerabilidad (sin ese supuesto).
  - Modelo posterior a la entrega (SUPUESTO simplificador): horas residuales de un sector =
    horas_reales * (1 - fracción de su demanda real cubierta).
MÉTRICAS: demanda real cubierta/no cubierta, % satisfecho, cobertura de sectores, persona-horas sin agua
  residuales, Gini de horas residuales (services.equidad), reparto entre sectores con pocos reportes,
  correlación de rangos (Spearman) entre el orden del método y la necesidad real (horas x personas).
"""

import math
import random
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from data.datos_ficticios import SECTORES_FICTICIOS
from models.schemas import Cisterna, EstadoReporte, PrioridadSector, Reporte, Sector
from services import equidad, planner, prioritization

FAMILIAS = ("sesgo_visibilidad", "neutro")
RATIOS_OFERTA = (0.3, 0.5, 0.8)
N_SEMILLAS = 200
POCOS_REPORTES = 2


def generar_escenario(semilla: int, familia: str, ratio_oferta: float) -> Dict[str, Any]:
    rng = random.Random(f"{familia}-{semilla}")
    sectores = [Sector(**s) for s in SECTORES_FICTICIOS]
    verdad: Dict[str, Dict[str, Any]] = {}
    reportes: List[Reporte] = []
    rid = 1
    for s in sectores:
        con_corte = rng.random() > 0.2
        horas = float(rng.randint(6, 120)) if con_corte else 0.0
        personas = rng.randint(30, 400) if con_corte else 0
        demanda = planner.litros_requeridos(personas) if personas else 0
        prop = rng.uniform(0.2, 1.0)
        if familia == "sesgo_visibilidad" and s.vulnerabilidad.value in ("alta", "crítica"):
            prop = 0.0 if rng.random() < 0.25 else prop * 0.25
        n = int(prop * personas / 20 + rng.random()) if con_corte else 0
        verdad[s.nombre] = {"horas": horas, "personas": personas, "demanda": demanda, "reportes": n,
                            "vulnerabilidad": s.vulnerabilidad.value}
        for _ in range(n):
            reportes.append(Reporte(
                id=rid, mensaje_original="(sintético)", fecha_creacion=datetime(2026, 1, 1),
                estado=EstadoReporte.CONFIRMADO, sector=s.nombre,
                horas_sin_agua=None if rng.random() < 0.10 else max(1.0, round(horas * rng.uniform(0.7, 1.1))),
                personas_afectadas=None if rng.random() < 0.20 else max(1, round(personas * rng.uniform(0.5, 1.1))),
                ficticio=True))
            rid += 1
    demanda_total = sum(v["demanda"] for v in verdad.values())
    k = 6
    oferta_total = int(demanda_total * ratio_oferta)
    cap = max(1000, ((oferta_total // k) // 100 + 1) * 100)
    cisternas, resto = [], oferta_total
    for i in range(k):
        carga = min(cap, resto)
        resto -= carga
        cisternas.append(Cisterna(id=f"SIM-{i + 1}", capacidad_litros=max(cap, carga), litros_disponibles=carga))
    return {"sectores": sectores, "verdad": verdad, "reportes": reportes, "cisternas": cisternas,
            "oferta_total": oferta_total, "semilla": semilla, "familia": familia, "ratio_oferta": ratio_oferta}


def _prioridades_base(esc) -> List[PrioridadSector]:
    """Línea base: puntaje = número de reportes; demanda = misma estimación que HIDRO360."""
    por: Dict[str, List[Reporte]] = {}
    for r in esc["reportes"]:
        por.setdefault(r.sector, []).append(r)
    out = []
    for nombre, reps in por.items():
        pers = [r.personas_afectadas for r in reps if r.personas_afectadas is not None]
        out.append(PrioridadSector(sector=nombre, puntaje=float(len(reps)), categoria="n/a", factores=[],
                                   reportes_ids=[r.id for r in reps], reportes_contados=len(reps),
                                   personas_afectadas=max(pers) if pers else None, razones=[]))
    return out


def _metricas(esc, prios: List[PrioridadSector]) -> Dict[str, Any]:
    asig, pend = planner.proponer_asignaciones(prios, esc["cisternas"])
    verdad = esc["verdad"]
    litros = {s: 0 for s in verdad}
    for a in asig:
        litros[a.sector] += a.litros
    con_necesidad = [s for s, v in verdad.items() if v["demanda"] > 0]
    cubierta = {s: min(litros[s], verdad[s]["demanda"]) for s in con_necesidad}
    dem_total = sum(verdad[s]["demanda"] for s in con_necesidad)
    frac = {s: cubierta[s] / verdad[s]["demanda"] for s in con_necesidad}
    horas_res = {s: (verdad[s]["horas"] * (1 - frac[s]) if s in frac else 0.0) for s in verdad}
    persona_horas = sum(horas_res[s] * verdad[s]["personas"] for s in verdad)
    persona_horas_antes = sum(v["horas"] * v["personas"] for v in verdad.values())
    pocos = [s for s in con_necesidad if verdad[s]["reportes"] <= POCOS_REPORTES]
    dem_pocos = sum(verdad[s]["demanda"] for s in pocos)
    orden = {p.sector: p.puntaje for p in prios}
    visibles = [s for s in con_necesidad if s in orden]
    return {
        "demanda_real_total": dem_total,
        "demanda_cubierta": sum(cubierta.values()),
        "demanda_no_cubierta": dem_total - sum(cubierta.values()),
        "proporcion_satisfecha": round(sum(cubierta.values()) / dem_total, 4) if dem_total else None,
        "sectores_con_necesidad": len(con_necesidad),
        "sectores_con_algo_de_agua": sum(1 for s in con_necesidad if litros[s] > 0),
        "sectores_completamente_cubiertos": sum(1 for s in con_necesidad if frac[s] >= 0.999),
        "sectores_pendientes": sorted(s for s in con_necesidad if frac[s] < 0.999),
        "persona_horas_residuales": round(persona_horas, 1),
        "reduccion_persona_horas": round(persona_horas_antes - persona_horas, 1),
        "gini_horas_residuales": equidad.gini([horas_res[s] for s in verdad]),
        "demanda_pocos_reportes": dem_pocos,
        "cubierta_pocos_reportes": sum(cubierta[s] for s in pocos),
        "spearman_orden_vs_necesidad": equidad.spearman(
            [orden[s] for s in visibles], [verdad[s]["horas"] * verdad[s]["personas"] for s in visibles]),
        "litros_por_sector": litros,
        "horas_residuales": {s: round(h, 1) for s, h in horas_res.items()},
        "oferta_total": esc["oferta_total"],
    }


def evaluar_escenario(esc) -> Dict[str, Any]:
    base = _metricas(esc, _prioridades_base(esc))
    ac_prios = prioritization.calcular_prioridades(esc["reportes"], esc["sectores"])
    ac = _metricas(esc, ac_prios)
    horas_antes = [v["horas"] for v in esc["verdad"].values()]
    con_reportes = {r.sector for r in esc["reportes"]}
    invisibles = [s for s, v in esc["verdad"].items() if v["demanda"] > 0 and s not in con_reportes]
    vul = {s.nombre: s.vulnerabilidad.value for s in esc["sectores"]}
    marcados = [s for s in invisibles if vul[s] in ("alta", "crítica")
                or (next(x for x in esc["sectores"] if x.nombre == s).antecedentes_interrupciones or 0) > 0]
    return {
        "semilla": esc["semilla"], "familia": esc["familia"], "ratio_oferta": esc["ratio_oferta"],
        "gini_horas_antes": equidad.gini(horas_antes),
        "linea_base": base, "agua_clara": ac,
        "orden_base": [p.sector for p in sorted(_prioridades_base(esc), key=lambda p: (-p.puntaje, p.sector))],
        "orden_agua_clara": [p.sector for p in ac_prios],
        "invisibles_con_necesidad": invisibles, "invisibles_marcados_para_verificar": marcados,
        "verdad": esc["verdad"],
    }


def _media(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def resumen(familia: str, ratio: float, n: int = N_SEMILLAS) -> Dict[str, Any]:
    evals = [evaluar_escenario(generar_escenario(s, familia, ratio)) for s in range(n)]
    out: Dict[str, Any] = {"familia": familia, "ratio_oferta": ratio, "n_escenarios": n, "metricas": {}}
    claves = ["proporcion_satisfecha", "demanda_no_cubierta", "sectores_con_algo_de_agua",
              "sectores_completamente_cubiertos", "persona_horas_residuales", "gini_horas_residuales",
              "spearman_orden_vs_necesidad"]
    for k in claves:
        out["metricas"][k] = {"linea_base": _media([e["linea_base"][k] for e in evals]),
                              "agua_clara": _media([e["agua_clara"][k] for e in evals])}
    pocos_b = sum(e["linea_base"]["cubierta_pocos_reportes"] for e in evals)
    pocos_a = sum(e["agua_clara"]["cubierta_pocos_reportes"] for e in evals)
    dem_pocos = sum(e["agua_clara"]["demanda_pocos_reportes"] for e in evals)
    out["metricas"]["proporcion_cubierta_sectores_pocos_reportes"] = {
        "linea_base": round(pocos_b / dem_pocos, 4) if dem_pocos else None,
        "agua_clara": round(pocos_a / dem_pocos, 4) if dem_pocos else None}
    mejor = peor = igual = 0
    for e in evals:
        a, b = e["agua_clara"]["persona_horas_residuales"], e["linea_base"]["persona_horas_residuales"]
        if a < b - 1e-9:
            mejor += 1
        elif a > b + 1e-9:
            peor += 1
        else:
            igual += 1
    out["escenarios_agua_clara_mejor_en_persona_horas"] = {"mejor": mejor, "igual": igual, "peor": peor}
    inv = sum(len(e["invisibles_con_necesidad"]) for e in evals)
    mar = sum(len(e["invisibles_marcados_para_verificar"]) for e in evals)
    out["sectores_invisibles_con_necesidad_total"] = inv
    out["marcados_para_verificacion_proactiva"] = mar
    out["gini_horas_antes_media"] = _media([e["gini_horas_antes"] for e in evals])
    return out


def experimento_completo(n: int = N_SEMILLAS) -> Dict[str, Any]:
    return {"version_reglas": config.VERSION_REGLAS, "sintetico": True, "n_escenarios": n,
            "resumenes": [resumen(f, r, n) for f in FAMILIAS for r in RATIOS_OFERTA]}
