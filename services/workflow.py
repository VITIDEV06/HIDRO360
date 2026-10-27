"""
Flujo de trabajo completo (sin interfaz). La interfaz Flet y la API FastAPI llaman SOLO a estas
funciones; las reglas viven aquí, no en la interfaz.

reporte -> revisión -> sector/duplicados -> prioridad -> plan -> decisión -> auditoría

Consistencia: cada operación que cambia datos corre en UNA transacción SQLite junto con su
registro de auditoría (si algo falla, se revierte todo). Las trazas JSONL son aparte y no
forman parte de la transacción.
"""

import functools
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import config
from data import database as db
from data.datos_ficticios import CISTERNAS_FICTICIAS, REPORTES_FICTICIOS, SECTORES_FICTICIOS
from models.schemas import (AccionDecision, Asignacion, CandidatoDuplicado, Cisterna,
                            ConfirmacionDispositivo, Decision, Dispositivo, EstadoActual, EstadoCisterna,
                            EstadoMision, EstadoParada, EstadoPlan, EstadoReporte, EventoMision, Mision,
                            Parada, PrioridadSector, PropuestaPlan, RegistroEvento, Reporte, Sector,
                            SectorPendiente, Severidad)
from services import (audios as audios_srv, audit, auth, communication, fotos as fotos_srv, geoloc, ia_tareas, ingest,
                      limites, moderacion, planner, prioritization, tracing)
from services.auth import requiere_admin
from services.textutil import normalizar


class ErrorDeNegocio(ValueError):
    """Error esperado (validación); la interfaz/API lo muestra al usuario."""


class ErrorLimite(limites.ErrorLimite, ErrorDeNegocio):
    """Demasiadas solicitudes desde el mismo origen (→ 429 en la API)."""


_SIN_CAMBIO = object()

# Máquina de estados de reportes: el BACKEND impide transiciones inválidas aunque se llame a la API.
TRANSICIONES_REPORTE = {
    EstadoReporte.POR_REVISAR: {EstadoReporte.CONFIRMADO, EstadoReporte.RECHAZADO, EstadoReporte.DUPLICADO},
    EstadoReporte.CONFIRMADO: {EstadoReporte.CONFIRMADO, EstadoReporte.RECHAZADO, EstadoReporte.DUPLICADO,
                               EstadoReporte.EN_PLAN},
    EstadoReporte.DUPLICADO: set(),
    EstadoReporte.RECHAZADO: set(),
    EstadoReporte.EN_PLAN: set(),
}
TRANSICIONES_PLAN = {
    EstadoPlan.PROPUESTO: {EstadoPlan.APROBADO, EstadoPlan.RECHAZADO},
    EstadoPlan.APROBADO: set(),
    EstadoPlan.RECHAZADO: set(),
}


TRANSICIONES_MISION = {
    EstadoMision.ASIGNADA: {EstadoMision.EN_CURSO, EstadoMision.CANCELADA},
    EstadoMision.EN_CURSO: {EstadoMision.COMPLETADA, EstadoMision.CANCELADA},
    EstadoMision.COMPLETADA: set(),
    EstadoMision.CANCELADA: set(),
}
# Una parada puede registrarse como «entregada» sin «llegada» previa (la terminal pudo perder ese evento).
TRANSICIONES_PARADA = {
    EstadoParada.PENDIENTE: {EstadoParada.LLEGADA, EstadoParada.ENTREGADA, EstadoParada.INCIDENCIA},
    EstadoParada.LLEGADA: {EstadoParada.ENTREGADA, EstadoParada.INCIDENCIA},
    EstadoParada.ENTREGADA: set(),
    EstadoParada.INCIDENCIA: set(),
}
_ACTIVAS = (EstadoMision.ASIGNADA, EstadoMision.EN_CURSO)
_PARADA_TERMINAL = (EstadoParada.ENTREGADA, EstadoParada.INCIDENCIA)
_TIPO_A_PARADA = {"llegada": EstadoParada.LLEGADA, "entrega": EstadoParada.ENTREGADA,
                  "incidencia": EstadoParada.INCIDENCIA}

# Rectángulo razonable de Panamá Oeste (lat_min, lat_max, lng_min, lng_max); sobrescribible con config.COORD_LIMITES.
_COORD_LIMITES_DEFECTO = (8.3, 9.3, -80.3, -79.2)


def _limites_coord() -> Tuple[float, float, float, float]:
    return tuple(getattr(config, "COORD_LIMITES", _COORD_LIMITES_DEFECTO))


def _validar_coord(lat: Optional[float], lng: Optional[float], cual: str = "El reporte") -> None:
    if lat is None and lng is None:
        return
    if lat is None or lng is None:
        raise ErrorDeNegocio(f"{cual}: indique latitud y longitud juntas.")
    la0, la1, ln0, ln1 = _limites_coord()
    if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))) or lat != lat or lng != lng:
        raise ErrorDeNegocio(f"{cual}: coordenadas inválidas.")
    if not (la0 <= lat <= la1 and ln0 <= lng <= ln1):
        raise ErrorDeNegocio(f"{cual}: las coordenadas están fuera de Panamá Oeste "
                             f"(latitud {la0} a {la1}, longitud {ln0} a {ln1}).")


def _mover_reporte(r: Reporte, nuevo: EstadoReporte) -> None:
    if nuevo not in TRANSICIONES_REPORTE[r.estado]:
        raise ErrorDeNegocio(f"Transición no permitida para el reporte {r.id}: '{r.estado.value}' → '{nuevo.value}'.")
    r.estado = nuevo


def _mover_plan(p: PropuestaPlan, nuevo: EstadoPlan) -> None:
    if nuevo not in TRANSICIONES_PLAN[p.estado]:
        raise ErrorDeNegocio(f"Transición no permitida para el plan {p.id}: '{p.estado.value}' → '{nuevo.value}'.")
    p.estado = nuevo


def _exigir_responsable(responsable: str) -> str:
    if not responsable or not responsable.strip():
        raise ErrorDeNegocio("Indique el responsable.")
    return responsable.strip()


def _resolver_responsable(declarado: Optional[str]) -> str:
    """Quién queda en la auditoría. Con sesión de usuario prevalece SIEMPRE el usuario autenticado (el nombre
    declarado se ignora: no se puede firmar como otra persona). Con el token de servicio de la API se respeta el
    nombre declarado, marcado como `servicio:<nombre>`. Sin actor (solo portal público) no hay identidad."""
    a = auth.actor()
    if a is None:
        return _exigir_responsable(declarado)
    if a.origen == "servicio":
        return f"servicio:{_exigir_responsable(declarado)}"
    return a.usuario


# Límites de tasa PERSISTIDOS en la base (services/limites.py): valen entre instancias serverless.
_LIMITE_REPORTES = (10, 60)    # 10 reportes por minuto y origen
_LIMITE_CONSULTAS = (20, 60)   # 20 consultas de código por minuto y origen (frena la adivinación de códigos)


def _limitar(clave: str, limite) -> None:
    maximo, ventana = limite
    try:
        limites.limitar(clave, maximo, ventana)
    except limites.ErrorLimite as e:
        raise ErrorLimite(str(e))


def reiniciar_limites() -> None:
    """Pruebas: borra los contadores de tasa de la base activa."""
    db.limpiar_limites(0)


def operacion(nombre: str):
    """Transacción SQLite + ejecución trazada para una operación de negocio."""
    def deco(fn):
        @functools.wraps(fn)
        def envoltura(*a, **k):
            with tracing.ejecucion():
                with tracing.traza("workflow", nombre, {"args": [type(x).__name__ for x in a], "kwargs": list(k)}) as t:
                    with db.transaccion():
                        resultado = fn(*a, **k)
                    t["output"] = {"tipo": type(resultado).__name__,
                                   "id": getattr(resultado, "id", None)}
                    return resultado
        return envoltura
    return deco


# ---------------------------------------------------------------- catálogos
# Decisión de seguridad: listar_sectores/listar_cisternas NO exigen rol en la capa de negocio porque son el
# catálogo FICTICIO de referencia que usan por dentro casi todas las operaciones (y la interfaz para sus
# listas desplegables). La API NO los expone sin rol de administrador (/cisternas exige admin) y el portal
# público tampoco. Si el catálogo llegara a contener datos reales sensibles, deben pasar a @requiere_admin.
def listar_sectores() -> List[Sector]:
    return [Sector(**d) for d in db.listar("sectores")]


def listar_cisternas() -> List[Cisterna]:
    return [Cisterna(**d) for d in db.listar("cisternas")]


@requiere_admin
@operacion("cargar_datos_demo")
def cargar_datos_demo(responsable: str = "sistema") -> Dict[str, int]:
    """Reinicia la base con datos FICTICIOS y procesa los reportes de ejemplo (quedan 'por_revisar').
    No borra las cuentas de usuario (tabla `usuarios`)."""
    responsable = _resolver_responsable(responsable)
    db.vaciar()
    for s in SECTORES_FICTICIOS:
        sec = Sector(**s)
        db.insertar("sectores", sec.model_dump(mode="json"), clave=sec.nombre)
    for c in CISTERNAS_FICTICIAS:
        cis = Cisterna(**c)
        db.insertar("cisternas", cis.model_dump(mode="json"), clave=cis.id)
    token_local = ingest.solo_local.set(True)      # la carga de ejemplo nunca gasta cuota de IA
    try:
        for texto in REPORTES_FICTICIOS:
            _crear_reporte(texto, responsable=responsable, canal="demo", ficticio=True, auditar=False)
    finally:
        ingest.solo_local.reset(token_local)
    audit.registrar(responsable, "cargar_datos_demo", "sistema", "demo",
                    detalle={"reportes": len(REPORTES_FICTICIOS), "cisternas": len(CISTERNAS_FICTICIAS),
                             "sectores": len(SECTORES_FICTICIOS), "ficticio": True})
    return {"reportes": len(REPORTES_FICTICIOS), "cisternas": len(CISTERNAS_FICTICIAS),
            "sectores": len(SECTORES_FICTICIOS)}


