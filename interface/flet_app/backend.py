"""Puente fino entre las páginas y `services.workflow` / `services.workflow_ia`.

No contiene reglas de negocio: solo localiza la función por nombre en el momento de llamar (para poder sustituirla en
pruebas y para dar un mensaje claro si una función del servidor aún no existe) y pasa únicamente los parámetros que
la función acepta. Las páginas siguen llamando SIEMPRE mediante `Ctx.ejecutar`."""

import inspect
from typing import Any, Callable, Optional, Tuple

from services import workflow
from services.workflow import ErrorDeNegocio

try:
    from services import workflow_ia
except Exception:   # el módulo de IA lo implementa otro componente; sin él la página lo dice
    workflow_ia = None


def funcion(nombre: str) -> Callable:
    for modulo in (workflow, workflow_ia):
        f = getattr(modulo, nombre, None) if modulo is not None else None
        if callable(f):
            return f
    raise ErrorDeNegocio(f"Esta función del servidor todavía no está disponible ({nombre}).")


def existe(nombre: str) -> bool:
    try:
        funcion(nombre)
        return True
    except ErrorDeNegocio:
        return False


def llamar(nombre: str, *args, **kw) -> Any:
    """Llama a la función del servidor; descarta los argumentos con nombre que esa versión aún no acepta si valen None/vacío."""
    f = funcion(nombre)
    try:
        params = inspect.signature(f).parameters
    except (TypeError, ValueError):
        return f(*args, **kw)
    if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
        for k in list(kw):
            if k not in params:
                if kw[k] in (None, "", [], ()):
                    kw.pop(k)
                else:
                    raise ErrorDeNegocio(f"Esta versión del servidor aún no admite «{k}».")
    return f(*args, **kw)


def leer(nombre: str, por_defecto: Any = None, *args, **kw) -> Tuple[Any, Optional[str]]:
    """Lectura para construir una página: devuelve (valor, mensaje_de_error). Nunca lanza por falta de la función o de datos."""
    try:
        return llamar(nombre, *args, **kw), None
    except ErrorDeNegocio as e:
        return por_defecto, str(e)
    except Exception as e:   # permisos u otros: la página lo muestra, no se cae
        return por_defecto, f"{type(e).__name__}: {e}"
