"""
Módulo 2 - Geolocalización y deduplicación.

Sector: se busca en un catálogo local (nombre, alias, referencias coloquiales).
  - exacto / alias / referencia sin ambigüedad  -> se PROPONE como sector (el operador lo confirma)
  - varios sectores distintos                    -> ambiguo: no se elige, se muestran opciones
  - similitud ortográfica (difflib, ratio>=0.82)  -> solo SUGERENCIA; nunca se asigna sola
  - sin coincidencia                              -> ninguno
La similitud NO equivale a ubicación confirmada.

Duplicados: se SUGIEREN con motivos legibles; nunca se fusionan. Una repetición puede significar
que el problema continúa sin resolverse; por eso se muestra tal cual y decide una persona.
"""

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import config
from models.schemas import CandidatoDuplicado, EstadoReporte, Reporte, Sector
from services.textutil import normalizar, palabras_clave

UMBRAL_DIFUSO = 0.82


def resolver_sector(nombre: Optional[str], sectores: List[Sector]) -> Optional[Sector]:
    """Sector del catálogo que coincide exactamente (sin acentos/mayúsculas) con `nombre`."""
    if not nombre:
        return None
    n = normalizar(nombre).strip()
    for s in sectores:
        if normalizar(s.nombre) == n or n in [normalizar(a) for a in s.alias]:
            return s
    return None


def buscar_sector_en_texto(texto: str, sectores: List[Sector]) -> Dict[str, Any]:
    """Devuelve {'decision': exacto|ambiguo|difuso|ninguno, 'sector': nombre|None, 'candidatos': [...]}.
    `sector` solo se rellena en 'exacto'."""
    t = normalizar(texto)
    exactos: List[Tuple[int, int, str, str]] = []  # (inicio, fin, sector, metodo)
    for s in sectores:
        nombres = [(s.nombre, "exacto")] + [(a, "alias") for a in s.alias] + [(r, "referencia") for r in s.referencias]
        for nombre, metodo in nombres:
            n = normalizar(nombre)
            for m in re.finditer(rf"\b{re.escape(n)}\b", t):
                exactos.append((m.start(), m.end(), s.nombre, metodo))
    # el nombre más largo gana sobre sus subcadenas ("Nuevo Arraiján" > "Arraiján")
    finales = [e for e in exactos
               if not any(o != e and o[0] <= e[0] and e[1] <= o[1] and (o[1] - o[0]) > (e[1] - e[0]) for o in exactos)]
    distintos = sorted({e[2] for e in finales})
    if distintos:
        cands = [{"sector": n, "similitud": 1.0,
                  "metodo": next(e[3] for e in finales if e[2] == n)} for n in distintos]
        if len(distintos) == 1:
            return {"decision": "exacto", "sector": distintos[0], "candidatos": cands}
        return {"decision": "ambiguo", "sector": None, "candidatos": cands}

    # similitud ortográfica sobre ventanas de palabras
    tokens = re.findall(r"[a-z0-9]+", t)
    mejores: Dict[str, float] = {}
    for s in sectores:
        for nombre in [s.nombre, *s.alias]:
            n = normalizar(nombre)
            if len(n) < 5:
                continue
            k = len(n.split())
            for i in range(0, max(len(tokens) - k + 1, 0)):
                ventana = " ".join(tokens[i:i + k])
                r = SequenceMatcher(None, ventana, n).ratio()
                if r >= UMBRAL_DIFUSO and r > mejores.get(s.nombre, 0):
                    mejores[s.nombre] = round(r, 2)
    if mejores:
        cands = [{"sector": n, "similitud": r, "metodo": "difuso"}
                 for n, r in sorted(mejores.items(), key=lambda x: (-x[1], x[0]))]
        return {"decision": "difuso", "sector": None, "candidatos": cands}
    return {"decision": "ninguno", "sector": None, "candidatos": []}


# ----------------------------------------------------------------------- duplicados
def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def sugerir_duplicados(nuevo: Reporte, existentes: List[Reporte],
                       umbral: float = config.UMBRAL_DUPLICADO) -> List[CandidatoDuplicado]:
    """Compara con reportes del MISMO sector. Puntaje 0-1 con motivos legibles."""
    candidatos: List[CandidatoDuplicado] = []
    if not nuevo.sector:
        return candidatos
    for o in existentes:
        if o.id == nuevo.id or o.estado in (EstadoReporte.RECHAZADO, EstadoReporte.DUPLICADO):
            continue
        if o.sector != nuevo.sector:
            continue
        motivos = ["mismo sector"]
        score = 0.25

        # cercanía en el tiempo de recepción
        dh = abs((nuevo.fecha_creacion - o.fecha_creacion).total_seconds()) / 3600
        if dh <= config.VENTANA_DUPLICADO_HORAS:
            score += 0.05
            motivos.append(f"recibidos con {dh:.1f} h de diferencia")
        else:
            score -= 0.10
            motivos.append(f"recibidos con {dh:.0f} h de diferencia (posible incidente distinto)")

        if nuevo.horas_sin_agua is not None and o.horas_sin_agua is not None:
            dif = abs(nuevo.horas_sin_agua - o.horas_sin_agua)
            if dif <= 6:
                score += 0.20
                motivos.append(f"tiempo sin agua similar ({nuevo.horas_sin_agua:g} h vs {o.horas_sin_agua:g} h)")
            elif dif <= 12:
                score += 0.08
                motivos.append("tiempo sin agua parecido")
            else:
                score -= 0.10
                motivos.append(f"tiempo sin agua distinto ({nuevo.horas_sin_agua:g} h vs {o.horas_sin_agua:g} h)")

        ref_n = nuevo.calle or nuevo.referencia_ubicacion
        ref_o = o.calle or o.referencia_ubicacion
        if ref_n and ref_o:
            sc = _jaccard(palabras_clave(ref_n), palabras_clave(ref_o))
            if sc >= 0.5:
                score += 0.25
                motivos.append(f"misma calle/referencia ('{ref_o}')")
            else:
                score -= 0.15
                motivos.append(f"calle/referencia distinta ('{ref_n}' vs '{ref_o}')")

        if nuevo.tipo_incidencia and o.tipo_incidencia and nuevo.tipo_incidencia == o.tipo_incidencia:
            score += 0.05
            motivos.append(f"mismo tipo de incidencia ({o.tipo_incidencia})")

        st = _jaccard(palabras_clave(nuevo.mensaje_original), palabras_clave(o.mensaje_original))
        score += 0.20 * min(st * 2, 1.0)
        if st >= 0.3:
            motivos.append(f"texto parecido ({st:.0%} de palabras clave)")

        score = round(max(0.0, min(score, 1.0)), 2)
        if score >= umbral:
            motivos.append("Una repetición puede indicar que el problema continúa: decide una persona.")
            candidatos.append(CandidatoDuplicado(reporte_id=o.id, similitud=score, motivos=motivos, dudoso=True))
    candidatos.sort(key=lambda c: (-c.similitud, c.reporte_id))
    return candidatos