# ----------------------------------------------------------------- reportes
def _listar_reportes() -> List[Reporte]:
    return [Reporte(**d) for d in db.listar("reportes")]


@requiere_admin
def listar_reportes() -> List[Reporte]:
    return _listar_reportes()


def _obtener_reporte(reporte_id: int) -> Reporte:
    d = db.obtener("reportes", reporte_id)
    if d is None:
        raise ErrorDeNegocio(f"El reporte {reporte_id} no existe.")
    return Reporte(**d)


@requiere_admin
def obtener_reporte(reporte_id: int) -> Reporte:
    return _obtener_reporte(reporte_id)


def _guardar_reporte(r: Reporte) -> None:
    db.actualizar("reportes", r.id, r.model_dump(mode="json"))


_ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin I, O, 0, 1 (ambiguos)
_RE_CODIGO = re.compile(r"HID-[" + _ALFABETO_CODIGO + r"]{8}")


def _nuevo_codigo(usados: set) -> str:
    while True:
        c = "HID-" + "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(8))
        if c not in usados:
            return c


def crear_reporte(texto: str, responsable: str = "anónimo", ficticio: bool = False, auditar: bool = True,
                  canal: str = "manual", clave_idempotencia: Optional[str] = None,
                  origen: Optional[str] = None, lat: Optional[float] = None, lng: Optional[float] = None,
                  fotos: Optional[List[Dict[str, Any]]] = None,
                  audios: Optional[List[Dict[str, Any]]] = None) -> Reporte:
    """Ingesta. Es la única operación de escritura abierta al ciudadano anónimo (portal público): no exige rol.
    `origen` (p. ej. la IP del portal o el chat de Telegram) activa el límite de tasa. Sin actor el responsable
    queda como «ciudadano». Opcionales: `lat`/`lng` (ubicación elegida o compartida, dentro de Panamá Oeste),
    `fotos` ([{"nombre":.., "base64":..}], máx. config.MAX_FOTOS_POR_REPORTE) y `audios`
    ([{"nombre"|"bytes".., "transcripcion":.., "metodo_transcripcion":.., "duracion_s":..}], máx.
    config.MAX_AUDIOS_POR_REPORTE). Fotos y audios se validan por firma binaria."""
    if origen:      # fuera de la transacción: el evento de límite se conserva aunque la operación se rechace
        _limitar("reporte:" + origen, _LIMITE_REPORTES)
    return _crear_reporte_tx(texto, responsable, ficticio, auditar, canal, clave_idempotencia, lat, lng, fotos, audios)


@operacion("crear_reporte")
def _crear_reporte_tx(*a, **k) -> Reporte:
    return _crear_reporte(*a, **k)


def _crear_reporte(texto: str, responsable: str = "anónimo", ficticio: bool = False, auditar: bool = True,
                   canal: str = "manual", clave_idempotencia: Optional[str] = None,
                   lat: Optional[float] = None, lng: Optional[float] = None,
                   fotos: Optional[List[Dict[str, Any]]] = None,
                   audios: Optional[List[Dict[str, Any]]] = None) -> Reporte:
    """Guarda el texto original (inalterable) y los campos extraídos, sin confirmar.
    Idempotencia: la misma `clave_idempotencia`, o el mismo texto reciente dentro de
    config.IDEMPOTENCIA_SEGUNDOS, devuelve el reporte ya creado en vez de duplicarlo."""
    texto = (texto or "").strip()
    if not texto:
        raise ErrorDeNegocio("El texto del reporte no puede estar vacío.")
    if len(texto) > 4000:
        raise ErrorDeNegocio("El texto del reporte excede 4000 caracteres.")
    _validar_coord(lat, lng)
    try:
        validadas = fotos_srv.validar(fotos)
    except fotos_srv.ErrorFoto as e:
        raise ErrorDeNegocio(str(e))
    try:
        validadas_audio = audios_srv.validar(audios)
    except audios_srv.ErrorAudio as e:
        raise ErrorDeNegocio(str(e))
    existentes = _listar_reportes()
    if clave_idempotencia:
        for r in existentes:
            if r.clave_idempotencia == clave_idempotencia:
                tracing.emitir("workflow", "crear_reporte_idempotente", "ok", {"reporte_id": r.id})
                return r
    elif not ficticio:
        huella = normalizar(texto)
        ahora = datetime.now()
        for r in reversed(existentes):
            if (ahora - r.fecha_creacion).total_seconds() > config.IDEMPOTENCIA_SEGUNDOS:
                break
            if normalizar(r.mensaje_original) == huella and not r.ficticio:
                tracing.emitir("workflow", "crear_reporte_idempotente", "ok", {"reporte_id": r.id, "motivo": "mismo texto reciente"})
                return r
    ex = ingest.interpretar(texto, listar_sectores())  # catálogo ficticio, ver nota de catálogos
    senales = moderacion.evaluar(texto, ex, _poblacion_sector(ex.sector))
    datos = {
        "mensaje_original": texto, "fecha_creacion": datetime.now().isoformat(timespec="seconds"),
        "estado": EstadoReporte.POR_REVISAR.value, "sector": ex.sector, "calle": ex.calle,
        "horas_sin_agua": ex.horas_sin_agua, "personas_afectadas": ex.personas_afectadas,
        "severidad": ex.severidad.value if ex.severidad else None,
        "datos_faltantes": ex.datos_faltantes, "datos_inciertos": ex.datos_inciertos,
        "notas": ex.notas, "campos_corregidos": [], "ficticio": ficticio, "id": 0, "canal": canal,
        "referencia_ubicacion": ex.referencia_ubicacion, "tipo_incidencia": ex.tipo_incidencia,
        "vulnerabilidades": ex.vulnerabilidades, "servicio_esencial": ex.servicio_esencial,
        "evidencia": ex.evidencia, "confianza_declarada": ex.confianza_declarada,
        "interprete": ex.metodo, "sector_candidatos": ex.sector_candidatos,
        "clave_idempotencia": clave_idempotencia,
        "codigo_seguimiento": _nuevo_codigo({r.codigo_seguimiento for r in existentes if r.codigo_seguimiento}),
        "lat": lat, "lng": lng, "ubicacion_fuente": "mapa" if lat is not None else None, "foto_ids": [],
        "audio_ids": [], "mensajes_adicionales": [], "senales_revision": senales,
    }
    escritas: List[Tuple[str, str]] = []
    audios_escritos: List[Tuple[str, str]] = []
    try:
        rid = db.insertar("reportes", datos)
        tracing.correlacionar(f"reporte:{rid}")
        for contenido, mime in validadas:
            try:
                f = fotos_srv.guardar(contenido, mime, rid)
            except fotos_srv.ErrorFoto as e:       # el reporte no queda a medias: se revierte todo
                raise ErrorDeNegocio(str(e))
            escritas.append((f.id, f.tipo_mime))
            datos["foto_ids"].append(f.id)
        for a in validadas_audio:
            try:
                au = audios_srv.guardar(a["datos"], a["mime"], rid, duracion_s=a["duracion_s"],
                                        transcripcion=a["transcripcion"], metodo_transcripcion=a["metodo_transcripcion"])
            except audios_srv.ErrorAudio as e:
                raise ErrorDeNegocio(str(e))
            audios_escritos.append((au.id, au.tipo_mime))
            datos["audio_ids"].append(au.id)
        if escritas or audios_escritos:
            db.actualizar("reportes", rid, {**datos, "id": rid})
        rep = _obtener_reporte(rid)
        if auditar:
            quien = "ciudadano" if auth.actor() is None else _resolver_responsable(responsable)
            audit.registrar(quien, "crear_reporte", "reporte", rid,
                            detalle={"extraccion": ex.model_dump(mode="json"), "canal": canal,
                                     "con_ubicacion": lat is not None, "n_fotos": len(escritas),
                                     "n_audios": len(audios_escritos), "senales_revision": senales})
    except BaseException:
        for fid, mime in escritas:      # la transacción se revierte: no dejar archivos huérfanos
            fotos_srv.borrar_archivo(fid, mime)
        for aid, mime in audios_escritos:
            audios_srv.borrar_archivo(aid, mime)
        raise
    return rep


def _poblacion_sector(nombre: Optional[str]) -> Optional[int]:
    """Población estimada del sector reconocido (para comparar cifras del reporte). None si no se conoce."""
    if not nombre:
        return None
    for s in listar_sectores():
        if s.nombre == nombre:
            return s.poblacion_estimada
    return None


def _recalcular_faltantes(r: Reporte) -> None:
    campos = ("sector", "horas_sin_agua", "personas_afectadas", "severidad")
    r.datos_faltantes = [c for c in campos if getattr(r, c) is None]
    r.datos_inciertos = [c for c in r.datos_inciertos
                         if c not in r.campos_corregidos and c not in r.datos_faltantes]


