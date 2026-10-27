"""
Orquestador del flujo asistido por IA (sin base de datos: recibe datos, devuelve una `Recomendacion`).

    Reporte recibido -> validación -> duplicados -> clasificación -> prioridad -> recomendación -> asignación
                                                                                 -> [APROBACIÓN HUMANA] -> misión

Reglas que no se rompen (ver docs/IA_GEMINI.md):
  - La IA solo participa en DOS etapas: «clasificación» (interpretar el texto, vía services.ingest) y
    «recomendación» (texto breve de acción sugerida, vía services.ia_tareas). Todo lo demás es determinista
    y reutiliza los servicios existentes: geoloc (duplicados), prioritization (puntaje), planner (asignación).
  - Cada etapa declara su PROCEDENCIA real: «gemini» solo si Gemini respondió y pasó la validación;
    «reglas» si el modo local es la configuración; «reglas(fallback)» si se intentó Gemini y falló.
  - Una etapa que falla queda marcada `error` y el flujo continúa solo si es seguro; si el reporte no es
    analizable (rechazado, duplicado, ya en plan) el flujo se detiene con motivo y el resto queda `omitida`.
  - Este módulo no escribe nada: no confirma reportes, no aprueba planes ni crea misiones. La propuesta
    termina en una `Recomendacion` en estado «propuesta» que una persona decide (services.workflow_ia).
"""

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from models.schemas import (Asignacion, Cisterna, EstadoReporte, EtapaIA, Parada, Recomendacion, Reporte,
                            Sector)
from services import geoloc, ia_gemini, ia_tareas, ingest, planner, prioritization, tracing

ETAPAS = ("validacion", "duplicados", "clasificacion", "prioridad", "recomendacion", "asignacion")
ETAPAS_CON_IA = ("clasificacion", "recomendacion")
ESTADOS_ANALIZABLES = (EstadoReporte.POR_REVISAR, EstadoReporte.CONFIRMADO)

# Presupuesto TOTAL de tiempo de las dos etapas con IA de un análisis (segundos). Pensado para funciones
# serverless con límite por invocación: al agotarse, las etapas con IA que falten usan el respaldo local
# (`reglas(fallback)`) y el análisis termina igual. Sin presupuesto, el peor caso sería
# 2 llamadas x (15 s + 1,5 s de pausa + 15 s) = 63 s. Con 20 s el análisis termina en ~20 s + el cálculo
# determinista (decenas de ms). La API de Gemini exige plazos >= 10 s (ia_gemini.MINIMO_UTIL_S): un presupuesto
# menor equivale a desactivar la IA en el análisis. Ajuste: HIDRO360_IA_PRESUPUESTO_S.
PRESUPUESTO_IA_S = 20.0


def presupuesto_ia_s() -> float:
    try:
        v = float(os.environ.get("HIDRO360_IA_PRESUPUESTO_S", "") or PRESUPUESTO_IA_S)
    except ValueError:
        return PRESUPUESTO_IA_S
    return v if v > 0 else PRESUPUESTO_IA_S

# (estado, proveedor, resumen, detalle) que devuelve cada etapa; el resultado útil queda en `memoria`.
_Salida = Tuple[str, str, str, Dict[str, Any]]


class _Detener(Exception):
    """Una etapa decide que el flujo no puede continuar (motivo claro en el mensaje)."""


def _etapa(nombre: str, reporte_id: int, fn: Callable[[], _Salida], etapas: List[EtapaIA]) -> EtapaIA:
    """Ejecuta una etapa aislada: su error no corrompe las demás y queda trazado."""
    inicio = time.perf_counter()
    try:
        with tracing.traza("orquestador", f"etapa.{nombre}", {"reporte_id": reporte_id}) as t:
            estado, proveedor, resumen, detalle = fn()
            t["status"] = "fallback" if estado == "fallback" else "ok"
            t["output"] = {"estado": estado, "proveedor": proveedor}
    except _Detener as e:
        estado, proveedor, resumen, detalle = "error", "reglas", str(e), {"detiene_flujo": True}
    except Exception as e:  # una etapa rota no tumba el resto
        estado, proveedor = "error", "reglas"
        resumen, detalle = f"La etapa falló ({type(e).__name__}); se continuó con los datos disponibles.", {}
    etapa = EtapaIA(nombre=nombre, estado=estado, proveedor=proveedor,
                    duracion_ms=int((time.perf_counter() - inicio) * 1000), resumen=resumen, detalle=detalle)
    etapas.append(etapa)
    return etapa


