"""
Tareas de IA de apoyo (opcionales). Gemini REDACTA; el código valida y decide qué se muestra.

  - explicar_prioridad: resumen en lenguaje natural de un puntaje YA calculado por reglas. No envía
    texto de ciudadanos: solo sector, puntaje y factores. Se rechaza si introduce cifras que no estaban
    en la entrada.
  - borrador_comunicacion: borrador informativo para un sector. Se rechaza si promete hora de llegada o
    supera el largo máximo; el aviso «BORRADOR» lo añade siempre el código.
  - recomendar_respuesta: texto breve de ACCIÓN SUGERIDA para un reporte analizado (orquestador). Salida con
    esquema estricto; se descarta si inventa sector/cisterna, introduce cifras ajenas a los datos, incluye
    datos personales o promete hora. Origen: "gemini" | "reglas" | "reglas(fallback)".
Si Gemini no está configurado o falla, se devuelve el texto local y `origen` lo dice ("local"). Nunca se
presenta texto local como si fuera de Gemini. Ninguna de estas tareas aprueba, envía ni asigna nada.
"""

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

import config
from models.schemas import PrioridadSector
from services import communication, ia_gemini, tracing
from services.textutil import normalizar

PROMESA_HORA = re.compile(r"\b\d{1,2}[:h]\d{2}\b|llegar[aá]\s+(a las|en|hoy|mañana)|llegaremos|en \d+ (minutos|horas)", re.I)

# Para resúmenes del operador, que citan fechas/horas pasadas («en 72 horas», «a las 18:00»): solo se
# rechaza lo que anuncia una llegada o envío futuro con hora/plazo.
PROMESA_FUTURA = re.compile(r"\b(llegar[aá]n?|llegaremos|arribar[aá]n?|estar[aá]n? (all[ií]|ah[ií]|en)|se enviar[aá]n?|enviaremos)\b"
                            r".{0,60}?(\b\d{1,2}[:h]\d{2}\b|\ba las \d|\ben \d+ (minutos|horas)|\bhoy\b|\bma[ñn]ana\b)", re.I)

_SISTEMA_RESUMEN = ("Redactas, en español claro y en máximo 3 frases, por qué un sector tiene cierta prioridad de "
                    "atención de agua. Usa SOLO los datos del JSON recibido; no inventes cifras ni causas. "
                    "Devuelve un JSON {\"resumen\": \"...\"}.")
_SISTEMA_BORRADOR = ("Redactas un borrador breve (máx. 4 frases) de aviso informativo para una comunidad sin agua. "
                     "Tono claro y respetuoso. NO prometas ninguna hora ni fecha de llegada; di que se informará cuando "
                     "esté confirmada. Usa SOLO los datos del JSON recibido. Devuelve un JSON {\"texto\": \"...\"}.")


def _usar_gemini() -> bool:
    return config.ai_provider() == "gemini"


def _numeros(texto: str) -> set:
    return set(re.findall(r"\d+(?:[.,]\d+)?", texto))


def explicar_prioridad(p: PrioridadSector) -> Dict[str, str]:
    local = " ".join(p.razones)
    if not _usar_gemini():
        return {"texto": local, "origen": "local"}
    entrada = json.dumps({"sector": p.sector, "puntaje": p.puntaje, "categoria": p.categoria,
                          "factores": [{"nombre": f.nombre, "puntos": f.contribucion, "explicacion": f.explicacion}
                                       for f in p.factores], "reportes": p.reportes_contados}, ensure_ascii=False)
    try:
        resumen = str(json.loads(ia_gemini.generar_json(_SISTEMA_RESUMEN, entrada, tarea="resumen"))["resumen"]).strip()
        if not resumen or len(resumen) > 600:
            raise ia_gemini.ErrorIA("resumen vacío o demasiado largo")
        if not _numeros(resumen) <= _numeros(entrada):
            raise ia_gemini.ErrorIA("el resumen introduce cifras que no estaban en los datos")
        return {"texto": resumen, "origen": "gemini"}
    except Exception as e:
        tracing.emitir("ia", "explicar_prioridad", "fallback", {"sector": p.sector}, {"motivo": str(e)[:120]})
        return {"texto": local, "origen": "local (Gemini no disponible)"}


def borrador_comunicacion(sector: str, litros: int, cisternas: List[str]) -> Dict[str, str]:
    local = communication.borrador_asignacion(sector, litros, cisternas)
    if not _usar_gemini():
        return {"texto": local, "origen": "local"}
    entrada = json.dumps({"sector": sector, "litros_previstos": litros, "cisternas": cisternas}, ensure_ascii=False)
    try:
        texto = str(json.loads(ia_gemini.generar_json(_SISTEMA_BORRADOR, entrada, tarea="borrador"))["texto"]).strip()
        if not texto or len(texto) > 700:
            raise ia_gemini.ErrorIA("borrador vacío o demasiado largo")
        if PROMESA_HORA.search(texto):
            raise ia_gemini.ErrorIA("el borrador promete una hora de llegada")
        if not _numeros(texto) <= _numeros(entrada):
            raise ia_gemini.ErrorIA("el borrador introduce cifras que no estaban en los datos")
        return {"texto": texto + "\n\n" + communication.AVISO, "origen": "gemini"}
    except Exception as e:
        tracing.emitir("ia", "borrador_comunicacion", "fallback", {"sector": sector}, {"motivo": str(e)[:120]})
        return {"texto": local, "origen": "local (Gemini no disponible)"}