@requiere_admin
@operacion("confirmar_reporte")
def corregir_y_confirmar(reporte_id: int, responsable: str, sector: Optional[str],
                         horas_sin_agua: Optional[float], personas_afectadas: Optional[int],
                         severidad: Optional[str], calle: Optional[str],
                         servicio_esencial: Any = _SIN_CAMBIO, vulnerabilidades: Any = _SIN_CAMBIO,
                         tipo_incidencia: Any = _SIN_CAMBIO, referencia_ubicacion: Any = _SIN_CAMBIO) -> Reporte:
    """Una persona revisa/corrige lo extraído y confirma. Requiere sector válido del catálogo.
    El mensaje original no se modifica nunca."""
    responsable = _resolver_responsable(responsable)
    r = _obtener_reporte(reporte_id)
    _mover_reporte(r, EstadoReporte.CONFIRMADO)
    sec = geoloc.resolver_sector(sector, listar_sectores())
    if sec is None:
        raise ErrorDeNegocio("Elija un sector válido del catálogo para confirmar la ubicación.")
    if horas_sin_agua is not None and not (0 <= horas_sin_agua <= 24 * 365):
        raise ErrorDeNegocio("Las horas sin agua deben estar entre 0 y 8760.")
    if personas_afectadas is not None and not (0 <= personas_afectadas <= 100000):
        raise ErrorDeNegocio("Las personas afectadas deben estar entre 0 y 100000.")
    try:
        sev = Severidad(severidad) if severidad else None
    except ValueError:
        raise ErrorDeNegocio(f"Severidad inválida: {severidad}")

    nuevos: Dict[str, Any] = {"sector": sec.nombre, "horas_sin_agua": horas_sin_agua,
                              "personas_afectadas": personas_afectadas, "severidad": sev, "calle": calle or None}
    if servicio_esencial is not _SIN_CAMBIO:
        nuevos["servicio_esencial"] = servicio_esencial or None
    if vulnerabilidades is not _SIN_CAMBIO:
        nuevos["vulnerabilidades"] = sorted(set(vulnerabilidades or []))
    if tipo_incidencia is not _SIN_CAMBIO:
        if tipo_incidencia and tipo_incidencia not in ingest.TIPOS_INCIDENCIA:
            raise ErrorDeNegocio(f"Tipo de incidencia inválido: {tipo_incidencia}")
        nuevos["tipo_incidencia"] = tipo_incidencia or None
    if referencia_ubicacion is not _SIN_CAMBIO:
        nuevos["referencia_ubicacion"] = referencia_ubicacion or None
    cambios = {}
    for campo, nuevo in nuevos.items():
        anterior = getattr(r, campo)
        if anterior != nuevo:
            cambios[campo] = {"antes": getattr(anterior, "value", anterior), "despues": getattr(nuevo, "value", nuevo)}
            setattr(r, campo, nuevo)
            if campo not in r.campos_corregidos:
                r.campos_corregidos.append(campo)
    _recalcular_faltantes(r)
    r.confirmado_por = responsable
    _guardar_reporte(r)
    audit.registrar(responsable, "confirmar_reporte", "reporte", r.id, detalle={"cambios": cambios})
    return r


@requiere_admin
@operacion("rechazar_reporte")
def rechazar_reporte(reporte_id: int, responsable: str, motivo: str) -> Reporte:
    responsable = _resolver_responsable(responsable)
    if not (motivo or "").strip():
        raise ErrorDeNegocio("Indique el motivo del rechazo.")
    r = _obtener_reporte(reporte_id)
    _mover_reporte(r, EstadoReporte.RECHAZADO)
    r.motivo_rechazo = motivo.strip()
    _guardar_reporte(r)
    audit.registrar(responsable, "rechazar_reporte", "reporte", r.id, motivo=motivo)
    return r


# ---------------------------------------------- complemento ciudadano (p. ej. webhook de Telegram)
@operacion("complementar_reporte")
def complementar_reporte(reporte_id: int, responsable: str = "anónimo", *, descripcion: Optional[str] = None,
                         lat: Optional[float] = None, lng: Optional[float] = None,
                         referencia_ubicacion: Optional[str] = None, ubicacion_fuente: str = "telegram",
                         fotos: Optional[List[Dict[str, Any]]] = None,
                         audios: Optional[List[Dict[str, Any]]] = None,
                         senales: Optional[List[str]] = None) -> Reporte:
    """Añade información que el ciudadano aporta DESPUÉS de crear el reporte (p. ej. el chatbot de Telegram
    pregunta la ubicación y el ciudadano responde). El texto original (`mensaje_original`) NUNCA se modifica:
    la aclaración se guarda aparte en `mensajes_adicionales`. Solo opera mientras el reporte está `por_revisar`.

    No exige rol administrativo: es una continuación del reporte ciudadano (misma naturaleza que `crear_reporte`).
    La única puerta que la invoca es el webhook de Telegram, protegido por su secreto; no se expone en la API pública
    ni en la administración. Coordenadas: solo se fijan si el reporte aún no tenía (`lat`/`lng` vacíos)."""
    r = _obtener_reporte(reporte_id)
    if r.estado != EstadoReporte.POR_REVISAR:
        raise ErrorDeNegocio(f"El reporte {reporte_id} ya está '{r.estado.value}': no admite más aclaraciones.")
    cambios: Dict[str, Any] = {}

    if descripcion is not None:
        d = (descripcion or "").strip()
        if d:
            if len(d) > 4000:
                raise ErrorDeNegocio("La aclaración excede 4000 caracteres.")
            r.mensajes_adicionales = list(r.mensajes_adicionales) + [d]
            cambios["aclaracion"] = {"longitud": len(d)}

    if lat is not None or lng is not None:
        _validar_coord(lat, lng)
        if r.lat is None and r.lng is None:
            r.lat, r.lng, r.ubicacion_fuente = lat, lng, ubicacion_fuente
            cambios["ubicacion"] = {"antes": None, "despues": {"lat": lat, "lng": lng, "fuente": ubicacion_fuente}}
        # Si ya tenía coordenadas, no se pisan (una ubicación ya confirmada no se sobrescribe por otra aproximada).

    if referencia_ubicacion:
        if not r.referencia_ubicacion:
            r.referencia_ubicacion = referencia_ubicacion.strip()[:300]
            cambios["referencia_ubicacion"] = r.referencia_ubicacion

    try:
        validadas = fotos_srv.validar(fotos)
        validadas_audio = audios_srv.validar(audios)
    except (fotos_srv.ErrorFoto, audios_srv.ErrorAudio) as e:
        raise ErrorDeNegocio(str(e))

    escritas: List[Tuple[str, str]] = []
    audios_escritos: List[Tuple[str, str]] = []
    try:
        for contenido, mime in validadas:
            f = fotos_srv.guardar(contenido, mime, r.id)
            escritas.append((f.id, f.tipo_mime))
            r.foto_ids = list(r.foto_ids) + [f.id]
        for a in validadas_audio:
            au = audios_srv.guardar(a["datos"], a["mime"], r.id, duracion_s=a["duracion_s"],
                                    transcripcion=a["transcripcion"], metodo_transcripcion=a["metodo_transcripcion"])
            audios_escritos.append((au.id, au.tipo_mime))
            r.audio_ids = list(r.audio_ids) + [au.id]
    except (fotos_srv.ErrorFoto, audios_srv.ErrorAudio) as e:
        for fid, mime in escritas:
            fotos_srv.borrar_archivo(fid, mime)
        for aid, mime in audios_escritos:
            audios_srv.borrar_archivo(aid, mime)
        raise ErrorDeNegocio(str(e))

    if senales:
        nuevas = [str(s) for s in senales if str(s)]
        r.senales_revision = sorted(set(r.senales_revision) | set(nuevas))
        if nuevas:
            cambios["senales_revision"] = nuevas

    _guardar_reporte(r)
    quien = "ciudadano" if auth.actor() is None else _resolver_responsable(responsable)
    audit.registrar(quien, "complementar_reporte", "reporte", r.id,
                    detalle={**cambios, "n_fotos": len(escritas), "n_audios": len(audios_escritos)})
    return r


# --------------------------------------------------------------- duplicados
@requiere_admin
def sugerir_duplicados(reporte_id: int) -> List[CandidatoDuplicado]:
    r = _obtener_reporte(reporte_id)
    with tracing.traza("geolocalizacion_dedup", "sugerir_duplicados", {"reporte_id": reporte_id},
                       correlation_id=f"reporte:{reporte_id}") as t:
        c = geoloc.sugerir_duplicados(r, _listar_reportes())
        t["output"] = {"candidatos": [(x.reporte_id, x.similitud) for x in c]}
    return c


@requiere_admin
@operacion("resolver_duplicado")
def resolver_duplicado(reporte_id: int, otro_id: int, es_duplicado: bool,
                       responsable: str, motivo: str = "") -> Reporte:
    """La persona decide. Si es duplicado, `reporte_id` queda ligado a `otro_id` y sale del cálculo;
    nada se fusiona ni se borra (ambos mensajes originales se conservan)."""
    responsable = _resolver_responsable(responsable)
    if reporte_id == otro_id:
        raise ErrorDeNegocio("Un reporte no puede ser duplicado de sí mismo.")
    r = _obtener_reporte(reporte_id)
    otro = _obtener_reporte(otro_id)
    if es_duplicado:
        if otro.estado in (EstadoReporte.DUPLICADO, EstadoReporte.RECHAZADO):
            raise ErrorDeNegocio(f"El reporte {otro_id} está '{otro.estado.value}': no puede ser el original.")
        _mover_reporte(r, EstadoReporte.DUPLICADO)
        r.duplicado_de = otro_id
        _guardar_reporte(r)
    db.insertar("decisiones_duplicado", {"reporte_id": reporte_id, "otro_id": otro_id,
                                         "es_duplicado": es_duplicado, "responsable": responsable,
                                         "fecha": datetime.now().isoformat(timespec="seconds")})
    audit.registrar(responsable, "duplicado_si" if es_duplicado else "duplicado_no", "reporte", reporte_id,
                    motivo=motivo, detalle={"otro_reporte": otro_id})
    return r


@requiere_admin
def decisiones_duplicado() -> List[Dict[str, Any]]:
    return db.listar("decisiones_duplicado")