def procedencia_global(etapas: List[EtapaIA]) -> str:
    """«gemini» solo si TODAS las etapas con IA respondieron Gemini; «reglas(fallback)» si alguna lo intentó y
    falló; en cualquier otro caso «reglas». Nunca se rotula como Gemini algo que no lo fue."""
    con_ia = [e for e in etapas if e.nombre in ETAPAS_CON_IA and e.estado != "omitida"]
    if any(e.proveedor == "reglas(fallback)" for e in con_ia):
        return "reglas(fallback)"
    if con_ia and all(e.proveedor == "gemini" for e in con_ia):
        return "gemini"
    return "reglas"


def analizar(r: Reporte, reportes: List[Reporte], sectores: List[Sector], cisternas: List[Cisterna],
             ocupadas: Optional[set] = None) -> Recomendacion:
    """Ejecuta las seis etapas EN ORDEN y devuelve la recomendación (sin persistir; id=0).
    `ocupadas`: ids de cisternas con una misión activa; no se proponen (solo una misión activa por cisterna)."""
    ocupadas = ocupadas or set()
    cisternas = [c for c in cisternas if c.id not in ocupadas]
    etapas: List[EtapaIA] = []
    mem: Dict[str, Any] = {"sector": None, "duplicados": [], "prioridad": None, "prios": [], "cisterna": None,
                           "paradas": [], "accion": ""}
    cat = {s.nombre: s for s in sectores}

    # ---- 1. validación (determinista)
    def validacion() -> _Salida:
        if not (r.mensaje_original or "").strip():
            raise _Detener("El reporte no tiene texto: no se puede analizar.")
        if r.estado not in ESTADOS_ANALIZABLES:
            raise _Detener(f"El reporte está '{r.estado.value}': no se analiza (solo 'por_revisar' o 'confirmado').")
        mem["sector"] = r.sector if r.sector in cat else None
        det = {"estado_reporte": r.estado.value, "sector_en_catalogo": mem["sector"] is not None,
               "datos_faltantes": list(r.datos_faltantes), "datos_inciertos": list(r.datos_inciertos),
               "confirmado": r.estado == EstadoReporte.CONFIRMADO}
        avisos = []
        if mem["sector"] is None:
            avisos.append("sin sector válido del catálogo")
        if r.estado == EstadoReporte.POR_REVISAR:
            avisos.append("aún no confirmado por una persona")
        return ("ok", "reglas", "Reporte analizable" + (f" ({'; '.join(avisos)})." if avisos else "."), det)

    primera = _etapa("validacion", r.id, validacion, etapas)
    detenido = primera.estado == "error"

    def omitida(nombre: str, motivo: str) -> None:
        etapas.append(EtapaIA(nombre=nombre, estado="omitida", proveedor="reglas", resumen=motivo))

    # ---- 2. duplicados (determinista; solo se SUGIEREN, nunca se fusionan)
    def duplicados() -> _Salida:
        if mem["sector"] is None:
            return ("omitida", "reglas", "Sin sector no se comparan reportes.", {})
        cands = geoloc.sugerir_duplicados(r, reportes)
        mem["duplicados"] = [c.reporte_id for c in cands]
        return ("ok", "reglas", f"{len(cands)} posible(s) duplicado(s); ninguno se fusiona automáticamente.",
                {"candidatos": [c.model_dump(mode="json") for c in cands]})

    # ---- 3. clasificación (Gemini propone, el código valida; fallback local visible)
    def clasificacion() -> _Salida:
        ex = ingest.interpretar(r.mensaje_original, sectores)
        det = {"tipo_incidencia": ex.tipo_incidencia,
               "severidad_texto": ex.severidad.value if ex.severidad else None,
               "sector_texto": ex.sector, "vulnerabilidades": ex.vulnerabilidades,
               "servicio_esencial": ex.servicio_esencial, "confianza_declarada": ex.confianza_declarada,
               "texto_con_instrucciones": "texto_con_instrucciones" in ex.datos_inciertos,
               "difiere_del_reporte": [c for c, v in (("sector", ex.sector), ("severidad", ex.severidad))
                                       if v is not None and getattr(r, c) is not None and v != getattr(r, c)],
               "nota": "Informativa: no modifica el reporte; la confianza declarada es subjetiva, no calibrada."}
        if ex.metodo == "reglas(fallback)":
            det["motivo_fallback"] = next((n.split(": ", 1)[1] for n in ex.notas if n.startswith("Motivo del fallback")), "")
        estado = "fallback" if ex.metodo == "reglas(fallback)" else "ok"
        return (estado, ex.metodo, f"Clasificado como '{ex.tipo_incidencia or 'sin tipo'}' ({ex.metodo}).", det)

    # ---- 4. prioridad (determinista; el reporte se evalúa «como si» estuviera confirmado, sin cambiarlo)
    def prioridad() -> _Salida:
        if mem["sector"] is None:
            return ("omitida", "reglas", "Sin sector del catálogo no se calcula prioridad.", {})
        simulado = r.model_copy(update={"estado": EstadoReporte.CONFIRMADO})
        prios = prioritization.calcular_prioridades([x for x in reportes if x.id != r.id] + [simulado], sectores)
        propia = next((p for p in prios if p.sector == mem["sector"]), None)
        if propia is None:
            return ("omitida", "reglas", "No se pudo calcular la prioridad del sector.", {})
        mem["prios"], mem["prioridad"] = prios, propia
        return ("ok", "reglas", f"Prioridad {propia.categoria} ({propia.puntaje:g} puntos).",
                {"puntaje": propia.puntaje, "categoria": propia.categoria, "razones": propia.razones,
                 "evaluado_como_confirmado": r.estado != EstadoReporte.CONFIRMADO})

    # ---- 5. recomendación (Gemini redacta con esquema estricto; texto local si falla)
    def recomendacion() -> _Salida:
        p = mem["prioridad"]
        datos = {"sector": mem["sector"], "prioridad": p.puntaje if p else None,
                 "categoria": p.categoria if p else None, "horas_sin_agua": r.horas_sin_agua,
                 "personas_afectadas": r.personas_afectadas, "posibles_duplicados": len(mem["duplicados"])}
        datos = {k: v for k, v in datos.items() if v is not None and v != 0 or k == "sector"}
        if mem["sector"] is None:  # nada que redactar con IA: texto local determinista
            mem["accion"] = ia_tareas.recomendacion_local(datos)
            return ("ok", "reglas", "Acción sugerida (sin sector: la redacta el código).", {})
        res = ia_tareas.recomendar_respuesta(datos, [s.nombre for s in sectores], [c.id for c in cisternas])
        mem["accion"] = res["texto"]
        estado = "fallback" if res["origen"] == "reglas(fallback)" else "ok"
        return (estado, res["origen"], f"Acción sugerida redactada ({res['origen']}).",
                {"motivo_fallback": res["motivo_fallback"]} if res["motivo_fallback"] else {})

    # ---- 6. asignación (determinista: planner respeta capacidad, litros disponibles y elegibilidad)
    def asignacion() -> _Salida:
        p = mem["prioridad"]
        if p is None:
            return ("omitida", "reglas", "Sin prioridad no hay propuesta de asignación.", {})
        asig, pend = planner.proponer_asignaciones(mem["prios"], cisternas)
        mias = [a for a in asig if a.sector == p.sector]
        pendiente = next((x for x in pend if x.sector == p.sector), None)
        if not mias:
            motivo = pendiente.motivo if pendiente else "No se asignaron litros a este sector."
            return ("ok", "reglas", f"Sin cisterna posible: {motivo}", {"cisterna_sugerida": None, "motivo": motivo})
        errores = planner.validar_asignaciones(mias, cisternas)
        if errores:
            raise RuntimeError("asignación inválida: " + " ".join(errores))
        elegida = max(mias, key=lambda a: (a.litros, a.cisterna_id))
        sec = cat[p.sector]
        if r.lat is not None and r.lng is not None:
            lat, lng, fuente = r.lat, r.lng, "reporte"
        elif sec.lat is not None and sec.lng is not None:
            lat, lng, fuente = sec.lat, sec.lng, "sector (aproximada)"
        else:
            lat = lng = None
            fuente = "sin coordenadas"
        lugar = r.calle or r.referencia_ubicacion
        mem["cisterna"] = elegida.cisterna_id
        mem["paradas"] = [Parada(orden=1, sector=p.sector, litros=elegida.litros, lat=lat, lng=lng,
                                 reporte_ids=[r.id],
                                 instruccion=f"Entregar agua en {p.sector}" + (f" ({lugar})" if lugar else "") + ".")]
        otras = [a.model_dump(mode="json") for a in mias if a.cisterna_id != elegida.cisterna_id]
        req = planner.litros_requeridos(p.personas_afectadas) if p.personas_afectadas else None
        return ("ok", "reglas", f"Cisterna {elegida.cisterna_id} con {elegida.litros} L para {p.sector}.",
                {"cisterna_sugerida": elegida.cisterna_id, "litros": elegida.litros, "litros_requeridos": req,
                 "coordenadas_fuente": fuente, "cisternas_con_mision_activa_excluidas": sorted(ocupadas), "otras_cisternas_necesarias": otras,
                 "pendiente": pendiente.model_dump(mode="json") if pendiente else None})

    pasos: List[Tuple[str, Callable[[], _Salida]]] = [
        ("duplicados", duplicados), ("clasificacion", clasificacion), ("prioridad", prioridad),
        ("recomendacion", recomendacion), ("asignacion", asignacion)]
    with ia_gemini.presupuesto(presupuesto_ia_s()):
        for nombre, fn in pasos:
            if detenido:
                omitida(nombre, "Flujo detenido en la validación: " + primera.resumen)
                continue
            _etapa(nombre, r.id, fn, etapas)  # un error o una etapa «omitida» no detiene las siguientes

    if not mem["accion"]:
        mem["accion"] = ("No se generó una acción sugerida: " + primera.resumen) if detenido else \
            ia_tareas.recomendacion_local({"sector": mem["sector"]})
    p = mem["prioridad"]
    return Recomendacion(reporte_id=r.id, proveedor=procedencia_global(etapas), etapas=etapas,
                         prioridad_sugerida=p.puntaje if p else None, categoria=p.categoria if p else None,
                         accion_sugerida=mem["accion"], posibles_duplicados=mem["duplicados"],
                         cisterna_sugerida=mem["cisterna"], paradas_sugeridas=mem["paradas"])


def revalidar_propuesta(cisterna_id: str, paradas: List[Parada], cisternas: List[Cisterna],
                        sectores: List[Sector]) -> List[str]:
    """Errores (lista vacía = válida) de una cisterna + paradas, editadas o no, antes de crear la misión:
    cisterna existente y disponible, sectores del catálogo, litros > 0, elegibilidad y capacidad."""
    errores: List[str] = []
    c = next((x for x in cisternas if x.id == cisterna_id), None)
    if c is None:
        return [f"La cisterna {cisterna_id} no existe."]
    if not paradas:
        return ["Indique al menos una parada."]
    nombres = {s.nombre for s in sectores}
    ordenes = [p.orden for p in paradas]
    if len(set(ordenes)) != len(ordenes):
        errores.append("Los números de orden de las paradas no pueden repetirse.")
    for p in paradas:
        if p.sector not in nombres:
            errores.append(f"El sector '{p.sector}' no está en el catálogo.")
    asign = [Asignacion(sector=p.sector, cisterna_id=cisterna_id, litros=p.litros, prioridad=0) for p in paradas]
    errores += planner.validar_asignaciones(asign, cisternas)
    return errores
