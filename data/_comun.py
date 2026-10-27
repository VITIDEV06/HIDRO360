"""
Piezas compartidas por los dos motores (SQLite y PostgreSQL): nombres válidos, claves y excepciones.
Nada aquí abre conexiones.
"""

from typing import Any, Dict, List

# Lista blanca de tablas: los nombres NUNCA se interpolan desde entrada externa sin pasar por aquí.
# tabla -> (columna de clave primaria, ¿la clave es entera autoincremental?)
CLAVES: Dict[str, tuple] = {
    "sectores": ("nombre", False),
    "cisternas": ("id", False),
    "reportes": ("id", True),
    "planes": ("id", True),
    "decisiones_duplicado": ("id", True),
    "auditoria": ("id", True),
    "usuarios": ("id", False),
    "sesiones": ("id", False),
    "fotos": ("id", False),
    "dispositivos": ("id", False),
    "misiones": ("id", True),
    "eventos_mision": ("id", True),
    "recomendaciones": ("id", True),
    "audios": ("id", False),
    "telegram_updates": ("id", False),
    "telegram_sesiones": ("id", False),
}

# `vaciar()` sin argumentos. `usuarios`, `sesiones`, `dispositivos`, `fotos`, `audios`, `telegram_updates` y
# `telegram_sesiones` NO están aquí: reiniciar la demo no debe borrar cuentas, sesiones, identidades de terminales,
# archivos subidos ni la idempotencia del webhook de Telegram. `limites` tampoco (se gestiona aparte).
# Misiones/eventos/recomendaciones sí se reinician con los datos de ejemplo.
TABLAS: List[str] = ["sectores", "cisternas", "reportes", "planes", "decisiones_duplicado", "auditoria",
                     "misiones", "eventos_mision", "recomendaciones"]


class ErrorBaseDeDatos(RuntimeError):
    """Error de persistencia traducido del motor. Su mensaje NUNCA contiene la cadena de conexión, usuarios,
    servidores ni contraseñas. `codigo` es el SQLSTATE de PostgreSQL cuando existe."""

    def __init__(self, mensaje: str, codigo: str = ""):
        super().__init__(mensaje)
        self.codigo = codigo


class ErrorConexionBD(ErrorBaseDeDatos):
    """No se pudo conectar (red, autenticación, tiempo agotado, SSL) o se perdió la conexión."""


class ErrorIntegridadBD(ErrorBaseDeDatos):
    """Violación de una restricción (única, clave foránea, CHECK, auditoría inmutable)."""


class EsquemaNoAplicado(ErrorBaseDeDatos):
    """Faltan las migraciones de supabase/migrations: `python -m scripts.aplicar_migraciones`."""


def clave_de(tabla: str) -> tuple:
    try:
        return CLAVES[tabla]
    except KeyError:
        raise ValueError(f"Tabla no permitida: {tabla!r}") from None


def columna_clave(tabla: str) -> str:
    return clave_de(tabla)[0]


def validar_tabla(tabla: str) -> str:
    clave_de(tabla)
    return tabla


def normalizar_clave(tabla: str, clave: Any) -> Any:
    """Convierte la clave al tipo de la columna (texto o entero). Devuelve None si una clave entera no es válida."""
    _, entera = clave_de(tabla)
    if entera:
        try:
            return int(clave)
        except (TypeError, ValueError):
            return None
    return None if clave is None else str(clave)


def validar_vaciable(tabla: str) -> str:
    """Tablas que `vaciar()` acepta: las de documento más `limites` (contadores de tasa, sin documento)."""
    if tabla != "limites":
        clave_de(tabla)
    return tabla


def es_entera(tabla: str) -> bool:
    return True if tabla == "limites" else clave_de(tabla)[1]
