"""
Módulo 4 - Planificador (determinista y reproducible).

MODELO
  Nodos: cisternas (oferta) -> sectores (demanda). Cada cisterna ofrece
         min(litros_disponibles, capacidad_litros) si está `disponible`.
  Arco cisterna->sector solo si la cisterna es elegible para ese sector (sectores_elegibles).
  Demanda de un sector = personas_afectadas * L/persona, redondeada a 100 L.
OBJETIVO (lexicográfico): maximizar la cobertura del sector de mayor prioridad, luego la del
  siguiente, etc. Se logra con flujo máximo incremental (Edmonds-Karp): se añade cada sector en
  orden de prioridad y se aumenta el flujo; el flujo hacia sectores ya añadidos nunca disminuye.
  Es óptimo para ese objetivo aun con restricciones de elegibilidad (donde un reparto voraz puede
  fallar). Un sector puede usar varias cisternas.
RESTRICCIONES: litros por cisterna <= min(disponibles, capacidad); solo cisternas disponibles y
  elegibles; cobertura de un sector <= su demanda.
DESEMPATE ESTABLE: prioridad desc, nombre de sector; al elegir cisterna: sede en el mismo sector,
  más litros libres, id. Misma entrada + misma versión => misma salida.
LÍMITES: no modela rutas, distancias, ventanas horarias ni ETA; no promete horas de llegada.
  No se usa OR-Tools: el problema es una asignación de transporte pequeña que este algoritmo
  resuelve de forma exacta para el objetivo declarado, sin dependencia pesada (ver docs/PLANIFICADOR.md).
"""

from collections import deque
from typing import Dict, List, Tuple

import config
from models.schemas import (Asignacion, Cisterna, EstadoCisterna, PrioridadSector,
                            SectorPendiente)

METODOS = ("flujo", "greedy")


