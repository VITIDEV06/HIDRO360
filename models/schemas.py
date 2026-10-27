"""
Contratos de datos estables de HIDRO360 (Pydantic).

Estos modelos son el "contrato" entre la lógica de negocio, la interfaz
(FLET), la API HTTP (FastAPI) y, más adelante, dispositivos externos
(Raspberry Pi 4 / ESP32-S3). Cambiar nombres de campo aquí es un cambio de
contrato: incrementar SCHEMA_VERSION.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.4"  # 1.4: adjuntos de audio (Reporte.audio_ids), mensajes adicionales y señales de revisión


class Severidad(str, Enum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"
    CRITICA = "crítica"


class Vulnerabilidad(str, Enum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"
    CRITICA = "crítica"


class EstadoReporte(str, Enum):
    POR_REVISAR = "por_revisar"      # extraído, aún sin confirmar por una persona
    CONFIRMADO = "confirmado"        # datos y ubicación confirmados
    DUPLICADO = "duplicado"          # una persona lo marcó duplicado de otro
    EN_PLAN = "en_plan"              # incluido en un plan aprobado
    RECHAZADO = "rechazado"          # descartado por una persona


class EstadoCisterna(str, Enum):
    DISPONIBLE = "disponible"
    EN_MANTENIMIENTO = "en_mantenimiento"
    FUERA_DE_SERVICIO = "fuera_de_servicio"


class EstadoPlan(str, Enum):
    PROPUESTO = "propuesto"
    APROBADO = "aprobado"
    RECHAZADO = "rechazado"


class AccionDecision(str, Enum):
    APROBAR = "aprobar"
    RECHAZAR = "rechazar"
    EDITAR = "editar"


# --------------------------------------------------------------------------
# Catálogos
# --------------------------------------------------------------------------
class Sector(BaseModel):
    nombre: str
    poblacion_estimada: int = Field(ge=0)
    vulnerabilidad: Vulnerabilidad = Vulnerabilidad.MEDIA
    lat: Optional[float] = None
    lng: Optional[float] = None
    alias: List[str] = Field(default_factory=list)          # variantes de nombre
    referencias: List[str] = Field(default_factory=list)    # referencias coloquiales ("chino de ...")
    antecedentes_interrupciones: Optional[int] = None       # nº de cortes documentados; None = sin dato
    fuente_datos: str = "ficticio (demo)"
    fecha_datos: Optional[str] = None
    ficticio: bool = True


class Cisterna(BaseModel):
    id: str
    capacidad_litros: int = Field(ge=0)
    litros_disponibles: int = Field(ge=0)
    estado: EstadoCisterna = EstadoCisterna.DISPONIBLE
    sector_base: Optional[str] = None
    sectores_elegibles: Optional[List[str]] = None  # None = puede ir a cualquier sector
    ficticio: bool = True

    @model_validator(mode="after")
    def _disponible_no_excede_capacidad(self):
        if self.litros_disponibles > self.capacidad_litros:
            raise ValueError("litros_disponibles no puede exceder capacidad_litros")
        return self


# --------------------------------------------------------------------------
# Reportes
# --------------------------------------------------------------------------
class Extraccion(BaseModel):
    """Resultado de interpretar el texto. Nunca reemplaza al texto original."""
    sector: Optional[str] = None            # solo si la coincidencia es exacta/alias y no ambigua
    sector_candidatos: List[Dict[str, Any]] = Field(default_factory=list)  # opciones para el operador
    referencia_ubicacion: Optional[str] = None
    tipo_incidencia: Optional[str] = None   # sin_suministro | tuberia_rota | baja_presion | otro
    vulnerabilidades: List[str] = Field(default_factory=list)
    servicio_esencial: Optional[str] = None
    evidencia: Dict[str, str] = Field(default_factory=dict)   # campo -> fragmento literal del mensaje
    confianza_declarada: Optional[float] = Field(None, ge=0, le=1)  # subjetiva del intérprete; NO calibrada
    calle: Optional[str] = None
    horas_sin_agua: Optional[float] = Field(None, ge=0)
    personas_afectadas: Optional[int] = Field(None, ge=0)
    severidad: Optional[Severidad] = None
    datos_faltantes: List[str] = Field(default_factory=list)
    datos_inciertos: List[str] = Field(default_factory=list)
    notas: List[str] = Field(default_factory=list)
    metodo: str = "reglas"


class Reporte(BaseModel):
    id: int
    mensaje_original: str
    fecha_creacion: datetime
    estado: EstadoReporte = EstadoReporte.POR_REVISAR
    sector: Optional[str] = None
    calle: Optional[str] = None
    horas_sin_agua: Optional[float] = None
    personas_afectadas: Optional[int] = None
    severidad: Optional[Severidad] = None
    datos_faltantes: List[str] = Field(default_factory=list)
    datos_inciertos: List[str] = Field(default_factory=list)
    notas: List[str] = Field(default_factory=list)
    campos_corregidos: List[str] = Field(default_factory=list)
    duplicado_de: Optional[int] = None
    confirmado_por: Optional[str] = None
    ficticio: bool = False
    canal: str = "manual"
    referencia_ubicacion: Optional[str] = None
    tipo_incidencia: Optional[str] = None
    vulnerabilidades: List[str] = Field(default_factory=list)
    servicio_esencial: Optional[str] = None
    evidencia: Dict[str, str] = Field(default_factory=dict)
    confianza_declarada: Optional[float] = None
    interprete: str = "reglas"
    sector_candidatos: List[Dict[str, Any]] = Field(default_factory=list)
    clave_idempotencia: Optional[str] = None
    motivo_rechazo: Optional[str] = None
    codigo_seguimiento: Optional[str] = None  # HID-XXXXXXXX; reportes antiguos no lo tienen
    lat: Optional[float] = Field(None, ge=-90, le=90)     # coordenadas elegidas en el mapa (None = no indicadas)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    ubicacion_fuente: Optional[str] = None                # "mapa" | "sector" (centro del sector, aproximada)
    foto_ids: List[str] = Field(default_factory=list)     # ids de Foto (ver services/fotos)
    audio_ids: List[str] = Field(default_factory=list)    # ids de Audio (ver services/audios)
    # Texto adicional que el ciudadano aportó DESPUÉS (p. ej. el webhook de Telegram pide una aclaración).
    # Nunca modifica `mensaje_original`, que es inalterable.
    mensajes_adicionales: List[str] = Field(default_factory=list)
    # Señales deterministas de que el reporte merece revisión humana cuidadosa (inconsistencias, exageración
    # posible, ubicación imprecisa). NO son un veredicto de falsedad: solo marcan para que una persona lo mire.
    senales_revision: List[str] = Field(default_factory=list)


class CandidatoDuplicado(BaseModel):
    reporte_id: int
    similitud: float
    motivos: List[str]
    dudoso: bool = True  # nunca se fusiona automáticamente


# --------------------------------------------------------------------------
# Prioridad y plan
# --------------------------------------------------------------------------
class FactorPrioridad(BaseModel):
    nombre: str
    valor: Optional[float] = None
    normalizado: float
    peso: float
    contribucion: float
    explicacion: str


class PrioridadSector(BaseModel):
    sector: str
    puntaje: float                      # 0-100, reproducible
    categoria: str
    factores: List[FactorPrioridad]
    reportes_ids: List[int]
    reportes_contados: int              # informativo; NO afecta el puntaje
    horas_sin_agua: Optional[float] = None
    personas_afectadas: Optional[int] = None
    severidad: Optional[Severidad] = None
    razones: List[str]


class Asignacion(BaseModel):
    sector: str
    cisterna_id: str
    litros: int = Field(gt=0)
    prioridad: float
    nota: str = ""


class SectorPendiente(BaseModel):
    sector: str
    prioridad: float
    litros_requeridos: int
    litros_cubiertos: int
    litros_faltantes: int
    motivo: str


class PropuestaPlan(BaseModel):
    id: int
    version: int = 1
    fecha_creacion: datetime
    estado: EstadoPlan = EstadoPlan.PROPUESTO
    litros_por_persona: int
    metodo: str = "flujo_maximo_lexicografico"
    version_reglas: str = ""
    asignaciones: List[Asignacion]
    pendientes: List[SectorPendiente]
    prioridades: List[PrioridadSector]
    editado: bool = False
    resumen: Dict[str, Any] = Field(default_factory=dict)
    borradores_comunicacion: List[Dict[str, str]] = Field(default_factory=list)


class Decision(BaseModel):
    plan_id: int
    accion: AccionDecision
    responsable: str
    motivo: str = ""
    fecha: datetime = Field(default_factory=datetime.now)
    cambios: List[Dict[str, Any]] = Field(default_factory=list)


class EntradaAuditoria(BaseModel):
    id: int
    fecha: datetime
    responsable: str
    accion: str
    entidad: str
    entidad_id: str
    motivo: str = ""
    detalle: Dict[str, Any] = Field(default_factory=dict)
    hash_previo: str
    hash: str


# --------------------------------------------------------------------------
# Contratos para dispositivos futuros (Raspberry Pi 4 / ESP32-S3)
# --------------------------------------------------------------------------
class EstadoActual(BaseModel):
    """Lo que un dispositivo puede leer con GET /api/v1/estado."""
    schema_version: str = SCHEMA_VERSION
    generado: datetime
    plan_aprobado: Optional[PropuestaPlan] = None
    plan_pendiente_de_revision: Optional[PropuestaPlan] = None
    reportes_activos: int = 0
    reportes_por_revisar: int = 0
    prioridades_top: List[Dict[str, Any]] = Field(default_factory=list)
    sectores_sin_informacion: int = 0
    cisternas: List[Cisterna] = Field(default_factory=list)


class ConfirmacionDispositivo(BaseModel):
    """Lo que un dispositivo publica con POST /api/v1/dispositivos/confirmaciones."""
    dispositivo_id: str
    plan_id: int
    cisterna_id: Optional[str] = None
    tipo: str = Field(description="p. ej. 'plan_recibido', 'cisterna_en_ruta', 'entrega_realizada'")
    detalle: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Fotografías, dispositivos (un vehículo = una Cisterna = una terminal ESP32-S3) y misiones
# --------------------------------------------------------------------------
class Foto(BaseModel):
    """Metadatos; el archivo vive en config.MEDIA_DIR/<id>.<ext> (fuera del código y de Git)."""
    id: str                                  # aleatorio, sin relación con el nombre original
    reporte_id: Optional[int] = None
    tipo_mime: str                           # image/jpeg | image/png | image/webp (validado por firma, no por extensión)
    bytes: int
    sha256: str
    fecha: datetime = Field(default_factory=datetime.now)


class Audio(BaseModel):
    """Metadatos de una nota de voz / audio. El archivo vive en MEDIA_DIR (local) o en el bucket privado de audios
    (Supabase). El tipo se decide por firma binaria, nunca por la extensión ni el Content-Type del cliente.
    `transcripcion` es el texto obtenido (o None si el adaptador no pudo); `metodo_transcripcion` declara el
    proveedor real o `simulada`, para no presentar como transcrito lo que no se transcribió."""
    id: str
    reporte_id: Optional[int] = None
    tipo_mime: str                           # audio/ogg | audio/mpeg | audio/mp4 | audio/wav | audio/webm | audio/amr
    bytes: int
    sha256: str
    duracion_s: Optional[float] = None       # declarada por Telegram; no verificada
    transcripcion: Optional[str] = None
    metodo_transcripcion: str = "simulada"   # simulada | gemini | otro proveedor real
    fecha: datetime = Field(default_factory=datetime.now)


class EstadoParada(str, Enum):
    PENDIENTE = "pendiente"
    LLEGADA = "llegada"
    ENTREGADA = "entregada"
    INCIDENCIA = "incidencia"


class EstadoMision(str, Enum):
    ASIGNADA = "asignada"
    EN_CURSO = "en_curso"
    COMPLETADA = "completada"
    CANCELADA = "cancelada"


class Parada(BaseModel):
    orden: int = Field(ge=1)
    sector: str
    litros: int = Field(gt=0)
    lat: Optional[float] = None
    lng: Optional[float] = None
    reporte_ids: List[int] = Field(default_factory=list)
    instruccion: str = ""
    estado: EstadoParada = EstadoParada.PENDIENTE
    litros_entregados: Optional[int] = None
    nota: str = ""


class Mision(BaseModel):
    id: int
    cisterna_id: str
    estado: EstadoMision = EstadoMision.ASIGNADA
    paradas: List[Parada]
    plan_id: Optional[int] = None
    recomendacion_id: Optional[int] = None   # si nació de una recomendación IA aprobada por una persona
    creada: datetime = Field(default_factory=datetime.now)
    creada_por: str                          # persona (o servicio) que la aprobó/asignó
    motivo: str = ""
    ficticio: bool = False


class Dispositivo(BaseModel):
    """Identidad de una terminal. Una por cisterna. El token solo se muestra al registrarla; se guarda su hash."""
    cisterna_id: str
    nombre: str = ""
    token_hash: str = Field(default="", exclude=True)   # nunca se serializa hacia la API
    activo: bool = True
    creado: datetime = Field(default_factory=datetime.now)
    ultima_conexion: Optional[datetime] = None
    ultimo_estado: Dict[str, Any] = Field(default_factory=dict)  # wifi, firmware, eventos pendientes...


class EventoMision(BaseModel):
    """Evento enviado por la terminal. `uid` lo genera el dispositivo: reenviarlo no duplica nada (idempotente)."""
    uid: str = Field(min_length=8, max_length=64)
    tipo: str = Field(pattern="^(llegada|entrega|incidencia)$")
    mision_id: int
    parada_orden: int = Field(ge=1)
    litros_entregados: Optional[int] = Field(None, ge=0)
    nota: str = Field("", max_length=300)
    fecha_dispositivo: Optional[datetime] = None   # el reloj del ESP32 puede ser inexacto: solo informativo


class RegistroEvento(EventoMision):
    id: int = 0
    cisterna_id: str
    fecha_servidor: datetime = Field(default_factory=datetime.now)
    resultado: str = "aplicado"                    # aplicado | duplicado | rechazado


# --------------------------------------------------------------------------
# Orquestación IA (la persona decide; la IA solo propone)
# --------------------------------------------------------------------------
class EtapaIA(BaseModel):
    nombre: str      # validacion | duplicados | clasificacion | prioridad | recomendacion | asignacion
    estado: str      # ok | omitida | error | fallback
    proveedor: str   # reglas | gemini | reglas(fallback)
    duracion_ms: int = 0
    resumen: str = ""
    detalle: Dict[str, Any] = Field(default_factory=dict)


class EstadoRecomendacion(str, Enum):
    PROPUESTA = "propuesta"
    APROBADA = "aprobada"
    MODIFICADA = "modificada"
    RECHAZADA = "rechazada"


class Recomendacion(BaseModel):
    id: int = 0
    reporte_id: int
    fecha: datetime = Field(default_factory=datetime.now)
    estado: EstadoRecomendacion = EstadoRecomendacion.PROPUESTA
    proveedor: str                              # procedencia real: gemini | reglas | reglas(fallback)
    etapas: List[EtapaIA]
    prioridad_sugerida: Optional[float] = None
    categoria: Optional[str] = None
    accion_sugerida: str = ""
    posibles_duplicados: List[int] = Field(default_factory=list)
    cisterna_sugerida: Optional[str] = None
    paradas_sugeridas: List[Parada] = Field(default_factory=list)
    decidida_por: Optional[str] = None
    motivo_decision: str = ""
    mision_id: Optional[int] = None             # solo existe tras aprobación humana
