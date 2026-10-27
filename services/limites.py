"""
Límites de tasa y bloqueos TEMPORALES persistidos en la base (válidos entre instancias serverless).

Por qué: en Vercel cada petición puede ejecutarse en una instancia distinta y las instancias se destruyen sin
aviso; un contador en memoria no limita nada. Todo el estado vive en la base:

- Ventana deslizante: `db.registrar_evento_limite(clave, ventana_s)` inserta un evento y devuelve cuántos hay en la
  ventana (atómico). Un evento se cuenta aunque la petición se rechace: insistir NO libera el límite.
- Bloqueo: tras N fallos se guarda un marcador con vencimiento (tabla `sesiones`, id `bloqueo:<sha256(clave)>`; nunca
  coincide con una huella de token, que son 64 hex). Mientras exista y no haya vencido, `bloqueado()` es verdadero.
  Un acierto lo borra (`perdonar`). Los eventos de fallo previos permanecen hasta que su ventana vence.
- Las claves se guardan con hash (no se almacenan IP ni nombres de usuario en claro en el marcador).

No hay estado en el módulo. Mantenimiento: `limpiar` borra eventos viejos de forma oportunista.
"""

import hashlib
import random
import time
from typing import Optional

from data import database as db

LIMPIEZA_PROBABILIDAD = 0.02       # 1 de cada ~50 llamadas purga eventos con más de una hora
LIMPIEZA_ANTIGUEDAD_S = 3600


class ErrorLimite(ValueError):
    """Demasiadas solicitudes o intentos desde el mismo origen (→ 429 en la API)."""


def _huella(clave: str) -> str:
    return hashlib.sha256((clave or "").encode("utf-8")).hexdigest()


def _limpiar_a_veces() -> None:
    if random.random() < LIMPIEZA_PROBABILIDAD:
        try:
            db.limpiar_limites(LIMPIEZA_ANTIGUEDAD_S)
        except Exception:        # la limpieza nunca debe romper una petición
            pass


def limitar(clave: str, maximo: int, ventana_s: float,
            mensaje: str = "Demasiadas solicitudes. Espere un minuto e inténtelo de nuevo.") -> int:
    """Registra un evento y rechaza si en la ventana ya hay más de `maximo`. Devuelve el conteo."""
    n = int(db.registrar_evento_limite(clave, int(ventana_s)))
    _limpiar_a_veces()
    if n > maximo:
        raise ErrorLimite(mensaje)
    return n


def _id_bloqueo(clave: str) -> str:
    return "bloqueo:" + _huella(clave)


def bloqueado(clave: str) -> bool:
    d = db.obtener("sesiones", _id_bloqueo(clave))
    if not d:
        return False
    if float(d.get("expira", 0)) <= time.time():
        db.borrar("sesiones", _id_bloqueo(clave))
        return False
    return True


def fallo(clave: str, maximo: int, ventana_s: float, bloqueo_s: Optional[float] = None) -> int:
    """Registra un fallo; al llegar a `maximo` en la ventana bloquea `bloqueo_s` (por defecto la ventana)."""
    n = int(db.registrar_evento_limite("fallo:" + clave, int(ventana_s)))
    _limpiar_a_veces()
    if n >= maximo:
        db.insertar("sesiones", {"id": _id_bloqueo(clave), "tipo": "bloqueo", "expira": time.time() + (bloqueo_s or ventana_s),
                                 "creada": time.time()}, clave=_id_bloqueo(clave))
    return n


def perdonar(clave: str) -> None:
    """Un acierto levanta el bloqueo (los fallos antiguos vencen solos con su ventana)."""
    db.borrar("sesiones", _id_bloqueo(clave))
