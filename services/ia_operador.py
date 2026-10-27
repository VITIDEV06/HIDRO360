"""
Ayudas de IA para el OPERADOR (solo lectura; sin base de datos ni funciones de escritura de workflow).

  - contexto_incidencia: resumen del contexto de un reporte (datos del reporte + duplicados sugeridos +
    historial del sector). Solo usa datos que existen; el texto original del ciudadano NO se envía a la IA.
  - detectar_patrones: patrones simples y DETERMINISTAS (sector con reportes recurrentes en una ventana, pico
    por hora del día). Cada patrón trae su explicación en lenguaje claro generada por código; la IA solo
    redacta un resumen opcional, validado (cifras y sectores ajenos a los datos se descartan) y con respaldo
    local visible (`origen`: "gemini" | "reglas" | "reglas(fallback)").

Este módulo recibe los datos ya leídos (los obtiene `services.workflow_ia`) y devuelve diccionarios: no
importa `services.workflow`, no escribe y no decide nada. La salida es informativa para una persona.
"""

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import config
from models.schemas import EstadoReporte, Reporte
from services import ia_gemini, ia_tareas, tracing

PRESUPUESTO_S = 12.0  # tiempo máximo total de la redacción con IA (serverless); luego, texto local
ESTADOS_ABIERTOS = (EstadoReporte.POR_REVISAR, EstadoReporte.CONFIRMADO)
ESTADOS_DESCARTADOS = (EstadoReporte.RECHAZADO, EstadoReporte.DUPLICADO)

_SISTEMA_CONTEXTO = (
    "Redactas para una persona operadora, en español claro y en máximo 4 frases, un resumen del contexto de una "
    "incidencia de falta de agua. Usa SOLO los datos del JSON recibido (reporte, duplicados_sugeridos, "
    "historial_sector). Escribe las cifras tal cual aparecen; no calcules ni inventes datos, causas, sectores, "
    "cisternas ni horas de llegada; no incluyas datos personales; la decisión final es de una persona. "
    "Devuelve únicamente un JSON {\"resumen\": \"...\"}.")
_SISTEMA_PATRONES = (
    "Redactas para una persona operadora, en español claro y en máximo 4 frases, un resumen de los patrones "
    "detectados en los reportes de falta de agua. Usa SOLO los datos del JSON recibido. Escribe las cifras tal "
    "cual aparecen; no calcules ni inventes causas, sectores, cisternas ni horas de llegada. Son indicios para "
    "revisar, no conclusiones. Devuelve únicamente un JSON {\"resumen\": \"...\"}.")


def _redactar(sistema: str, tarea: str, datos: Dict[str, Any], local: str, sectores: List[str],
              permitidos: List[str]) -> Dict[str, Optional[str]]:
    """Gemini redacta con validación de código; si no hay IA o falla, texto local. Devuelve texto/origen/motivo."""
    if config.ai_provider() != "gemini":
        return {"texto": local, "origen": "reglas", "motivo_fallback": None}
    try:
        with ia_gemini.presupuesto(PRESUPUESTO_S):
            bruto = ia_gemini.generar_json(sistema, json.dumps(datos, ensure_ascii=False, default=str), tarea=tarea)
        try:
            resumen = ia_tareas.SalidaResumen(**json.loads(bruto)).resumen
        except Exception as e:
            raise ia_gemini.ErrorIA(f"salida inválida: {type(e).__name__}") from e
        texto = ia_tareas.validar_recomendacion(resumen, datos, sectores, [], maximo=700, permitidos=permitidos,
                                              horas_pasadas_ok=True)
        return {"texto": texto, "origen": "gemini", "motivo_fallback": None}
    except Exception as e:
        motivo = f"{type(e).__name__}: {e}"[:160]
        tracing.emitir("ia", f"operador.{tarea}", "fallback", None, {"motivo": motivo})
        return {"texto": local, "origen": "reglas(fallback)", "motivo_fallback": motivo}


def _fecha(d: Optional[datetime]) -> Optional[str]:
    # dd/mm/aaaa: sin guiones, para que no se confunda con un teléfono en el validador de datos personales
    return d.strftime("%d/%m/%Y %H:%M") if d else None