def litros_requeridos(personas: int, litros_por_persona: int = config.LITROS_POR_PERSONA) -> int:
    bruto = personas * litros_por_persona
    return ((bruto + 99) // 100) * 100


def _oferta(c: Cisterna) -> int:
    if c.estado != EstadoCisterna.DISPONIBLE:
        return 0
    return min(c.litros_disponibles, c.capacidad_litros)


def _elegible(c: Cisterna, sector: str) -> bool:
    return c.sectores_elegibles is None or sector in c.sectores_elegibles


def _pendientes(pri: List[PrioridadSector], cist: List[Cisterna], requerido: Dict[str, int],
                cubierto: Dict[str, int]) -> List[SectorPendiente]:
    pend: List[SectorPendiente] = []
    hay_disp = any(_oferta(c) > 0 for c in cist)
    for p in pri:
        if not p.personas_afectadas or p.personas_afectadas <= 0:
            pend.append(SectorPendiente(
                sector=p.sector, prioridad=p.puntaje, litros_requeridos=0, litros_cubiertos=0, litros_faltantes=0,
                motivo="Falta el dato de personas afectadas: no se estima la demanda. Corrija el reporte."))
            continue
        req, cub = requerido[p.sector], cubierto.get(p.sector, 0)
        if cub >= req:
            continue
        elegibles = [c for c in cist if _oferta(c) > 0 and _elegible(c, p.sector)]
        if not hay_disp:
            motivo = "No hay cisternas disponibles."
        elif not elegibles:
            motivo = "Ninguna cisterna disponible es elegible para este sector."
        elif cub == 0:
            motivo = "Capacidad agotada: las cisternas elegibles ya se asignaron a sectores de mayor prioridad."
        else:
            motivo = "Cobertura parcial: la capacidad restante de las cisternas elegibles no alcanza."
        pend.append(SectorPendiente(sector=p.sector, prioridad=p.puntaje, litros_requeridos=req,
                                    litros_cubiertos=cub, litros_faltantes=req - cub, motivo=motivo))
    return pend


def proponer_asignaciones(prioridades: List[PrioridadSector], cisternas: List[Cisterna],
                          litros_por_persona: int = config.LITROS_POR_PERSONA, metodo: str = "flujo"
                          ) -> Tuple[List[Asignacion], List[SectorPendiente]]:
    if metodo == "greedy":
        return proponer_asignaciones_greedy(prioridades, cisternas, litros_por_persona)
    if metodo != "flujo":
        raise ValueError(f"método desconocido: {metodo}")

    pri = sorted(prioridades, key=lambda p: (-p.puntaje, p.sector))
    cist = sorted(cisternas, key=lambda c: c.id)
    por_id = {c.id: c for c in cist}
    cap: Dict[Tuple, int] = {}
    adj: Dict[object, List[object]] = {}

    def arco(u, v, c):
        cap[(u, v)] = cap.get((u, v), 0) + c
        cap.setdefault((v, u), 0)
        for a, b in ((u, v), (v, u)):
            if b not in adj.setdefault(a, []):
                adj[a].append(b)

    for c in cist:
        if _oferta(c) > 0:
            arco("S", ("c", c.id), _oferta(c))
    requerido: Dict[str, int] = {}
    cubierto: Dict[str, int] = {}

    def aumentar(orden: List[str]) -> int:
        total = 0
        while True:
            padre: Dict[object, object] = {"S": None}
            q = deque(["S"])
            while q and "T" not in padre:
                u = q.popleft()
                vecinos = adj.get(u, [])
                if u == "S":
                    vecinos = sorted(vecinos, key=lambda v: orden.index(v[1]) if v[1] in orden else 10 ** 6)
                for v in vecinos:
                    if v not in padre and cap[(u, v)] > 0:
                        padre[v] = u
                        q.append(v)
            if "T" not in padre:
                return total
            v, cuello = "T", 10 ** 12
            while padre[v] is not None:
                cuello = min(cuello, cap[(padre[v], v)])
                v = padre[v]
            v = "T"
            while padre[v] is not None:
                u = padre[v]
                cap[(u, v)] -= cuello
                cap[(v, u)] += cuello
                v = u
            total += cuello

    for p in pri:
        if not p.personas_afectadas or p.personas_afectadas <= 0:
            continue
        req = litros_requeridos(p.personas_afectadas, litros_por_persona)
        requerido[p.sector] = req
        nodo = ("s", p.sector)
        for c in cist:
            if _oferta(c) > 0 and _elegible(c, p.sector):
                arco(("c", c.id), nodo, _oferta(c))
        arco(nodo, "T", req)
        libre = {cid: cap[("S", ("c", cid))] for cid in por_id if ("S", ("c", cid)) in cap}
        orden = sorted(libre, key=lambda cid: ((por_id[cid].sector_base or "") != p.sector, -libre[cid], cid))
        cubierto[p.sector] = cubierto.get(p.sector, 0) + aumentar(orden)

    asignaciones: List[Asignacion] = []
    for p in pri:
        nodo = ("s", p.sector)
        for c in cist:
            if (("c", c.id), nodo) in cap:
                litros = cap[(nodo, ("c", c.id))]  # flujo neto cisterna->sector = capacidad inversa
                if litros > 0:
                    asignaciones.append(Asignacion(
                        sector=p.sector, cisterna_id=c.id, litros=litros, prioridad=p.puntaje,
                        nota="cisterna con sede en el sector" if c.sector_base == p.sector else ""))
    # la cobertura real de cada sector es la suma de su flujo final (los aumentos pueden reencaminar)
    real: Dict[str, int] = {}
    for a in asignaciones:
        real[a.sector] = real.get(a.sector, 0) + a.litros
    return asignaciones, _pendientes(pri, cist, requerido, real)


def proponer_asignaciones_greedy(prioridades: List[PrioridadSector], cisternas: List[Cisterna],
                                 litros_por_persona: int = config.LITROS_POR_PERSONA
                                 ) -> Tuple[List[Asignacion], List[SectorPendiente]]:
    """Reparto voraz por prioridad (referencia para comparar con el método de flujo)."""
    pri = sorted(prioridades, key=lambda p: (-p.puntaje, p.sector))
    libre = {c.id: _oferta(c) for c in cisternas if _oferta(c) > 0}
    por_id = {c.id: c for c in cisternas}
    asignaciones: List[Asignacion] = []
    requerido: Dict[str, int] = {}
    cubierto: Dict[str, int] = {}
    for p in pri:
        if not p.personas_afectadas or p.personas_afectadas <= 0:
            continue
        req = litros_requeridos(p.personas_afectadas, litros_por_persona)
        requerido[p.sector] = req
        falta = req
        orden = sorted((cid for cid in libre if _elegible(por_id[cid], p.sector)),
                       key=lambda cid: ((por_id[cid].sector_base or "") != p.sector, -libre[cid], cid))
        for cid in orden:
            if falta <= 0:
                break
            tomar = min(libre[cid], falta)
            if tomar <= 0:
                continue
            libre[cid] -= tomar
            falta -= tomar
            asignaciones.append(Asignacion(sector=p.sector, cisterna_id=cid, litros=tomar, prioridad=p.puntaje,
                                           nota="cisterna con sede en el sector" if por_id[cid].sector_base == p.sector else ""))
        cubierto[p.sector] = req - falta
    return asignaciones, _pendientes(pri, list(cisternas), requerido, cubierto)


def validar_asignaciones(asignaciones: List[Asignacion], cisternas: List[Cisterna]) -> List[str]:
    """Errores si un plan (p. ej. editado a mano) viola capacidad, disponibilidad o elegibilidad."""
    errores: List[str] = []
    por_id = {c.id: c for c in cisternas}
    usados: Dict[str, int] = {}
    for a in asignaciones:
        c = por_id.get(a.cisterna_id)
        if c is None:
            errores.append(f"La cisterna {a.cisterna_id} no existe.")
            continue
        if c.estado != EstadoCisterna.DISPONIBLE:
            errores.append(f"{c.id} no está disponible ({c.estado.value}).")
        if not _elegible(c, a.sector):
            errores.append(f"{c.id} no es elegible para {a.sector}.")
        usados[c.id] = usados.get(c.id, 0) + a.litros
    for cid, total in usados.items():
        c = por_id.get(cid)
        if c and total > _oferta(c):
            errores.append(f"{cid} excede su disponibilidad/capacidad: {total} L asignados de {_oferta(c)} L.")
    return errores
