"""
Moderación determinista: señales de inconsistencia o posible exageración.

NO decide si un reporte es falso. Un reporte puede ser exagerado, incompleto o incluso inventado y aun así describir un
problema real (o al contrario). Por eso este módulo solo MARCA para revisión humana: produce una lista de frases
explicables (`senales_revision`) que un operador puede leer y contrastar. Ninguna señal descarta ni fusiona nada.

Reglas (deterministas, sin IA; cambian con VERSION_REGLAS):
  - Cifras extremas: personas afectadas u horas sin agua fuera de rango razonable.
  - Contradicción tipo↔severidad: p. ej. «baja presión» declarada como crítica.
  - Cifra mayor que la población estimada del sector (si el sector se reconoce).
  - Marcas textuales de broma/exageración («es broma», «jajaja», «miles de personas»).
  - Falta de información clave (varios campos de `datos_faltantes`).
  - Ubicación imprecisa (sin sector y sin referencia textual).
"""

import re
from typing import List, Optional, Sequence

from services.textutil import normalizar

# Frases que, por sí solas, no prueban nada: solo piden que una persona lea con cuidado.
_PATRONES_EXAGERACION = [
    (r"\b(es|era)\s+(una\s+)?broma\b|\bjajaja|\bjeje\b|\blol\b", "el mensaje parece una broma"),
    (r"\b(no es cierto|es mentira|invente|lo invente|puro cuento|chiste)\b", "el propio mensaje pone en duda su veracidad"),
    (r"\b(miles|millones)\s+de\s+(personas|familias|gente)\b", "habla de miles/millones de personas (cifra no verificable)"),
    (r"\btodo(s)?\s+el\s+(barrio|pueblo|mundo|sector|area)\b", "afirma que afecta a «todo el barrio» (generalización)"),
]
_PERSONAS_MUY_ALTA = 5000       # por encima de esto, una cifra concreta merece una mirada
_HORAS_MUY_ALTA = 24 * 30       # 30 días sin agua
_SEVERIDADES_ALTAS = ("alta", "crítica", "critica")


def _norm(v: Optional[str]) -> str:
    return normalizar(v or "")


def evaluar(texto: str, extraccion, poblacion_sector: Optional[int] = None) -> List[str]:
    """Devuelve señales legibles (posiblemente vacío). `extraccion` es un `models.schemas.Extraccion`.
    `poblacion_sector`: población estimada del sector reconocido, si se conoce (para comparar cifras)."""
    senales: List[str] = []
    t = normalizar(texto or "")

    for patron, frase in _PATRONES_EXAGERACION:
        if re.search(patron, t):
            senales.append(frase)

    personas = getattr(extraccion, "personas_afectadas", None)
    if personas is not None:
        if personas > 100000:
            senales.append("las personas afectadas declaradas superan cualquier rango razonable")
        elif personas > _PERSONAS_MUY_ALTA:
            senales.append("declara una cifra de personas afectadas muy alta: conviene contrastarla")
        if poblacion_sector and personas > poblacion_sector:
            senales.append("las personas afectadas superan la población estimada del sector")

    horas = getattr(extraccion, "horas_sin_agua", None)
    if horas is not None and horas > _HORAS_MUY_ALTA:
        senales.append("declara más de un mes sin agua: conviene contrastarlo")

    tipo = getattr(extraccion, "tipo_incidencia", None)
    sev = getattr(extraccion, "severidad", None)
    sev_val = getattr(sev, "value", sev)
    if tipo == "baja_presion" and _norm(sev_val) in _SEVERIDADES_ALTAS:
        senales.append("el tipo de incidencia (baja presión) no concuerda con la severidad declarada")
    if tipo is None and sev_val in _SEVERIDADES_ALTAS:
        senales.append("declara severidad alta sin describir el tipo de incidencia")

    faltantes: Sequence[str] = getattr(extraccion, "datos_faltantes", []) or []
    if len([f for f in faltantes if f in ("sector", "horas_sin_agua", "personas_afectadas", "severidad")]) >= 3:
        senales.append("faltan varios datos clave; el reporte está incompleto")

    if not getattr(extraccion, "sector", None) and not getattr(extraccion, "referencia_ubicacion", None):
        senales.append("ubicación imprecisa: no se reconoce sector ni referencia textual")

    # Deduplicar conservando el orden.
    vistas, salida = set(), []
    for s in senales:
        if s not in vistas:
            vistas.add(s)
            salida.append(s)
    return salida
