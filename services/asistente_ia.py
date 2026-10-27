"""Orquestación del asistente: Gemini + herramientas autorizadas de HIDRO360.

Flujo: pregunta -> Gemini decide si necesita una herramienta -> el sistema VALIDA la llamada -> ejecuta la función
de negocio existente -> el resultado real vuelve a Gemini -> respuesta final. Sin Gemini (o si falla) se responde con
las reglas locales de `portal/asistente.py`, declarando siempre la procedencia.

Reglas que no se rompen:
- Las herramientas solo llaman a funciones públicas de `services.workflow` (nunca a SQLite).
- Ninguna herramienta guarda un reporte. `proponer_reporte` solo prepara un borrador; `confirmar_reporte` lo guarda y
  debe invocarla la persona desde la interfaz o el bot tras confirmar.
- El modelo no ejecuta comandos, no lee archivos y no usa datos personales de otros ciudadanos.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import config
from interface.flet_app.portal import asistente as reglas_locales
from services import ia_gemini, workflow

CODIGO_RE = re.compile(r"^HID-[A-Z0-9]{8}$")
LARGO_MIN_REPORTE, LARGO_MAX_REPORTE = 10, 1500
SISTEMA = ("Eres el asistente de HIDRO360, un sistema ciudadano para reportar falta de agua en Panamá Oeste. "
           "Responde en español claro y breve. No inventes estados, fechas, horas de llegada ni datos del suministro. "
           "Para consultar un reporte usa la herramienta consultar_reporte con el código. Para preparar un reporte usa "
           "proponer_reporte: nunca digas que el reporte está guardado hasta que la persona lo confirme.")


class ErrorHerramienta(ValueError):
    """Llamada de herramienta no válida (nombre, argumentos o resultado)."""


@dataclass
class RespuestaAsistente:
    texto: str
    origen: str                     # "gemini" | "reglas" | "reglas(fallback)"
    herramientas: List[str] = field(default_factory=list)
    borrador: Optional[str] = None  # reporte propuesto, pendiente de confirmación (nada guardado)
    aviso: Optional[str] = None     # explicación breve si Gemini no respondió


def _consultar_reporte(args: Dict[str, Any]) -> Dict[str, Any]:
    codigo = str(args.get("codigo", "")).strip().upper()
    if not CODIGO_RE.match(codigo):
        raise ErrorHerramienta("El código debe tener el formato HID-XXXXXXXX.")
    datos = workflow.consultar_reporte_publico(codigo)
    return {k: datos[k] for k in ("codigo", "estado", "estado_publico", "tiene_ubicacion", "n_fotos") if k in datos}


def _avisos(_args: Dict[str, Any]) -> Dict[str, Any]:
    avisos = workflow.avisos_publicos()[:5]
    return {"avisos": [{"titulo": a.get("titulo", ""), "mensaje": a.get("mensaje", "")} for a in avisos]}


def _proponer_reporte(args: Dict[str, Any]) -> Dict[str, Any]:
    texto = str(args.get("texto", "")).strip()
    if not (LARGO_MIN_REPORTE <= len(texto) <= LARGO_MAX_REPORTE):
        raise ErrorHerramienta(f"La descripción debe tener entre {LARGO_MIN_REPORTE} y {LARGO_MAX_REPORTE} caracteres.")
    return {"borrador": texto, "guardado": False,
            "mensaje": "Borrador preparado. Debe confirmarlo la persona antes de guardarse."}


HERRAMIENTAS: Dict[str, Dict[str, Any]] = {
    "consultar_reporte": {
        "descripcion": "Consulta el estado público de un reporte con su código de seguimiento HID-XXXXXXXX.",
        "parameters": {"type": "OBJECT", "properties": {"codigo": {"type": "STRING", "description": "Código HID-XXXXXXXX"}},
                       "required": ["codigo"]},
        "funcion": _consultar_reporte, "efecto": "lectura"},
    "avisos_publicos": {
        "descripcion": "Lista los avisos públicos vigentes para la comunidad.",
        "parameters": {"type": "OBJECT", "properties": {}},
        "funcion": _avisos, "efecto": "lectura"},
    "proponer_reporte": {
        "descripcion": "Prepara un borrador de reporte de falta de agua para que la persona lo confirme. NO lo guarda.",
        "parameters": {"type": "OBJECT", "properties": {"texto": {"type": "STRING", "description": "Descripción del problema"}},
                       "required": ["texto"]},
        "funcion": _proponer_reporte, "efecto": "confirmacion"},
}


def declaraciones() -> List[Dict[str, Any]]:
    return [{"name": n, "description": h["descripcion"], "parameters": h["parameters"]} for n, h in HERRAMIENTAS.items()]


def ejecutar_herramienta(nombre: str, args: Dict[str, Any], registro: List[str], borrador: List[str]) -> Dict[str, Any]:
    """Valida el nombre y los argumentos y ejecuta SOLO herramientas registradas."""
    if nombre not in HERRAMIENTAS:
        raise ErrorHerramienta(f"Herramienta no autorizada: {nombre}")
    if not isinstance(args, dict):
        raise ErrorHerramienta("Los argumentos deben ser un objeto.")
    requeridos = HERRAMIENTAS[nombre]["parameters"].get("required", [])
    for clave in requeridos:
        if clave not in args:
            raise ErrorHerramienta(f"Falta el argumento «{clave}».")
    registro.append(nombre)
    resultado = HERRAMIENTAS[nombre]["funcion"](args)
    if nombre == "proponer_reporte":
        borrador.append(resultado["borrador"])
    return resultado


def responder(pregunta: str, usar_gemini: Optional[bool] = None) -> RespuestaAsistente:
    """Respuesta del asistente. Nunca lanza por fallos de Gemini: degrada a reglas locales y lo declara."""
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return RespuestaAsistente(reglas_locales.responder("")[1], "reglas")
    if usar_gemini is None:
        usar_gemini = config.ai_provider() == "gemini"
    if not usar_gemini:
        return RespuestaAsistente(reglas_locales.responder(pregunta)[1], "reglas")
    registro: List[str] = []
    borrador: List[str] = []

    def ejecutor(nombre: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return ejecutar_herramienta(nombre, args, registro, borrador)
        except ErrorHerramienta as e:
            return {"error": str(e)}  # el modelo recibe el error real y puede corregirse

    try:
        texto, ejecutadas = ia_gemini.conversar(SISTEMA, pregunta, declaraciones(), ejecutor)
        return RespuestaAsistente(texto, "gemini", ejecutadas, borrador[-1] if borrador else None)
    except ia_gemini.ErrorIA as e:
        tema, texto = reglas_locales.responder(pregunta)
        return RespuestaAsistente(texto, "reglas(fallback)", [], None,
                                  aviso="Gemini no respondió en este momento; respuesta con reglas locales.")


def confirmar_reporte(texto: str, origen: str = "asistente-local", canal: str = "asistente") -> Dict[str, Any]:
    """Guarda el borrador tras la confirmación explícita de la persona. Única vía de guardado del asistente."""
    texto = (texto or "").strip()
    if not (LARGO_MIN_REPORTE <= len(texto) <= LARGO_MAX_REPORTE):
        raise ErrorHerramienta(f"La descripción debe tener entre {LARGO_MIN_REPORTE} y {LARGO_MAX_REPORTE} caracteres.")
    r = workflow.crear_reporte(texto, canal=canal, origen=origen)
    return {"codigo": r.codigo_seguimiento}