# ---------------------------------------------------------------- prioridad
def _calcular_prioridades() -> List[PrioridadSector]:
    with tracing.traza("priorizacion", "calcular_prioridades") as t:
        p = prioritization.calcular_prioridades(_listar_reportes(), listar_sectores())
        t["output"] = [(x.sector, x.puntaje) for x in p[:8]]
    return p


@requiere_admin
def calcular_prioridades() -> List[PrioridadSector]:
    return _calcular_prioridades()


@requiere_admin
def explicar_prioridad_ia(sector: str) -> Dict[str, str]:
    """Resumen explicable de un puntaje ya calculado (Gemini redacta; el código valida). Informativo."""
    p = next((x for x in _calcular_prioridades() if x.sector == sector), None)
    if p is None:
        raise ErrorDeNegocio(f"El sector {sector} no tiene prioridad calculada.")
    return ia_tareas.explicar_prioridad(p)


@requiere_admin
def redactar_borrador_ia(plan_id: int, sector: str) -> Dict[str, str]:
    """Borrador de comunicación para un sector de un plan. NO se envía ni se guarda: lo revisa una persona."""
    plan = _obtener_plan(plan_id)
    items = [a for a in plan.asignaciones if a.sector == sector]
    if not items:
        raise ErrorDeNegocio(f"El plan {plan_id} no asigna cisternas al sector {sector}.")
    return ia_tareas.borrador_comunicacion(sector, sum(a.litros for a in items), sorted({a.cisterna_id for a in items}))


# ----------------------------------------------------------- visibilidad
ESTADOS_VISIBILIDAD = {
    "con_reportes_confirmados": "Hay reportes confirmados con datos clave: se prioriza.",
    "con_reportes_por_revisar": "Hay reportes sin confirmar: revisar antes de priorizar.",
    "sin_informacion_suficiente": "Hay reportes confirmados pero sin horas o personas: necesidad no cuantificada.",
    "sin_reportes_recientes": "Sin reportes recientes. NO significa que no haya necesidad.",
    "necesidad_no_evaluada": "Sin reportes ni antecedentes registrados: necesidad no evaluada.",
}


@requiere_admin
def visibilidad_sectores(ahora: Optional[datetime] = None) -> List[Dict[str, Any]]:
    return _visibilidad_sectores(ahora)


