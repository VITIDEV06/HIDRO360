"""Utilidades de texto compartidas."""

import re
import unicodedata

STOPWORDS = set(
    "el la los las un una unos unas de del en y o a con sin por para que se no es al lo su sus "
    "hay hace desde hasta muy mas como esta estan estamos llevamos tenemos agua dias dia horas hora".split()
)


def normalizar(texto: str) -> str:
    """minúsculas y sin acentos (para comparar)."""
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def palabras_clave(texto: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", normalizar(texto)) if w not in STOPWORDS and len(w) > 2}
