"""
Cliente mínimo de la API de Telegram Bot (https://core.telegram.org/bots/api), por httpx.

- Sin dependencias nuevas ni SDK: solo las llamadas que HIDRO360 necesita (sendMessage, getFile y la descarga del
  archivo por `https://api.telegram.org/file/bot<token>/<file_path>`).
- El token del bot es un SECRETO: se lee de `config.TELEGRAM_BOT_TOKEN()` (entorno) y jamás se registra, se devuelve
  ni se incluye en mensajes de error. Sin token, `configurado()` es falso y no se llama a la red.
- Transporte HTTP inyectable (`_transport`) para pruebas con `httpx.MockTransport`; nada de red en pytest.
- Nunca lanza por un fallo de red: devuelve False/None (quien llama decide si reintentar). Un fallo al responder NO
  debe provocar que Telegram reintente un update ya aplicado: la idempotencia vive en services/telegram.py.

Aviso: el contenido que envía el ciudadano (texto, archivos) es DATO no confiable. Nada de lo que llegue por este
cliente se ejecuta ni se interpreta como instrucción; el texto se interpreta con reglas, como cualquier otro canal.
"""

import re
from typing import Any, Dict, Optional

import httpx

import config

TIMEOUT_S = 20.0
LARGO_MAX_MENSAJE = 4096           # límite de Telegram para sendMessage
# Transporte HTTP inyectable (solo pruebas, httpx.MockTransport). None en producción.
_transport: Optional[Any] = None

TECLADO_UBICACION = {"keyboard": [[{"text": "📍 Compartir ubicación", "request_location": True}]],
                     "resize_keyboard": True, "one_time_keyboard": True}
TECLADO_QUITAR = {"remove_keyboard": True}


class ErrorTelegram(ValueError):
    """La integración con Telegram no está configurada (falta el token del bot)."""


def configurado() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN())


def _base() -> str:
    token = config.TELEGRAM_BOT_TOKEN()
    if not token:
        raise ErrorTelegram("La integración con Telegram no está configurada (falte TELEGRAM_BOT_TOKEN).")
    return f"{config.TELEGRAM_API_BASE().rstrip('/')}/bot{token}"


def _llamar(metodo: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """POST a un método del Bot API. Devuelve el objeto `result` o None ante cualquier fallo (sin filtrar detalles)."""
    try:
        with httpx.Client(timeout=TIMEOUT_S, transport=_transport) as cli:
            r = cli.post(f"{_base()}/{metodo}", json=payload or {})
        if r.status_code != 200:
            return None
        datos = r.json()
        return datos.get("result") if isinstance(datos, dict) and datos.get("ok") else None
    except (httpx.HTTPError, ValueError, ErrorTelegram):
        return None


def enviar_mensaje(chat_id: Any, texto: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    """Envía un mensaje de texto (recortado al límite de Telegram). True si Telegram lo aceptó."""
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": (texto or "")[:LARGO_MAX_MENSAJE],
                               "disable_web_page_preview": True}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _llamar("sendMessage", payload) is not None


def obtener_archivo(file_id: str) -> Optional[str]:
    """`getFile`: devuelve la ruta temporal del archivo (`file_path`) o None."""
    res = _llamar("getFile", {"file_id": file_id})
    return res.get("file_path") if isinstance(res, dict) and isinstance(res.get("file_path"), str) else None


def descargar(file_path: str, max_bytes: int) -> Optional[bytes]:
    """Descarga el archivo por su `file_path`. Rechaza rutas extrañas y respeta `max_bytes` (por si acaso)."""
    if not isinstance(file_path, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,255}", file_path) or ".." in file_path:
        return None
    try:
        token = config.TELEGRAM_BOT_TOKEN()
        if not token:
            return None
        url = f"{config.TELEGRAM_API_BASE().rstrip('/')}/file/bot{token}/{file_path}"
        with httpx.Client(timeout=TIMEOUT_S, transport=_transport) as cli:
            r = cli.get(url)
        if r.status_code != 200:
            return None
        datos = r.content
        return datos if 0 < len(datos) <= max_bytes else None
    except (httpx.HTTPError, ValueError):
        return None


def descargar_archivo(file_id: str, max_bytes: int) -> Optional[bytes]:
    """getFile + descarga. Devuelve los bytes o None (archivo inexistente, demasiado grande o fallo de red)."""
    file_path = obtener_archivo(file_id)
    if not file_path:
        return None
    return descargar(file_path, max_bytes)


def registrar_webhook(url: str, secret: str, *, drop_pending: bool = False) -> bool:
    """`setWebhook` con el `secret_token` que Telegram reenviará en la cabecera. Para scripts de configuración."""
    payload = {"url": url, "secret_token": secret, "allowed_updates": ["message", "edited_message", "callback_query"],
               "drop_pending_updates": bool(drop_pending)}
    return _llamar("setWebhook", payload) is not None


def borrar_webhook() -> bool:
    return _llamar("deleteWebhook", {"drop_pending_updates": False}) is not None


def informacion_webhook() -> Optional[Dict[str, Any]]:
    res = _llamar("getWebhookInfo")
    return res if isinstance(res, dict) else None
