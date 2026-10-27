"""
Módulo 3 - Priorización (determinista, sin IA).

puntaje = 100 * (0.40*horas + 0.30*población + 0.30*vulnerabilidad), cada factor en [0,1]:
  horas       = min(horas_sin_agua / 96, 1)
  población   = min(personas_afectadas / 300, 1)
  vulnerab.   = baja .25 | media .5 | alta .75 | crítica 1 (catálogo del sector), elevada a
                1.0 si un reporte CONFIRMADO del sector indica servicio esencial afectado y a
                0.75 si indica personas vulnerables (adultos mayores, niños pequeños, enfermos)

Por sector se toma el MÁXIMO de horas y de personas entre sus reportes activos (no la suma):
por eso el número de quejas repetidas NO aumenta la prioridad. Un dato faltante aporta 0
y se declara como razón; no se inventa.
"""

from collections import defaultdict
from typing import Dict, List

import config
from models.schemas import (EstadoReporte, FactorPrioridad, PrioridadSector, Reporte,
                            Sector, Severidad)

_ORDEN_SEV = [Severidad.BAJA, Severidad.MEDIA, Severidad.ALTA, Severidad.CRITICA]


def categoria(puntaje: float) -> str:
    for minimo, nombre in config.CATEGORIAS:
        if puntaje >= minimo:
            return nombre
    return "BAJA"


def reportes_activos(reportes: List[Reporte]) -> List[Reporte]:
    return [r for r in reportes if r.estado == EstadoReporte.CONFIRMADO and r.sector]


def calcular_prioridades(reportes: List[Reporte], sectores: List[Sector]) -> List[PrioridadSector]:
    cat: Dict[str, Sector] = {s.nombre: s for s in sectores}
    por_sector: Dict[str, List[Reporte]] = defaultdict(list)
    for r in reportes_activos(reportes):
        if r.sector in cat:
            por_sector[r.sector].append(r)

    resultado: List[PrioridadSector] = []
    for nombre, reps in por_sector.items():
        sector = cat[nombre]
        horas_vals = [r.horas_sin_agua for r in reps if r.horas_sin_agua is not None]
        pers_vals = [r.personas_afectadas for r in reps if r.personas_afectadas is not None]
        sev_vals = [r.severidad for r in reps if r.severidad is not None]
        horas = max(horas_vals) if horas_vals else None
        personas = max(pers_vals) if pers_vals else None
        sev = max(sev_vals, key=_ORDEN_SEV.index) if sev_vals else None

        h_norm = min(horas / config.HORAS_TOPE, 1.0) if horas is not None else 0.0
        p_norm = min(personas / config.PERSONAS_TOPE, 1.0) if personas is not None else 0.0
        v_norm = config.VULNERABILIDAD_VALORES.get(sector.vulnerabilidad.value, 0.5)
        v_expl = f"vulnerabilidad '{sector.vulnerabilidad.value}' según el catálogo"
        esenciales = sorted({r.servicio_esencial for r in reps if r.servicio_esencial})
        vulnerables = sorted({v for r in reps for v in r.vulnerabilidades})
        if esenciales and config.VULN_SERVICIO_ESENCIAL > v_norm:
            v_norm = config.VULN_SERVICIO_ESENCIAL
            v_expl = f"servicio esencial confirmado ({', '.join(esenciales)}): vulnerabilidad elevada a {v_norm:g}"
        elif vulnerables and config.VULN_PERSONAS_VULNERABLES > v_norm:
            v_norm = config.VULN_PERSONAS_VULNERABLES
            v_expl = f"personas vulnerables confirmadas ({', '.join(vulnerables)}): vulnerabilidad elevada a {v_norm:g}"

        factores = [
            FactorPrioridad(
                nombre="Horas sin agua", valor=horas, normalizado=round(h_norm, 4),
                peso=config.PESO_HORAS, contribucion=round(100 * config.PESO_HORAS * h_norm, 2),
                explicacion=(f"{horas:g} h de un tope de {config.HORAS_TOPE:g} h" if horas is not None
                             else "dato faltante: aporta 0 hasta que se confirme")),
            FactorPrioridad(
                nombre="Población afectada", valor=personas, normalizado=round(p_norm, 4),
                peso=config.PESO_POBLACION, contribucion=round(100 * config.PESO_POBLACION * p_norm, 2),
                explicacion=(f"{personas} personas de un tope de {config.PERSONAS_TOPE}" if personas is not None
                             else "dato faltante: aporta 0 hasta que se confirme")),
            FactorPrioridad(
                nombre="Vulnerabilidad del sector", valor=v_norm, normalizado=round(v_norm, 4),
                peso=config.PESO_VULNERABILIDAD, contribucion=round(100 * config.PESO_VULNERABILIDAD * v_norm, 2),
                explicacion=v_expl),
        ]
        puntaje = round(sum(f.contribucion for f in factores), 2)

        razones = [f"{f.nombre}: +{f.contribucion:g} pts ({f.explicacion})" for f in factores]
        if len(reps) > 1:
            razones.append(f"{len(reps)} reportes del sector: se usa el valor máximo, la cantidad de quejas NO suma puntos.")
        if len(horas_vals) > 1 and max(horas_vals) - min(horas_vals) > 24:
            razones.append(f"Reportes con horas sin agua contradictorias ({min(horas_vals):g}-{max(horas_vals):g} h): "
                           "se usa el máximo declarado; verifique con el operador o en terreno.")
        if horas is None or personas is None:
            razones.append("Faltan datos: corrija el reporte para que el puntaje sea completo.")

        resultado.append(PrioridadSector(
            sector=nombre, puntaje=puntaje, categoria=categoria(puntaje), factores=factores,
            reportes_ids=sorted(r.id for r in reps), reportes_contados=len(reps),
            horas_sin_agua=horas, personas_afectadas=personas, severidad=sev, razones=razones))

    resultado.sort(key=lambda p: (-p.puntaje, p.sector))
    return resultado
