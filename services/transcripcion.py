"""
Adaptador de transcripción de notas de voz.

El proyecto NO integra todavía un servicio de transcripción real (no hay proveedor contratado ni clave). En lugar de
fingir resultados, este módulo:
  - declara una interfaz (`Proveedor`) y un resultado tipado (`ResultadoTranscripcion`);
  - implementa `Simulada`, que NO transcribe: devuelve `disponible=False` y lo dice en `notas`;
  - deja el punto exacto donde conectar un proveedor real (`TRANSCRIPCION_BACKEND`), que debe devolver
    `disponible=True` con el texto y su `metodo` real.

Regla de honestidad: mientras no exista un proveedor real, un audio se conserva y se marca para revisión humana, y el
sistema pide al ciudadano una descripción por texto. Nunca se presenta como "transcrito" lo que no se transcribió.
"""

from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

import config

MULTIMEDIA_SOPORTADA = ("audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/amr")


class ResultadoTranscripcion(BaseModel):
    """`disponible=False` significa: NO se transcribió (no que el audio esté vacío)."""
    texto: Optional[str] = None
    disponible: bool = False
    metodo: str = "simulada"                 # simulada | gemini | <proveedor real>
    idioma: Optional[str] = None
    confianza: Optional[float] = Field(None, ge=0, le=1)   # subjetiva del proveedor; NO calibrada
    notas: List[str] = Field(default_factory=list)


class Proveedor:
    """Interfaz. Una implementación real recibe (datos, mime) y devuelve un ResultadoTranscripcion con
    `disponible=True` y el texto. Debe ser tolerante a fallos: ante un error, devolver `disponible=False` con nota."""

    nombre = "base"

    def transcribir(self, datos: bytes, mime: str) -> ResultadoTranscripcion:  # pragma: no cover - interfaz
        raise NotImplementedError


class Simulada(Proveedor):
    """Sin red ni claves. No transcribe; lo declara con claridad para que nadie lo confunda con un resultado real."""

    nombre = "simulada"

    def transcribir(self, datos: bytes, mime: str) -> ResultadoTranscripcion:
        return ResultadoTranscripcion(
            texto=None, disponible=False, metodo="simulada",
            notas=["La transcripción automática no está configurada; el audio se guarda para revisión humana."],
        )


_PROVEEDORES: Dict[str, Callable[[], Proveedor]] = {"simulada": Simulada}


def _proveedor() -> Proveedor:
    nombre = config.TRANSCRIPCION_BACKEND()
    constructor = _PROVEEDORES.get(nombre)
    if constructor is None:      # proveedor pedido pero no implementado: se declara y no se transcribe
        return _NoImplementado(nombre)
    return constructor()


class _NoImplementado(Proveedor):
    def __init__(self, nombre: str):
        self.nombre = nombre

    def transcribir(self, datos: bytes, mime: str) -> ResultadoTranscripcion:
        return ResultadoTranscripcion(texto=None, disponible=False, metodo="simulada",
                                      notas=[f"Proveedor «{self.nombre}» no implementado; audio guardado sin transcribir."])


def transcribir(datos: bytes, mime: str) -> ResultadoTranscripcion:
    """Traduce un audio a texto con el proveedor configurado. Nunca lanza: ante cualquier fallo devuelve un resultado
    no disponible (el audio se conserva igual y va a revisión humana)."""
    if mime not in MULTIMEDIA_SOPORTADA:
        return ResultadoTranscripcion(texto=None, disponible=False, metodo="simulada",
                                      notas=[f"Tipo de audio no soportado para transcripción: {mime}."])
    try:
        return _proveedor().transcribir(datos, mime)
    except Exception:  # noqa: BLE001 - una transcripción que falla nunca debe tumbar el reporte
        return ResultadoTranscripcion(texto=None, disponible=False, metodo="simulada",
                                      notas=["La transcripción falló; el audio se guarda para revisión humana."])


def metadatos_proveedor() -> Dict[str, Any]:
    """Informativo (diagnóstico / documentación): nombre efectivo del proveedor."""
    return {"backend": config.TRANSCRIPCION_BACKEND(), "implementado": config.TRANSCRIPCION_BACKEND() in _PROVEEDORES}
