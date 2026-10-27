"""
Operaciones de negocio del flujo asistido por IA (misma puerta de negocio que services.workflow).

  analizar_reporte(reporte_id, responsable)      -> Recomendacion «propuesta» (persistida, auditada)
  decidir_recomendacion(id, accion, ...)         -> aprobar | modificar | rechazar (decisión HUMANA)
  listar_recomendaciones / obtener_recomendacion

La IA nunca crea una misión: la única vía es `decidir_recomendacion` con «aprobar» o «modificar» por un
administrador, que llama a `workflow.crear_mision(...)`. Cada operación corre en una transacción SQLite
junto con su auditoría (si crear la misión falla, la decisión se revierte). La lógica de las etapas vive
en services.orquestador; aquí solo se persiste, se autoriza y se audita.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from data import database as db
from models.schemas import EstadoRecomendacion, EstadoReporte, Parada, Recomendacion
from services import audit, geoloc, ia_operador, orquestador, tracing, workflow
from services.auth import requiere_admin
from services.workflow import ErrorDeNegocio, operacion

ACCIONES = ("aprobar", "modificar", "rechazar")
_ESTADO_POR_ACCION = {"aprobar": EstadoRecomendacion.APROBADA, "modificar": EstadoRecomendacion.MODIFICADA,
                      "rechazar": EstadoRecomendacion.RECHAZADA}


def _leer(recomendacion_id: int) -> Recomendacion:
    d = db.obtener("recomendaciones", recomendacion_id)
    if d is None:
        raise ErrorDeNegocio(f"La recomendación {recomendacion_id} no existe.")
    return Recomendacion(**d)


def _guardar(rec: Recomendacion) -> None:
    db.actualizar("recomendaciones", rec.id, rec.model_dump(mode="json"))


@requiere_admin
@operacion("analizar_reporte")
def analizar_reporte(reporte_id: int, responsable: str) -> Recomendacion:
    """Ejecuta el flujo validación → duplicados → clasificación → prioridad → recomendación → asignación y
    guarda una NUEVA recomendación «propuesta» (re-analizar no sobrescribe las anteriores). No cambia el
    reporte, no confirma nada y no crea misiones."""
    responsable = workflow._resolver_responsable(responsable)
    r = workflow._obtener_reporte(reporte_id)
    cisternas = workflow.listar_cisternas()
    ocupadas = {c.id for c in cisternas if workflow._mision_activa_de(c.id) is not None}
    rec = orquestador.analizar(r, workflow._listar_reportes(), workflow.listar_sectores(), cisternas, ocupadas)
    rid = db.insertar("recomendaciones", rec.model_dump(mode="json"))
    rec.id = rid
    _guardar(rec)
    tracing.correlacionar(f"reporte:{reporte_id}")
    audit.registrar(responsable, "analizar_reporte", "recomendacion", rid,
                    detalle={"reporte_id": reporte_id, "proveedor": rec.proveedor,
                             "etapas": [{"nombre": e.nombre, "estado": e.estado, "proveedor": e.proveedor}
                                        for e in rec.etapas],
                             "prioridad": rec.prioridad_sugerida, "cisterna_sugerida": rec.cisterna_sugerida,
                             "nota": "propuesta de IA; no crea misión sin aprobación humana"})
    return rec


def _coaccionar_paradas(paradas: List[Any]) -> List[Parada]:
    try:
        return [p if isinstance(p, Parada) else Parada(**p) for p in paradas]
    except Exception as e:
        raise ErrorDeNegocio(f"Las paradas indicadas no son válidas: {type(e).__name__}.") from e


@requiere_admin
@operacion("decidir_recomendacion")
def decidir_recomendacion(recomendacion_id: int, accion: str, responsable: str, motivo: str = "",
                          cisterna_id: Optional[str] = None, paradas: Optional[List[Any]] = None) -> Recomendacion:
    """Decisión humana, irreversible y única por recomendación.
      - rechazar: exige motivo.
      - aprobar: usa la cisterna y paradas sugeridas (revalidadas contra el estado ACTUAL de las cisternas).
      - modificar: exige motivo y acepta `cisterna_id`/`paradas` editadas (se revalidan igual).
    Aprobar/modificar crean la misión con workflow.crear_mision y guardan `mision_id`."""
    accion = (accion or "").strip().lower()
    if accion not in ACCIONES:
        raise ErrorDeNegocio(f"Acción inválida '{accion}': use aprobar, modificar o rechazar.")
    responsable = workflow._resolver_responsable(responsable)
    rec = _leer(recomendacion_id)
    if rec.estado != EstadoRecomendacion.PROPUESTA:
        raise ErrorDeNegocio(f"Transición no permitida: la recomendación {rec.id} ya está '{rec.estado.value}'.")
    motivo = (motivo or "").strip()

    if accion == "rechazar":
        if not motivo:
            raise ErrorDeNegocio("Indique el motivo del rechazo.")
    else:
        if accion == "modificar" and not motivo:
            raise ErrorDeNegocio("Indique el motivo de la modificación.")
        if workflow._obtener_reporte(rec.reporte_id).estado in (EstadoReporte.RECHAZADO, EstadoReporte.DUPLICADO):
            raise ErrorDeNegocio("El reporte ya no es atendible (rechazado o duplicado): rechace la recomendación.")
        if accion == "modificar":
            if not cisterna_id or not paradas:
                raise ErrorDeNegocio("Para modificar indique la cisterna y las paradas.")
            cisterna, lista = cisterna_id, _coaccionar_paradas(paradas)
        else:
            if cisterna_id or paradas:
                raise ErrorDeNegocio("Para cambiar la cisterna o las paradas use la acción 'modificar'.")
            if not rec.cisterna_sugerida or not rec.paradas_sugeridas:
                raise ErrorDeNegocio("La recomendación no propone cisterna: use 'modificar' o 'rechazar'.")
            cisterna, lista = rec.cisterna_sugerida, rec.paradas_sugeridas
        errores = orquestador.revalidar_propuesta(cisterna, lista, workflow.listar_cisternas(), workflow.listar_sectores())
        if errores:
            raise ErrorDeNegocio("La propuesta no es válida: " + " ".join(errores))
        mision = workflow.crear_mision(cisterna, lista, responsable, plan_id=None, recomendacion_id=rec.id,
                                       motivo=motivo or f"Aprobada la recomendación {rec.id}")
        rec.mision_id = mision.id
        if accion == "modificar":
            rec.cisterna_sugerida, rec.paradas_sugeridas = cisterna, lista

    rec.estado = _ESTADO_POR_ACCION[accion]
    rec.decidida_por = responsable
    rec.motivo_decision = motivo
    _guardar(rec)
    audit.registrar(responsable, f"{accion}_recomendacion", "recomendacion", rec.id, motivo=motivo,
                    detalle={"reporte_id": rec.reporte_id, "proveedor_ia": rec.proveedor, "mision_id": rec.mision_id,
                             "cisterna": rec.cisterna_sugerida,
                             "paradas": [p.model_dump(mode="json") for p in rec.paradas_sugeridas]})
    return rec


@requiere_admin
def listar_recomendaciones(estado: Optional[str] = None, reporte_id: Optional[int] = None) -> List[Recomendacion]:
    if estado is not None:
        try:
            estado = EstadoRecomendacion(estado).value
        except ValueError:
            raise ErrorDeNegocio(f"Estado inválido '{estado}'.")
    salida = [Recomendacion(**d) for d in db.listar("recomendaciones")]
    return [x for x in salida if (estado is None or x.estado.value == estado)
            and (reporte_id is None or x.reporte_id == reporte_id)]


@requiere_admin
def obtener_recomendacion(recomendacion_id: int) -> Recomendacion:
    return _leer(recomendacion_id)


# ----------------------------------------------------------------- ayudas de lectura para el operador
@requiere_admin
def resumen_contexto_reporte(reporte_id: int, usar_ia: bool = True) -> Dict[str, Any]:
    """Contexto de una incidencia: datos del reporte + duplicados sugeridos + historial del sector, con un
    resumen en lenguaje claro (`resumen.origen`: gemini | reglas | reglas(fallback)). Solo lectura: no cambia
    nada ni envía el texto del ciudadano a la IA. Devuelve {"datos", "resumen": {"texto","origen",
    "motivo_fallback"}, "nota"}."""
    r = workflow._obtener_reporte(reporte_id)
    reportes = workflow._listar_reportes()
    duplicados = geoloc.sugerir_duplicados(r, [x for x in reportes if x.id != r.id])
    return ia_operador.contexto_incidencia(r, duplicados, reportes, workflow.listar_misiones(),
                                           [s.nombre for s in workflow.listar_sectores()], usar_ia=usar_ia)


@requiere_admin
def detectar_patrones(ventana_horas: int = 72, minimo_reportes: int = 3, usar_ia: bool = True) -> Dict[str, Any]:
    """Patrones deterministas (sectores con reportes recurrentes y pico por hora) en la ventana indicada.
    `ventana_horas` 1-720, `minimo_reportes` 2-50. Devuelve {"parametros","generado","reportes_en_ventana",
    "patrones":[{"tipo","explicacion",...}],"resumen":{"texto","origen","motivo_fallback"},"nota"}."""
    if not 1 <= int(ventana_horas) <= 720:
        raise ErrorDeNegocio("La ventana debe estar entre 1 y 720 horas.")
    if not 2 <= int(minimo_reportes) <= 50:
        raise ErrorDeNegocio("El mínimo de reportes debe estar entre 2 y 50.")
    return ia_operador.detectar_patrones(workflow._listar_reportes(), [s.nombre for s in workflow.listar_sectores()],
                                         int(ventana_horas), int(minimo_reportes), usar_ia=usar_ia)