# ------------------------------------------------------- recomendación de respuesta (orquestador)
_SISTEMA_RECOMENDACION = (
    "Redactas, en español claro y en máximo 3 frases, la ACCIÓN SUGERIDA para atender un reporte de falta de agua. "
    "Usa SOLO los datos del JSON recibido (sector, prioridad, categoría, horas, personas, duplicados). "
    "Escribe las cifras tal cual aparecen en los datos (no conviertas horas a días ni calcules nada). "
    "No menciones otros sectores, cisternas, litros ni cifras que no estén en los datos; no incluyas datos "
    "personales; no prometas hora ni fecha de llegada; la decisión final es de una persona. "
    "Devuelve únicamente un JSON {\"accion\": \"...\"}."
)
_RE_CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_TEL = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_RE_ID_RECURSO = re.compile(r"\b[A-Z]{2,5}-\d{1,}\b")


class SalidaRecomendacion(BaseModel):
    """Esquema ESTRICTO de lo aceptado del modelo: un único campo; cualquier extra se rechaza."""
    model_config = {"extra": "forbid"}
    accion: str = Field(min_length=10, max_length=500)


class SalidaResumen(BaseModel):
    """Esquema ESTRICTO de los resúmenes para el operador (contexto de incidencia, patrones)."""
    model_config = {"extra": "forbid"}
    resumen: str = Field(min_length=10, max_length=700)


def _canon_numeros(texto: str) -> set:
    salida = set()
    for n in re.findall(r"\d+(?:[.,]\d+)?", texto):
        try:
            salida.add(f"{float(n.replace(',', '.')):g}")
        except ValueError:
            pass
    return salida


def validar_recomendacion(texto: str, datos: Dict[str, Any], sectores: List[str], cisternas: List[str],
                          maximo: int = 500, permitidos: Optional[List[str]] = None,
                          horas_pasadas_ok: bool = False) -> str:
    """Revalida con código lo que redactó el modelo. Lanza ErrorIA si no es seguro mostrarlo.
    `permitidos`: sectores que sí pueden nombrarse además de `datos["sector"]` (están en los datos enviados)."""
    t = (texto or "").strip()
    if not t or len(t) > maximo:
        raise ia_gemini.ErrorIA("recomendación vacía o demasiado larga")
    if _RE_CORREO.search(t) or _RE_TEL.search(t):
        raise ia_gemini.ErrorIA("la recomendación contiene datos personales")
    if (PROMESA_FUTURA if horas_pasadas_ok else PROMESA_HORA).search(t):
        raise ia_gemini.ErrorIA("la recomendación promete una hora de llegada")
    if not _canon_numeros(t) <= _canon_numeros(json.dumps(datos, ensure_ascii=False)):
        raise ia_gemini.ErrorIA("la recomendación introduce cifras que no estaban en los datos")
    nt = normalizar(t)
    propios = {normalizar(str(datos.get("sector") or ""))} | {normalizar(p) for p in (permitidos or [])}
    propios.discard("")
    resto = nt
    for propio in sorted(propios, key=len, reverse=True):
        resto = resto.replace(propio, " ")
    for s in sectores:
        ns = normalizar(s)
        if ns and ns not in propios and re.search(rf"\b{re.escape(ns)}\b", resto):
            raise ia_gemini.ErrorIA(f"la recomendación menciona un sector no incluido en los datos ({s})")
    if any(c.lower() in t.lower() for c in cisternas) or _RE_ID_RECURSO.search(t):
        raise ia_gemini.ErrorIA("la recomendación menciona una cisterna que no está en sus datos")
    return t


def recomendacion_local(datos: Dict[str, Any]) -> str:
    """Texto determinista (sin IA) de acción sugerida a partir de los datos ya calculados."""
    partes = []
    if datos.get("sector"):
        det = []
        if datos.get("categoria") is not None and datos.get("prioridad") is not None:
            det.append(f"prioridad {datos['categoria']} ({datos['prioridad']:g} puntos)")
        if datos.get("personas_afectadas") is not None:
            det.append(f"{datos['personas_afectadas']} personas afectadas")
        if datos.get("horas_sin_agua") is not None:
            det.append(f"{datos['horas_sin_agua']:g} h sin agua")
        partes.append(f"Atender {datos['sector']}" + (f": {', '.join(det)}." if det else "."))
    else:
        partes.append("El reporte no tiene sector confirmado: una persona debe elegirlo del catálogo antes de asignar recursos.")
    if datos.get("posibles_duplicados"):
        partes.append(f"Revisar {datos['posibles_duplicados']} posible(s) duplicado(s) antes de asignar.")
    partes.append("Revisar la propuesta de asignación y decidir; la hora de llegada aún no está confirmada.")
    return " ".join(partes)


def recomendar_respuesta(datos: Dict[str, Any], sectores: List[str], cisternas: List[str]) -> Dict[str, Optional[str]]:
    """Devuelve {"texto", "origen", "motivo_fallback"}. `origen` es la procedencia REAL del texto."""
    local = recomendacion_local(datos)
    if not _usar_gemini():
        return {"texto": local, "origen": "reglas", "motivo_fallback": None}
    try:
        bruto = ia_gemini.generar_json(_SISTEMA_RECOMENDACION, json.dumps(datos, ensure_ascii=False), tarea="recomendar")
        try:
            salida = SalidaRecomendacion(**json.loads(bruto))
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            raise ia_gemini.ErrorIA(f"salida inválida: {type(e).__name__}") from e
        texto = validar_recomendacion(salida.accion, datos, sectores, cisternas)
        return {"texto": texto, "origen": "gemini", "motivo_fallback": None}
    except Exception as e:
        motivo = f"{type(e).__name__}: {e}"[:160]
        tracing.emitir("ia", "recomendar_respuesta", "fallback", {"sector": datos.get("sector")}, {"motivo": motivo})
        return {"texto": local, "origen": "reglas(fallback)", "motivo_fallback": motivo}
