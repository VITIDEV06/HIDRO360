"""Acceso del portal público a los datos: SOLO funciones públicas de services.workflow (sin actor).
Normaliza lo que devuelven y nunca expone ids internos, hashes ni textos de otras personas."""

from typing import Any, Dict, List, Optional

from services import auth, workflow


def _cadena(d: dict, claves) -> str:
    for k in claves:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _aviso(x: Any) -> Optional[Dict[str, str]]:
    if isinstance(x, str):
        return {"titulo": "", "mensaje": x.strip()} if x.strip() else None
    if isinstance(x, dict):
        titulo = _cadena(x, ("titulo", "título", "title"))
        mensaje = _cadena(x, ("mensaje", "texto", "descripcion", "descripción", "detalle", "resumen"))
        if not mensaje:
            titulo, mensaje = "", titulo or _cadena(x, ("sector",))
        if not mensaje:
            return None
        return {"titulo": titulo, "mensaje": mensaje, "sector": _cadena(x, ("sector",)), "nivel": _cadena(x, ("nivel", "tipo", "estado"))}
    return None


def avisos() -> List[Dict[str, str]]:
    """Avisos comprensibles para la ciudadanía (lista vacía si no hay o si la función aún no existe)."""
    fn = getattr(workflow, "avisos_publicos", None)
    if fn is None:
        return []
    try:
        with auth.como(None):
            bruto = fn()
    except Exception:
        return []
    if isinstance(bruto, dict):
        elementos: list = []
        lista = bruto.get("avisos")
        if isinstance(lista, (list, tuple)):
            elementos = list(lista)
        else:
            elementos = [v for v in bruto.values() if isinstance(v, (list, tuple))]
            elementos = [i for sub in elementos for i in sub] or [bruto]
    elif isinstance(bruto, (list, tuple)):
        elementos = list(bruto)
    else:
        elementos = []
    return [a for a in (_aviso(e) for e in elementos) if a]


def fecha_legible(valor: Any) -> str:
    s = str(valor or "")
    return s.replace("T", " ")[:16] if s else ""