# ---------------------------------------------------------------------- contexto de una incidencia
def contexto_incidencia(r: Reporte, duplicados: List[Any], reportes: List[Reporte], misiones: List[Any],
                        sectores: List[str], ahora: Optional[datetime] = None, usar_ia: bool = True) -> Dict[str, Any]:
    ahora = ahora or datetime.now()
    del_sector = [x for x in reportes if r.sector and x.sector == r.sector and x.id != r.id]
    semana = [x for x in del_sector if x.fecha_creacion >= ahora - timedelta(days=7)]
    por_estado = Counter(x.estado.value for x in del_sector)
    n_misiones = sum(1 for m in misiones if any(p.sector == r.sector for p in m.paradas)) if r.sector else 0
    datos: Dict[str, Any] = {
        "reporte": {"id": r.id, "estado": r.estado.value, "sector": r.sector, "recibido": _fecha(r.fecha_creacion),
                    "horas_sin_agua": r.horas_sin_agua, "personas_afectadas": r.personas_afectadas,
                    "severidad": r.severidad.value if r.severidad else None, "tipo_incidencia": r.tipo_incidencia,
                    "vulnerabilidades": list(r.vulnerabilidades), "datos_faltantes": list(r.datos_faltantes)},
        "duplicados_sugeridos": [{"reporte_id": d.reporte_id, "similitud": round(d.similitud, 2), "motivos": d.motivos}
                                 for d in duplicados],
        "historial_sector": {"sector": r.sector, "otros_reportes": len(del_sector), "ultimos_7_dias": len(semana),
                             "por_estado": dict(por_estado), "misiones_con_paradas_en_el_sector": n_misiones,
                             "ultimo_reporte": _fecha(max((x.fecha_creacion for x in del_sector), default=None))},
    }
    hs = datos["historial_sector"]
    partes = [f"Reporte {r.id} ({r.estado.value}) recibido el {datos['reporte']['recibido']}"
              + (f" en {r.sector}." if r.sector else ", sin sector del catálogo asignado.")]
    detalle = []
    if r.horas_sin_agua is not None:
        detalle.append(f"{r.horas_sin_agua:g} h sin agua")
    if r.personas_afectadas is not None:
        detalle.append(f"{r.personas_afectadas} personas afectadas")
    if detalle:
        partes.append("Datos: " + ", ".join(detalle) + ".")
    if r.datos_faltantes:
        partes.append("Faltan: " + ", ".join(r.datos_faltantes) + ".")
    if duplicados:
        partes.append(f"Hay {len(duplicados)} posible(s) duplicado(s) sugerido(s) (ids: "
                      + ", ".join(str(d.reporte_id) for d in duplicados) + "); ninguno está fusionado.")
    if r.sector:
        partes.append(f"En {r.sector} hay {hs['otros_reportes']} reporte(s) más, {hs['ultimos_7_dias']} en los últimos 7 días,"
                      f" y {n_misiones} misión(es) con paradas en el sector.")
    local = " ".join(partes)
    salida = {"datos": datos, "resumen": _redactar(_SISTEMA_CONTEXTO, "contexto", datos, local, sectores, [r.sector] if r.sector else [])
              if usar_ia else {"texto": local, "origen": "reglas", "motivo_fallback": None},
              "nota": "Informativo: solo usa datos registrados; no decide ni modifica nada."}
    return salida


# ---------------------------------------------------------------------- patrones deterministas
def detectar_patrones(reportes: List[Reporte], sectores: List[str], ventana_horas: int = 72, minimo_reportes: int = 3,
                      ahora: Optional[datetime] = None, usar_ia: bool = True) -> Dict[str, Any]:
    ahora = ahora or datetime.now()
    desde = ahora - timedelta(hours=ventana_horas)
    en_ventana = [x for x in reportes if x.fecha_creacion >= desde and x.estado not in ESTADOS_DESCARTADOS]
    patrones: List[Dict[str, Any]] = []

    por_sector: Dict[str, List[Reporte]] = {}
    for x in en_ventana:
        if x.sector:
            por_sector.setdefault(x.sector, []).append(x)
    for sector, lista in sorted(por_sector.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(lista) >= minimo_reportes:
            abiertos = sum(1 for x in lista if x.estado in ESTADOS_ABIERTOS)
            patrones.append({
                "tipo": "sector_recurrente", "sector": sector, "reportes": len(lista), "sin_atender": abiertos,
                "reporte_ids": sorted(x.id for x in lista), "ventana_horas": ventana_horas,
                "explicacion": f"{sector} acumula {len(lista)} reportes en las últimas {ventana_horas} horas "
                               f"({abiertos} aún sin atender). Puede indicar un problema persistente en el sector; "
                               "conviene revisarlo."})

    por_hora = Counter(x.fecha_creacion.hour for x in en_ventana)
    total = len(en_ventana)
    if total >= 2 * minimo_reportes and por_hora:
        hora, n = max(por_hora.items(), key=lambda kv: (kv[1], -kv[0]))
        if n >= minimo_reportes and n / total >= 0.4:
            patrones.append({
                "tipo": "pico_horario", "hora": hora, "reportes": n, "total_ventana": total,
                "reporte_ids": sorted(x.id for x in en_ventana if x.fecha_creacion.hour == hora),
                "ventana_horas": ventana_horas,
                "explicacion": f"{n} de {total} reportes de las últimas {ventana_horas} horas llegaron entre las "
                               f"{hora:02d}:00 y las {hora:02d}:59. Es un indicio de concentración horaria, no una causa."})

    datos = {"ventana_horas": ventana_horas, "minimo_reportes": minimo_reportes, "reportes_en_ventana": total,
             "patrones": [{k: v for k, v in p.items() if k != "reporte_ids"} for p in patrones]}
    sectores_pat = [p["sector"] for p in patrones if p["tipo"] == "sector_recurrente"]
    local = " ".join(p["explicacion"] for p in patrones) if patrones else \
        f"No se detectaron patrones en las últimas {ventana_horas} horas ({total} reportes analizados)."
    if usar_ia and patrones:
        resumen = _redactar(_SISTEMA_PATRONES, "patrones", datos, local, sectores, sectores_pat)
    else:
        resumen = {"texto": local, "origen": "reglas", "motivo_fallback": None}
    return {"parametros": {"ventana_horas": ventana_horas, "minimo_reportes": minimo_reportes},
            "generado": ahora.isoformat(timespec="seconds"), "reportes_en_ventana": total,
            "patrones": patrones, "resumen": resumen,
            "nota": "Detección determinista (conteos); la IA solo redacta el resumen. Son indicios, no conclusiones."}