def _visibilidad_sectores(ahora: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Distingue 'sin reportes' de 'sin necesidad'. Nunca asigna prioridad crítica ni cero por ausencia de reportes."""
    ahora = ahora or datetime.now()
    limite = ahora - timedelta(hours=config.VENTANA_REPORTES_RECIENTES_HORAS)
    reps = _listar_reportes()
    salida = []
    for s in listar_sectores():
        propios = [r for r in reps if r.sector == s.nombre]
        confirmados = [r for r in propios if r.estado in (EstadoReporte.CONFIRMADO, EstadoReporte.EN_PLAN)]
        pendientes = [r for r in propios if r.estado == EstadoReporte.POR_REVISAR]
        recientes = [r for r in propios if r.fecha_creacion >= limite and r.estado != EstadoReporte.RECHAZADO]
        if confirmados:
            completos = any(r.horas_sin_agua is not None and r.personas_afectadas is not None for r in confirmados)
            estado = "con_reportes_confirmados" if completos else "sin_informacion_suficiente"
        elif pendientes:
            estado = "con_reportes_por_revisar"
        elif recientes:
            estado = "sin_informacion_suficiente"
        elif s.antecedentes_interrupciones is None:
            estado = "necesidad_no_evaluada"
        else:
            estado = "sin_reportes_recientes"
        verificar = estado in ("sin_reportes_recientes", "necesidad_no_evaluada") and (
            (s.antecedentes_interrupciones or 0) > 0 or s.vulnerabilidad.value in ("alta", "crítica"))
        salida.append({
            "sector": s.nombre, "estado": estado, "descripcion": ESTADOS_VISIBILIDAD[estado],
            "reportes_confirmados": len(confirmados), "reportes_por_revisar": len(pendientes),
            "vulnerabilidad": s.vulnerabilidad.value, "poblacion": s.poblacion_estimada,
            "antecedentes_interrupciones": s.antecedentes_interrupciones,
            "requiere_verificacion_proactiva": verificar,
            "fuente_datos": s.fuente_datos, "fecha_datos": s.fecha_datos,
        })
    return salida


# --------------------------------------------------------------------- plan
def _listar_planes() -> List[PropuestaPlan]:
    return [PropuestaPlan(**d) for d in db.listar("planes")]


@requiere_admin
def listar_planes() -> List[PropuestaPlan]:
    return _listar_planes()


def _obtener_plan(plan_id: int) -> PropuestaPlan:
    d = db.obtener("planes", plan_id)
    if d is None:
        raise ErrorDeNegocio(f"El plan {plan_id} no existe.")
    return PropuestaPlan(**d)


@requiere_admin
def obtener_plan(plan_id: int) -> PropuestaPlan:
    return _obtener_plan(plan_id)


def _guardar_plan(p: PropuestaPlan) -> None:
    db.actualizar("planes", p.id, p.model_dump(mode="json"))


def _resumen(asig: List[Asignacion], pend, prios: List[PrioridadSector], cist: List[Cisterna]) -> Dict[str, Any]:
    total_disp = sum(min(c.litros_disponibles, c.capacidad_litros) for c in cist if c.estado.value == "disponible")
    usado = sum(a.litros for a in asig)
    pend_set = {p.sector for p in pend}
    return {
        "sectores_con_prioridad": len(prios),
        "sectores_atendidos": len({a.sector for a in asig} - pend_set),
        "sectores_pendientes": sorted(pend_set),
        "litros_disponibles": total_disp, "litros_asignados": usado, "litros_sobrantes": total_disp - usado,
    }


def _guardar_derivados(plan: PropuestaPlan) -> None:
    plan.borradores_comunicacion = communication.redactar_borradores(plan.asignaciones, plan.pendientes)
    plan.resumen = _resumen(plan.asignaciones, plan.pendientes, plan.prioridades, listar_cisternas())


@requiere_admin
@operacion("generar_plan")
def generar_plan(responsable: str, metodo: str = "flujo") -> PropuestaPlan:
    """Calcula prioridades y propone asignaciones. NO aprueba ni envía nada."""
    responsable = _resolver_responsable(responsable)
    prios = _calcular_prioridades()
    if not prios:
        raise ErrorDeNegocio("No hay reportes confirmados. Confirme al menos un reporte primero.")
    cist = listar_cisternas()
    with tracing.traza("planificador", "proponer_asignaciones", {"sectores": len(prios), "cisternas": len(cist),
                                                                 "metodo": metodo}, tools=[f"planner.{metodo}"]) as t:
        asig, pend = planner.proponer_asignaciones(prios, cist, metodo=metodo)
        t["output"] = {"asignaciones": len(asig), "pendientes": len(pend)}
    plan = PropuestaPlan(id=0, fecha_creacion=datetime.now(), litros_por_persona=config.LITROS_POR_PERSONA,
                         metodo="flujo_maximo_lexicografico" if metodo == "flujo" else "greedy",
                         version_reglas=config.VERSION_REGLAS,
                         asignaciones=asig, pendientes=pend, prioridades=prios)
    _guardar_derivados(plan)
    pid = db.insertar("planes", plan.model_dump(mode="json"))
    plan.id = pid
    _guardar_plan(plan)
    tracing.correlacionar(f"plan:{pid}")
    audit.registrar(responsable, "generar_plan", "plan", pid,
                    detalle={"resumen": plan.resumen, "asignaciones": len(asig), "pendientes": len(pend),
                             "metodo": plan.metodo})
    return plan


@requiere_admin
@operacion("editar_plan")
def editar_plan(plan_id: int, nuevas_asignaciones: List[Asignacion], responsable: str, motivo: str) -> PropuestaPlan:
    """Modifica asignaciones de un plan 'propuesto'. Valida capacidad; registra antes/después."""
    responsable = _resolver_responsable(responsable)
    if not (motivo or "").strip():
        raise ErrorDeNegocio("Indique el motivo de la modificación.")
    plan = _obtener_plan(plan_id)
    if plan.estado != EstadoPlan.PROPUESTO:
        raise ErrorDeNegocio(f"El plan está '{plan.estado.value}' y ya no se puede editar.")
    errores = planner.validar_asignaciones(nuevas_asignaciones, listar_cisternas())
    if errores:
        raise ErrorDeNegocio("El plan editado no es válido: " + " ".join(errores))
    antes = [a.model_dump(mode="json") for a in plan.asignaciones]
    despues = [a.model_dump(mode="json") for a in nuevas_asignaciones]
    if antes == despues:
        raise ErrorDeNegocio("No hay cambios que guardar.")
    plan.asignaciones = nuevas_asignaciones
    plan.editado = True
    plan.version += 1
    _recalcular_pendientes(plan)
    _guardar_derivados(plan)
    _guardar_plan(plan)
    audit.registrar(responsable, "editar_plan", "plan", plan.id, motivo=motivo,
                    detalle={"antes": antes, "despues": despues, "version": plan.version})
    return plan


def _recalcular_pendientes(plan: PropuestaPlan) -> None:
    """Tras una edición manual, recalcula qué demanda queda sin cubrir (determinista)."""
    cubierto: Dict[str, int] = {}
    for a in plan.asignaciones:
        cubierto[a.sector] = cubierto.get(a.sector, 0) + a.litros
    pend = []
    for p in plan.prioridades:
        if not p.personas_afectadas:
            pend.append(SectorPendiente(sector=p.sector, prioridad=p.puntaje, litros_requeridos=0,
                                        litros_cubiertos=0, litros_faltantes=0,
                                        motivo="Falta el dato de personas afectadas."))
            continue
        req = planner.litros_requeridos(p.personas_afectadas, plan.litros_por_persona)
        cub = min(cubierto.get(p.sector, 0), req)
        if cub < req:
            pend.append(SectorPendiente(sector=p.sector, prioridad=p.puntaje, litros_requeridos=req,
                                        litros_cubiertos=cub, litros_faltantes=req - cub,
                                        motivo="Cobertura incompleta tras la modificación manual del plan."))
    plan.pendientes = pend


@requiere_admin
@operacion("decidir_plan")
def decidir_plan(plan_id: int, accion: AccionDecision, responsable: str, motivo: str = "") -> PropuestaPlan:
    """Aprobar o rechazar. Rechazar exige motivo. Aprobar revalida capacidad. El estado solo avanza
    por TRANSICIONES_PLAN."""
    if accion == AccionDecision.EDITAR:
        raise ErrorDeNegocio("Para editar use editar_plan().")
    responsable = _resolver_responsable(responsable)
    plan = _obtener_plan(plan_id)
    if accion == AccionDecision.RECHAZAR:
        if not (motivo or "").strip():
            raise ErrorDeNegocio("Indique el motivo del rechazo.")
        _mover_plan(plan, EstadoPlan.RECHAZADO)
    else:
        _mover_plan(plan, EstadoPlan.APROBADO)
        errores = planner.validar_asignaciones(plan.asignaciones, listar_cisternas())
        if errores:
            raise ErrorDeNegocio("No se puede aprobar: " + " ".join(errores))
        if plan.editado and not (motivo or "").strip():
            raise ErrorDeNegocio("El plan fue modificado: indique el motivo/comentario de la aprobación.")
        atendidos = {a.sector for a in plan.asignaciones}
        for r in _listar_reportes():
            if r.estado == EstadoReporte.CONFIRMADO and r.sector in atendidos:
                _mover_reporte(r, EstadoReporte.EN_PLAN)
                _guardar_reporte(r)
    _guardar_plan(plan)
    dec = Decision(plan_id=plan.id, accion=accion, responsable=responsable, motivo=motivo or "")
    audit.registrar(responsable, f"{accion.value}_plan", "plan", plan.id, motivo=motivo or "",
                    detalle={"decision": dec.model_dump(mode="json"), "editado": plan.editado,
                             "version": plan.version})
    return plan


# ------------------------------------------------- contratos para dispositivos
def estado_actual() -> EstadoActual:
    """Lectura para dispositivos: sin borradores de comunicación ni textos de ciudadanos."""
    planes = _listar_planes()
    aprobados = [p for p in planes if p.estado == EstadoPlan.APROBADO]
    propuestos = [p for p in planes if p.estado == EstadoPlan.PROPUESTO]
    reps = _listar_reportes()
    activos = [r for r in reps if r.estado in (EstadoReporte.CONFIRMADO, EstadoReporte.POR_REVISAR)]
    por_revisar = sum(r.estado == EstadoReporte.POR_REVISAR for r in reps)

    def limpio(p):
        return p.model_copy(update={"borradores_comunicacion": []}) if p else None

    prios = [{"sector": p.sector, "puntaje": p.puntaje, "categoria": p.categoria}
             for p in prioritization.calcular_prioridades(reps, listar_sectores())[:5]]
    vis = _visibilidad_sectores()
    return EstadoActual(generado=datetime.now(), plan_aprobado=limpio(aprobados[-1] if aprobados else None),
                        plan_pendiente_de_revision=limpio(propuestos[-1] if propuestos else None),
                        reportes_activos=len(activos), reportes_por_revisar=por_revisar,
                        cisternas=listar_cisternas(), prioridades_top=prios,
                        sectores_sin_informacion=sum(v["estado"] in ("sin_informacion_suficiente", "necesidad_no_evaluada",
                                                                      "sin_reportes_recientes") for v in vis))


@operacion("confirmacion_dispositivo")
def registrar_confirmacion_dispositivo(c: ConfirmacionDispositivo) -> None:
    """Un dispositivo publica una confirmación; solo se AUDITA, no cambia planes ni cisternas."""
    plan = _obtener_plan(c.plan_id)
    if plan.estado != EstadoPlan.APROBADO:
        raise ErrorDeNegocio("Solo se aceptan confirmaciones sobre planes aprobados.")
    audit.registrar(f"dispositivo:{c.dispositivo_id}", f"confirmacion_{c.tipo}", "plan", c.plan_id,
                    detalle=c.model_dump(mode="json"))


# ------------------------------------------------------------ portal público (sin cuenta)
MENSAJE_CODIGO_INVALIDO = "No existe un reporte con ese código."

# Estados internos -> texto comprensible para el ciudadano. Nunca promete hora de llegada.
_ESTADO_PUBLICO = {
    EstadoReporte.POR_REVISAR: "Recibido, en revisión.",
    EstadoReporte.CONFIRMADO: "Confirmado, en evaluación.",
    EstadoReporte.EN_PLAN: "Atención programada, hora aún sin confirmar.",
    EstadoReporte.DUPLICADO: "Agrupado con otro reporte del mismo problema.",
    EstadoReporte.RECHAZADO: "No procede como reporte.",
}


def consultar_reporte_publico(codigo: str, origen: Optional[str] = None) -> Dict[str, Any]:
    """Consulta por código de seguimiento, sin cuenta. Devuelve SOLO estado comprensible, sector (si está
    confirmado) y fecha: jamás el texto original, datos extraídos, motivos, ids ni responsables.
    Código mal formado y código inexistente dan EXACTAMENTE el mismo error; los intentos se limitan por origen."""
    _limitar("consulta:" + (origen or "global"), _LIMITE_CONSULTAS)
    c = (codigo or "").strip().upper()
    r = None
    if _RE_CODIGO.fullmatch(c):
        r = next((x for x in _listar_reportes() if x.codigo_seguimiento == c), None)
    if r is None:
        raise ErrorDeNegocio(MENSAJE_CODIGO_INVALIDO)
    confirmado = r.estado in (EstadoReporte.CONFIRMADO, EstadoReporte.EN_PLAN)
    return {"codigo": c, "estado": r.estado.value, "estado_publico": _ESTADO_PUBLICO[r.estado],
            "atencion": _atencion_publica(r),
            "sector": r.sector if confirmado else None,
            "fecha_creacion": r.fecha_creacion.isoformat(timespec="minutes"),
            "tiene_ubicacion": r.lat is not None and r.lng is not None, "n_fotos": len(r.foto_ids)}


def _atencion_publica(r: Reporte) -> Optional[Dict[str, bool]]:
    """Solo para reportes en plan: ¿hay una misión (no cancelada) con parada para este reporte y se registró su
    entrega? Sin cisterna, litros, horas ni coordenadas."""
    if r.estado != EstadoReporte.EN_PLAN:
        return None
    asignada = entregada = False
    for d in db.listar("misiones"):
        m = Mision(**d)
        if m.estado == EstadoMision.CANCELADA:
            continue
        for p in m.paradas:
            if r.id in p.reporte_ids:
                asignada = True
                entregada = entregada or p.estado == EstadoParada.ENTREGADA
    return {"mision_asignada": asignada, "entrega_registrada": entregada}


def avisos_publicos() -> List[Dict[str, Any]]:
    """Avisos para el público: atención programada por sector (planes APROBADOS; sin litros, cisternas ni horas
    ni borradores) y conteo agregado de reportes confirmados en evaluación por sector (sin textos)."""
    avisos: List[Dict[str, Any]] = []
    programados = sorted({a.sector for p in _listar_planes() if p.estado == EstadoPlan.APROBADO
                          for a in p.asignaciones})
    for s in programados:
        avisos.append({"tipo": "atencion_programada", "sector": s,
                       "mensaje": f"{s}: atención programada, hora aún sin confirmar."})
    conteo: Dict[str, int] = {}
    for r in _listar_reportes():
        if r.estado == EstadoReporte.CONFIRMADO and r.sector:
            conteo[r.sector] = conteo.get(r.sector, 0) + 1
    for s in sorted(conteo):
        n = conteo[s]
        avisos.append({"tipo": "en_evaluacion", "sector": s, "reportes_en_evaluacion": n,
                       "mensaje": f"{s}: {n} reporte(s) confirmado(s) en evaluación."})
    return avisos


# ================================================================== fotos (solo administración)
@requiere_admin
def obtener_foto(reporte_id: int, foto_id: str) -> Tuple[bytes, str]:
    """(bytes, tipo_mime) de una foto de un reporte. El portal público nunca llega aquí. La foto debe pertenecer
    al reporte indicado (no basta con conocer un id de foto)."""
    r = _obtener_reporte(reporte_id)
    if foto_id not in r.foto_ids:
        raise ErrorDeNegocio("La foto no existe.")
    res = fotos_srv.leer(foto_id)
    if res is None:
        raise ErrorDeNegocio("La foto no existe o su archivo no está disponible.")
    return res


@requiere_admin
@operacion("eliminar_foto")
def eliminar_foto(reporte_id: int, foto_id: str, motivo: str, responsable: str = "") -> Reporte:
    """Borrado controlado de una foto (p. ej. contiene datos personales). Quita archivo, metadatos y la referencia
    del reporte, con motivo obligatorio y auditoría. Si el almacén no confirma el borrado, no cambia nada."""
    quien = _quien(responsable)
    if not (motivo or "").strip():
        raise ErrorDeNegocio("Indique el motivo del borrado.")
    r = _obtener_reporte(reporte_id)
    if foto_id not in r.foto_ids:
        raise ErrorDeNegocio("La foto no existe.")
    if not fotos_srv.eliminar(foto_id):
        raise ErrorDeNegocio("No se pudo borrar el archivo de la foto; no se hizo ningún cambio.")
    r.foto_ids = [x for x in r.foto_ids if x != foto_id]
    _guardar_reporte(r)
    audit.registrar(quien, "eliminar_foto", "reporte", reporte_id, motivo=motivo.strip(), detalle={"foto_id": foto_id})
    return r


# ================================================================== audios (solo administración)
@requiere_admin
def obtener_audio(reporte_id: int, audio_id: str) -> Tuple[bytes, str]:
    """(bytes, tipo_mime) de una nota de voz/audio de un reporte. El portal público nunca llega aquí y el audio debe
    pertenecer al reporte indicado (no basta con conocer un id de audio)."""
    r = _obtener_reporte(reporte_id)
    if audio_id not in r.audio_ids:
        raise ErrorDeNegocio("El audio no existe.")
    res = audios_srv.leer(audio_id)
    if res is None:
        raise ErrorDeNegocio("El audio no existe o su archivo no está disponible.")
    return res


@requiere_admin
@operacion("eliminar_audio")
def eliminar_audio(reporte_id: int, audio_id: str, motivo: str, responsable: str = "") -> Reporte:
    """Borrado controlado de un audio (p. ej. contiene datos personales). Quita archivo, metadatos y la referencia
    del reporte, con motivo obligatorio y auditoría. Si el almacén no confirma el borrado, no cambia nada."""
    quien = _quien(responsable)
    if not (motivo or "").strip():
        raise ErrorDeNegocio("Indique el motivo del borrado.")
    r = _obtener_reporte(reporte_id)
    if audio_id not in r.audio_ids:
        raise ErrorDeNegocio("El audio no existe.")
    if not audios_srv.eliminar(audio_id):
        raise ErrorDeNegocio("No se pudo borrar el archivo del audio; no se hizo ningún cambio.")
    r.audio_ids = [x for x in r.audio_ids if x != audio_id]
    _guardar_reporte(r)
    audit.registrar(quien, "eliminar_audio", "reporte", reporte_id, motivo=motivo.strip(), detalle={"audio_id": audio_id})
    return r


def _quien(responsable: Optional[str] = None) -> str:
    """Responsable de auditoría para operaciones donde el nombre declarado es opcional."""
    a = auth.exigir_admin()
    if a.origen == "servicio":
        return f"servicio:{(responsable or '').strip() or a.usuario}"
    return a.usuario


# ============================================================ dispositivos (un vehículo = una terminal)
def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _doc_dispositivo(cisterna_id: str) -> Optional[Dispositivo]:
    d = db.obtener("dispositivos", cisterna_id)
    return Dispositivo(**d) if d else None


def _guardar_dispositivo(d: Dispositivo) -> None:
    # token_hash está excluido de la serialización pública; aquí se persiste explícitamente.
    db.insertar("dispositivos", {**d.model_dump(mode="json"), "token_hash": d.token_hash}, clave=d.cisterna_id)


def _sin_hash(d: Dispositivo) -> Dispositivo:
    return d.model_copy(update={"token_hash": ""})


def _token_nuevo() -> str:
    return secrets.token_urlsafe(32)        # 256 bits


@requiere_admin
@operacion("registrar_dispositivo")
def registrar_dispositivo(cisterna_id: str, nombre: str = "", responsable: str = "") -> Tuple[Dispositivo, str]:
    """Registra la terminal de un vehículo y devuelve (Dispositivo, token en claro). El token se muestra UNA vez:
    solo se guarda su SHA-256."""
    quien = _quien(responsable)
    if db.obtener("cisternas", cisterna_id) is None:
        raise ErrorDeNegocio(f"La cisterna {cisterna_id} no existe.")
    if _doc_dispositivo(cisterna_id) is not None:
        raise ErrorDeNegocio(f"La cisterna {cisterna_id} ya está registrada como dispositivo: rote su token.")
    token = _token_nuevo()
    d = Dispositivo(cisterna_id=cisterna_id, nombre=(nombre or "").strip()[:80], token_hash=_hash_token(token))
    _guardar_dispositivo(d)
    audit.registrar(quien, "registrar_dispositivo", "dispositivo", cisterna_id, detalle={"nombre": d.nombre})
    return _sin_hash(d), token


@requiere_admin
@operacion("rotar_token_dispositivo")
def rotar_token(cisterna_id: str, responsable: str = "") -> Tuple[Dispositivo, str]:
    """Genera un token nuevo e invalida el anterior al instante. También reactiva un dispositivo desactivado."""
    quien = _quien(responsable)
    d = _doc_dispositivo(cisterna_id)
    if d is None:
        raise ErrorDeNegocio(f"El dispositivo de la cisterna {cisterna_id} no existe.")
    token = _token_nuevo()
    d.token_hash = _hash_token(token)
    d.activo = True
    _guardar_dispositivo(d)
    audit.registrar(quien, "rotar_token_dispositivo", "dispositivo", cisterna_id)
    return _sin_hash(d), token


@requiere_admin
@operacion("desactivar_dispositivo")
def desactivar_dispositivo(cisterna_id: str, responsable: str = "") -> Dispositivo:
    quien = _quien(responsable)
    d = _doc_dispositivo(cisterna_id)
    if d is None:
        raise ErrorDeNegocio(f"El dispositivo de la cisterna {cisterna_id} no existe.")
    d.activo = False
    _guardar_dispositivo(d)
    audit.registrar(quien, "desactivar_dispositivo", "dispositivo", cisterna_id)
    return _sin_hash(d)


@requiere_admin
def listar_dispositivos() -> List[Dispositivo]:
    return [_sin_hash(Dispositivo(**d)) for d in db.listar("dispositivos")]


# ============================================================ usuarios (administración)
def _usuario_publico(d: dict) -> Dict[str, Any]:
    """Sin hash ni sal."""
    return {"usuario": d["usuario"], "rol": d["rol"], "activo": d.get("activo", True), "creado": d.get("creado"),
            "origen": "supabase" if (d.get("email") or d.get("supabase_uid")) else "local",
            "vinculado": bool(d.get("supabase_uid"))}


def _doc_usuario(usuario: str) -> dict:
    d = db.obtener("usuarios", (usuario or "").strip().lower())
    if d is None:
        raise ErrorDeNegocio("El usuario no existe.")
    return d


def _admins_activos() -> List[dict]:
    return [u for u in db.listar("usuarios") if u.get("rol") == auth.Rol.ADMIN.value and u.get("activo", True)]


def _exigir_otro_admin(d: dict) -> None:
    """Nunca se puede dejar el sistema sin administradores activos."""
    if d.get("rol") == auth.Rol.ADMIN.value and d.get("activo", True) and \
            not any(u["usuario"] != d["usuario"] for u in _admins_activos()):
        raise ErrorDeNegocio("No se puede dejar el sistema sin administradores activos.")


@requiere_admin
def listar_usuarios_admin() -> List[Dict[str, Any]]:
    return [_usuario_publico(d) for d in db.listar("usuarios")]


@requiere_admin
@operacion("crear_usuario")
def crear_usuario_admin(usuario: str, rol: str = "usuario", clave: Optional[str] = None,
                        responsable: str = "") -> Dict[str, Any]:
    """Crea una cuenta. Con HIDRO360_AUTH=local exige `clave` (política de contraseñas) y `usuario` es un nombre;
    con HIDRO360_AUTH=supabase `usuario` es el correo de una cuenta de Supabase y se crea solo el PERFIL (rol y
    estado) a vincular en su primer acceso; no se acepta clave. No reemplaza cuentas existentes."""
    quien = _quien(responsable)
    try:
        rol_ = auth.Rol(rol)
    except ValueError:
        raise ErrorDeNegocio("Rol inválido (administrador | usuario).")
    u = (usuario or "").strip().lower()
    if db.obtener("usuarios", u) is not None:
        raise ErrorDeNegocio("Ya existe un usuario con ese nombre.")
    try:
        if config.AUTH_BACKEND() == "supabase":
            if clave:
                raise ErrorDeNegocio("Con Supabase Auth la contraseña se gestiona en Supabase, no aquí.")
            from services import auth_supabase
            auth_supabase.crear_perfil(u, rol_)
        else:
            if not clave:
                raise ErrorDeNegocio("Indique una contraseña.")
            auth.crear_usuario(u, clave, rol_)
    except ValueError as e:
        if isinstance(e, ErrorDeNegocio):
            raise
        raise ErrorDeNegocio(str(e))
    audit.registrar(quien, "crear_usuario", "usuario", u, detalle={"rol": rol_.value})
    return _usuario_publico(_doc_usuario(u))


def _cambiar_activo(usuario: str, activo: bool, responsable: str) -> Dict[str, Any]:
    quien = _quien(responsable)
    d = _doc_usuario(usuario)
    if d.get("activo", True) == activo:
        raise ErrorDeNegocio(f"El usuario ya está {'activo' if activo else 'desactivado'}.")
    if not activo:
        _exigir_otro_admin(d)
    d["activo"] = activo
    db.actualizar("usuarios", d["usuario"], d)
    audit.registrar(quien, "reactivar_usuario" if activo else "desactivar_usuario", "usuario", d["usuario"])
    return _usuario_publico(d)


@requiere_admin
@operacion("desactivar_usuario")
def desactivar_usuario(usuario: str, responsable: str = "") -> Dict[str, Any]:
    """Bloquea el acceso al instante (sesiones locales y JWT de Supabase dejan de valer)."""
    return _cambiar_activo(usuario, False, responsable)


@requiere_admin
@operacion("reactivar_usuario")
def reactivar_usuario(usuario: str, responsable: str = "") -> Dict[str, Any]:
    return _cambiar_activo(usuario, True, responsable)


@requiere_admin
@operacion("cambiar_rol_usuario")
def cambiar_rol_usuario(usuario: str, rol: str, responsable: str = "") -> Dict[str, Any]:
    quien = _quien(responsable)
    try:
        nuevo = auth.Rol(rol)
    except ValueError:
        raise ErrorDeNegocio("Rol inválido (administrador | usuario).")
    d = _doc_usuario(usuario)
    if d["rol"] == nuevo.value:
        raise ErrorDeNegocio("El usuario ya tiene ese rol.")
    if nuevo != auth.Rol.ADMIN:
        _exigir_otro_admin(d)
    anterior, d["rol"] = d["rol"], nuevo.value
    db.actualizar("usuarios", d["usuario"], d)
    audit.registrar(quien, "cambiar_rol_usuario", "usuario", d["usuario"], detalle={"antes": anterior, "despues": nuevo.value})
    return _usuario_publico(d)


def autenticar_dispositivo(token: Optional[str]) -> Optional[Dispositivo]:
    """Identifica la terminal por su token (la identidad sale del token, jamás del cuerpo de la petición).
    Compara en tiempo constante contra todos los hashes. None si el token no es válido o el dispositivo está inactivo."""
    if not token or len(token) > 200:
        return None
    h = _hash_token(token)
    hallado = None
    for doc in db.listar("dispositivos"):
        if hmac.compare_digest(h, doc.get("token_hash", "")):
            hallado = doc
    if hallado is None:
        return None
    d = Dispositivo(**hallado)
    return d if d.activo else None


@operacion("dispositivo_latido")
def dispositivo_latido(cisterna_id: str, firmware: str = "", wifi_rssi: Optional[int] = None,
                       pendientes: int = 0, uptime_s: Optional[int] = None) -> Dict[str, Any]:
    """Latido de la terminal: última conexión y estado (no se audita cada latido)."""
    d = _doc_dispositivo(cisterna_id)
    if d is None or not d.activo:
        raise ErrorDeNegocio("Dispositivo no autorizado.")
    d.ultima_conexion = datetime.now()
    d.ultimo_estado = {"firmware": (firmware or "")[:40], "wifi_rssi": wifi_rssi,
                       "pendientes": max(0, int(pendientes or 0)), "uptime_s": uptime_s}
    _guardar_dispositivo(d)
    return {"ok": True, "servidor_hora": _ahora_utc()}


def _ahora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ================================================================================ misiones
def _obtener_mision(mision_id: int) -> Mision:
    d = db.obtener("misiones", mision_id)
    if d is None:
        raise ErrorDeNegocio(f"La misión {mision_id} no existe.")
    return Mision(**d)


def _guardar_mision(m: Mision) -> None:
    db.actualizar("misiones", m.id, m.model_dump(mode="json"))


def _mision_activa_de(cisterna_id: str) -> Optional[Mision]:
    activas = [Mision(**d) for d in db.listar("misiones")
               if d.get("cisterna_id") == cisterna_id and d.get("estado") in {e.value for e in _ACTIVAS}]
    return activas[-1] if activas else None


def _normalizar_paradas(paradas: List[Any], sectores: Dict[str, Sector]) -> List[Parada]:
    if not paradas:
        raise ErrorDeNegocio("La misión necesita al menos una parada.")
    salida: List[Parada] = []
    for i, p in enumerate(paradas, start=1):
        try:
            par = p if isinstance(p, Parada) else Parada(**p)
        except Exception:
            raise ErrorDeNegocio(f"La parada {i} es inválida (sector, litros mayores que 0 y orden).")
        if par.sector not in sectores:
            raise ErrorDeNegocio(f"Parada {par.orden}: el sector «{par.sector}» no está en el catálogo.")
        _validar_coord(par.lat, par.lng, f"Parada {par.orden}")
        sec = sectores[par.sector]
        lat, lng = (par.lat, par.lng) if par.lat is not None else (sec.lat, sec.lng)   # nunca se inventan
        salida.append(Parada(orden=par.orden, sector=par.sector, litros=par.litros, lat=lat, lng=lng,
                             reporte_ids=list(par.reporte_ids), instruccion=par.instruccion[:300],
                             estado=EstadoParada.PENDIENTE, litros_entregados=None, nota=""))
    ordenes = [p.orden for p in salida]
    if len(set(ordenes)) != len(ordenes):
        raise ErrorDeNegocio("Las paradas tienen órdenes repetidos.")
    return sorted(salida, key=lambda p: p.orden)


def _crear_mision(cisterna_id: str, paradas: List[Any], quien: str, plan_id: Optional[int],
                  recomendacion_id: Optional[int], motivo: str) -> Mision:
    cd = db.obtener("cisternas", cisterna_id)
    if cd is None:
        raise ErrorDeNegocio(f"La cisterna {cisterna_id} no existe.")
    cis = Cisterna(**cd)
    if cis.estado != EstadoCisterna.DISPONIBLE:
        raise ErrorDeNegocio(f"La cisterna {cisterna_id} no está disponible ({cis.estado.value}).")
    activa = _mision_activa_de(cisterna_id)
    if activa is not None:
        raise ErrorDeNegocio(f"La cisterna {cisterna_id} ya está en la misión activa {activa.id}: cancélela primero.")
    if plan_id is not None and db.obtener("planes", plan_id) is None:
        raise ErrorDeNegocio(f"El plan {plan_id} no existe.")
    if recomendacion_id is not None and db.obtener("recomendaciones", recomendacion_id) is None:
        raise ErrorDeNegocio(f"La recomendación {recomendacion_id} no existe.")
    sectores = {s.nombre: s for s in listar_sectores()}
    ps = _normalizar_paradas(paradas, sectores)
    total = sum(p.litros for p in ps)
    if total > cis.litros_disponibles:
        raise ErrorDeNegocio(f"Las paradas suman {total} L y la cisterna {cisterna_id} solo tiene "
                             f"{cis.litros_disponibles} L disponibles.")
    m = Mision(id=0, cisterna_id=cisterna_id, estado=EstadoMision.ASIGNADA, paradas=ps, plan_id=plan_id,
               recomendacion_id=recomendacion_id, creada=datetime.now(), creada_por=quien,
               motivo=(motivo or "").strip(), ficticio=cis.ficticio)
    m.id = db.insertar("misiones", m.model_dump(mode="json"))
    _guardar_mision(m)
    tracing.correlacionar(f"mision:{m.id}")
    audit.registrar(quien, "crear_mision", "mision", m.id, motivo=m.motivo,
                    detalle={"cisterna_id": cisterna_id, "plan_id": plan_id, "recomendacion_id": recomendacion_id,
                             "paradas": [p.model_dump(mode="json") for p in ps], "litros": total})
    return m


@requiere_admin
@operacion("crear_mision")
def crear_mision(cisterna_id: str, paradas: List[Any], responsable: str, plan_id: Optional[int] = None,
                 recomendacion_id: Optional[int] = None, motivo: str = "") -> Mision:
    """Asigna una misión a UN vehículo (la persona aprueba; la IA solo propone). Valida cisterna disponible,
    litros > 0 y dentro de lo disponible, sectores del catálogo y una sola misión activa por cisterna."""
    quien = _resolver_responsable(responsable)
    return _crear_mision(cisterna_id, paradas, quien, plan_id, recomendacion_id, motivo)


@requiere_admin
@operacion("crear_mision_desde_plan")
def crear_mision_desde_plan(plan_id: int, cisterna_id: str, responsable: str) -> Mision:
    """Convierte las asignaciones de un plan APROBADO para esa cisterna en paradas (mayor prioridad primero)."""
    quien = _resolver_responsable(responsable)
    plan = _obtener_plan(plan_id)
    if plan.estado != EstadoPlan.APROBADO:
        raise ErrorDeNegocio(f"El plan {plan_id} está '{plan.estado.value}': solo se asignan planes aprobados.")
    propias = [a for a in plan.asignaciones if a.cisterna_id == cisterna_id]
    if not propias:
        raise ErrorDeNegocio(f"El plan {plan_id} no asigna la cisterna {cisterna_id}.")
    if any(d.get("plan_id") == plan_id and d.get("cisterna_id") == cisterna_id and d.get("estado") != "cancelada"
           for d in db.listar("misiones")):
        raise ErrorDeNegocio(f"El plan {plan_id} ya está convertido en misión para la cisterna {cisterna_id}.")
    por_sector: Dict[str, Asignacion] = {}
    for a in sorted(propias, key=lambda x: -x.prioridad):
        if a.sector in por_sector:
            por_sector[a.sector] = por_sector[a.sector].model_copy(update={"litros": por_sector[a.sector].litros + a.litros})
        else:
            por_sector[a.sector] = a
    reps = _listar_reportes()
    paradas = []
    for orden, (sector, a) in enumerate(por_sector.items(), start=1):
        ids = [r.id for r in reps if r.sector == sector and r.estado in (EstadoReporte.CONFIRMADO, EstadoReporte.EN_PLAN)]
        paradas.append(Parada(orden=orden, sector=sector, litros=a.litros, reporte_ids=ids,
                              instruccion=a.nota or f"Plan {plan_id}"))
    return _crear_mision(cisterna_id, paradas, quien, plan_id, None, f"Desde el plan aprobado {plan_id}")


@requiere_admin
def listar_misiones(estado: Any = None) -> List[Mision]:
    if estado is not None:
        try:
            estado = EstadoMision(getattr(estado, "value", estado))
        except ValueError:
            raise ErrorDeNegocio(f"Estado de misión inválido: {estado}")
    ms = [Mision(**d) for d in db.listar("misiones")]
    return [m for m in ms if estado is None or m.estado == estado]


@requiere_admin
def obtener_mision(mision_id: int) -> Mision:
    return _obtener_mision(mision_id)


@requiere_admin
@operacion("cancelar_mision")
def cancelar_mision(mision_id: int, responsable: str, motivo: str) -> Mision:
    quien = _resolver_responsable(responsable)
    if not (motivo or "").strip():
        raise ErrorDeNegocio("Indique el motivo de la cancelación.")
    m = _obtener_mision(mision_id)
    if EstadoMision.CANCELADA not in TRANSICIONES_MISION[m.estado]:
        raise ErrorDeNegocio(f"Transición no permitida para la misión {m.id}: '{m.estado.value}' → 'cancelada'.")
    m.estado = EstadoMision.CANCELADA
    _guardar_mision(m)
    audit.registrar(quien, "cancelar_mision", "mision", m.id, motivo=motivo)
    return m


@requiere_admin
def listar_eventos(mision_id: Optional[int] = None, cisterna_id: Optional[str] = None) -> List[RegistroEvento]:
    ev = [RegistroEvento(**d) for d in db.listar("eventos_mision")]
    return [e for e in ev if (mision_id is None or e.mision_id == mision_id)
            and (cisterna_id is None or e.cisterna_id == cisterna_id)]


def _siguiente_tarea(m: Mision) -> Optional[Dict[str, Any]]:
    for p in sorted(m.paradas, key=lambda x: x.orden):
        if p.estado in _PARADA_TERMINAL:
            continue
        return {"parada_orden": p.orden, "accion": "ir_a" if p.estado == EstadoParada.PENDIENTE else "entregar",
                "sector": p.sector, "litros": p.litros, "lat": p.lat, "lng": p.lng, "instruccion": p.instruccion}
    return None


def dispositivo_mision_activa(cisterna_id: str) -> Dict[str, Any]:
    """Contrato de GET /dispositivo/mision. Solo la misión de ESE vehículo (la identidad ya viene del token)."""
    m = _mision_activa_de(cisterna_id)
    cuerpo = None
    if m is not None:
        cuerpo = m.model_dump(mode="json")
        cuerpo["siguiente_tarea"] = _siguiente_tarea(m)
    return {"cisterna_id": cisterna_id, "servidor_hora": _ahora_utc(), "mision": cuerpo}


MAX_EVENTOS_LOTE = 50


def _aplicar_evento(cisterna_id: str, ev: EventoMision) -> Tuple[str, str]:
    """Aplica un evento a la parada/misión. Devuelve (resultado, motivo). Lo llama una transacción abierta."""
    d = db.obtener("misiones", ev.mision_id)
    if d is None or d.get("cisterna_id") != cisterna_id:       # mismo mensaje: no se revela misiones ajenas
        return "rechazado", "La misión no existe para este vehículo."
    m = Mision(**d)
    if m.estado not in _ACTIVAS:
        return "rechazado", f"La misión no está activa ({m.estado.value})."
    parada = next((p for p in m.paradas if p.orden == ev.parada_orden), None)
    if parada is None:
        return "rechazado", "La parada no existe en la misión."
    nuevo = _TIPO_A_PARADA[ev.tipo]
    if nuevo not in TRANSICIONES_PARADA[parada.estado]:
        return "rechazado", f"Transición no permitida en la parada: '{parada.estado.value}' → '{nuevo.value}'."
    litros = 0
    cis = None
    if ev.tipo == "entrega":
        litros = ev.litros_entregados if ev.litros_entregados is not None else parada.litros
        if litros <= 0:
            return "rechazado", "Una entrega necesita litros mayores que 0 (use una incidencia si no se entregó)."
        if litros > parada.litros:
            return "rechazado", f"Los litros entregados superan los planificados ({parada.litros} L)."
        cd = db.obtener("cisternas", cisterna_id)
        if cd is None:
            return "rechazado", "La cisterna no existe."
        cis = Cisterna(**cd)
    quien = f"dispositivo:{cisterna_id}"
    antes_m = m.estado
    parada.estado = nuevo
    if ev.nota:
        parada.nota = ev.nota
    detalle: Dict[str, Any] = {"parada_orden": parada.orden, "tipo": ev.tipo, "uid": ev.uid}
    if cis is not None:
        descontados = min(litros, cis.litros_disponibles)           # nunca por debajo de 0
        parada.litros_entregados = litros
        cis.litros_disponibles -= descontados
        db.actualizar("cisternas", cis.id, cis.model_dump(mode="json"))
        detalle.update({"litros_entregados": litros, "litros_descontados": descontados,
                        "litros_disponibles_despues": cis.litros_disponibles})
    if m.estado == EstadoMision.ASIGNADA:
        m.estado = EstadoMision.EN_CURSO
    if all(p.estado in _PARADA_TERMINAL for p in m.paradas):
        m.estado = EstadoMision.COMPLETADA
    _guardar_mision(m)
    detalle["mision_estado"] = {"antes": antes_m.value, "despues": m.estado.value}
    audit.registrar(quien, f"evento_{ev.tipo}", "mision", m.id, motivo=ev.nota, detalle=detalle)
    return "aplicado", ""


@operacion("dispositivo_eventos")
def dispositivo_recibir_eventos(cisterna_id: str, eventos: List[Any]) -> List[Dict[str, str]]:
    """Procesa un lote de eventos de la terminal en UNA transacción. Idempotente por `uid`: un evento ya aplicado
    responde «duplicado» sin tocar nada (ni volver a descontar litros). Los eventos inválidos se rechazan uno a uno."""
    if len(eventos) > MAX_EVENTOS_LOTE:
        raise ErrorDeNegocio(f"Máximo {MAX_EVENTOS_LOTE} eventos por envío.")
    d = _doc_dispositivo(cisterna_id)
    if d is None or not d.activo:
        raise ErrorDeNegocio("Dispositivo no autorizado.")
    aplicados = {e["uid"]: e for e in db.listar("eventos_mision") if e.get("resultado") == "aplicado"}
    resultados: List[Dict[str, str]] = []
    vistos: set = set()
    for bruto in eventos:
        try:
            ev = bruto if isinstance(bruto, EventoMision) else EventoMision(**bruto)
        except Exception:
            uid = bruto.get("uid") if isinstance(bruto, dict) and isinstance(bruto.get("uid"), str) else ""
            resultados.append({"uid": uid[:64], "resultado": "rechazado", "motivo": "Evento con formato inválido."})
            continue
        previo = aplicados.get(ev.uid)
        if previo is not None or ev.uid in vistos:
            if previo is not None and previo.get("cisterna_id") != cisterna_id:
                resultados.append({"uid": ev.uid, "resultado": "rechazado", "motivo": "Identificador de evento en uso."})
            else:
                resultados.append({"uid": ev.uid, "resultado": "duplicado", "motivo": "Ya aplicado."})
            continue
        resultado, motivo = _aplicar_evento(cisterna_id, ev)
        datos = ev.model_dump(mode="json")
        if resultado == "rechazado":
            datos["nota"] = f"RECHAZADO: {motivo}"[:300]       # el contrato no tiene campo «motivo»
        else:
            vistos.add(ev.uid)
        reg = RegistroEvento(**{**ev.model_dump(), "nota": datos["nota"]}, cisterna_id=cisterna_id,
                             fecha_servidor=datetime.now(), resultado=resultado)
        db.insertar("eventos_mision", reg.model_dump(mode="json"))
        resultados.append({"uid": ev.uid, "resultado": resultado, "motivo": motivo})
    return resultados


# ==================================================================================== mapa
@requiere_admin
def datos_mapa() -> Dict[str, Any]:
    """Datos reales de la BD para el mapa (no se inventan coordenadas)."""
    reportes = [{"id": r.id, "lat": r.lat, "lng": r.lng, "estado": r.estado.value, "sector": r.sector,
                 "severidad": r.severidad.value if r.severidad else None, "ficticio": r.ficticio}
                for r in _listar_reportes() if r.lat is not None and r.lng is not None]
    sectores = {s.nombre: s for s in listar_sectores()}
    misiones = [Mision(**d) for d in db.listar("misiones")]
    ultima: Dict[str, Tuple[float, float]] = {}
    for m in misiones:      # última parada alcanzada (llegada/entrega/incidencia) = última ubicación conocida
        for p in sorted(m.paradas, key=lambda x: x.orden):
            if p.estado != EstadoParada.PENDIENTE and p.lat is not None:
                ultima[m.cisterna_id] = (p.lat, p.lng)
    cisternas = []
    for c in listar_cisternas():
        lat = lng = None
        fuente = None
        if c.id in ultima:
            (lat, lng), fuente = ultima[c.id], "ultima_parada"
        elif c.sector_base and sectores.get(c.sector_base) and sectores[c.sector_base].lat is not None:
            lat, lng, fuente = sectores[c.sector_base].lat, sectores[c.sector_base].lng, "base_sector"
        cisternas.append({"id": c.id, "estado": c.estado.value, "litros_disponibles": c.litros_disponibles,
                          "capacidad_litros": c.capacidad_litros, "lat": lat, "lng": lng, "fuente_ubicacion": fuente,
                          "ficticio": c.ficticio})
    paradas = [{"mision_id": m.id, "cisterna_id": m.cisterna_id, "orden": p.orden, "sector": p.sector,
                "lat": p.lat, "lng": p.lng, "litros": p.litros, "estado": p.estado.value}
               for m in misiones if m.estado in _ACTIVAS for p in m.paradas if p.lat is not None]
    return {"generado": _ahora_utc(), "reportes": reportes, "cisternas": cisternas, "paradas": paradas}
