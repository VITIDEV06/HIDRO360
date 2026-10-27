"""Webhook de Telegram: convierte los mensajes del bot en reportes ciudadanos.

Telegram llama por POST a /api/v1/telegram/webhook con la cabecera X-Telegram-Bot-Api-Secret-Token.
Sin el secreto correcto no se procesa nada. Con un mensaje válido respondemos siempre 200: si devolviéramos
error, Telegram reintentaría la misma actualización indefinidamente.
"""

import hmac
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request

import config
from services import audios as audios_srv
from services import fotos as fotos_srv
from services import telegram_api, workflow

router = APIRouter()


def _responder(chat_id: Any, texto: str) -> None:
    """Respuesta al chat: es best-effort. Si falla, el reporte ya quedó guardado."""
    try:
        telegram_api.enviar_mensaje(chat_id, texto)
    except Exception:
        pass


def _adjuntos(msg: Dict[str, Any]):
    """Descarga foto/voz/audio de Telegram con el tope del servidor. Devuelve (fotos, audios)."""
    fotos, audios = [], []
    if msg.get("photo"):
        mejor = max(msg["photo"], key=lambda f: f.get("file_size", 0))
        datos = telegram_api.descargar_archivo(mejor["file_id"], config.MAX_BYTES_FOTO)
        if datos:
            fotos.append({"bytes": datos})
    for clave in ("voice", "audio"):
        adj = msg.get(clave)
        if adj:
            datos = telegram_api.descargar_archivo(adj["file_id"], config.MAX_BYTES_AUDIO)
            if datos:
                audios.append({"bytes": datos, "duracion_s": adj.get("duration")})
    return fotos, audios


def _procesar(chat_id: Any, msg: Dict[str, Any]) -> None:
    origen = f"telegram:{chat_id}"
    texto = (msg.get("text") or msg.get("caption") or "").strip()
    ubicacion = msg.get("location") if isinstance(msg.get("location"), dict) else None
    fotos, audios = _adjuntos(msg)
    lat = ubicacion.get("latitude") if ubicacion else None
    lng = ubicacion.get("longitude") if ubicacion else None
    if not texto:
        if ubicacion:
            texto = "Ubicación compartida por Telegram."
        elif fotos:
            texto = "Foto enviada por Telegram."
        elif audios:
            texto = "Nota de voz enviada por Telegram."
        else:
            _responder(chat_id, "Escriba el problema, envíe una foto, una nota de voz o su ubicación.")
            return
    try:
        r = workflow.crear_reporte(texto, canal="telegram", origen=origen, lat=lat, lng=lng,
                                   fotos=fotos or None, audios=audios or None)
        aviso = ""
    except (fotos_srv.ErrorFoto, audios_srv.ErrorAudio, workflow.ErrorDeNegocio):
        # Si el adjunto no se puede guardar, el reporte de texto sí se registra y se avisa al ciudadano.
        r = workflow.crear_reporte(texto, canal="telegram", origen=origen, lat=lat, lng=lng)
        aviso = " El adjunto no se pudo guardar; describa el problema por texto si puede."
    _responder(chat_id, f"Reporte recibido. Código de seguimiento: {r.codigo_seguimiento}.{aviso}")


@router.post("/telegram/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    esperado = config.TELEGRAM_WEBHOOK_SECRET()
    if not esperado:
        raise HTTPException(503, "Telegram no está configurado en el servidor.")
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(x_telegram_bot_api_secret_token, esperado):
        raise HTTPException(403, "Secreto de webhook inválido.")
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}
    msg = update.get("message") or update.get("edited_message") if isinstance(update, dict) else None
    if not isinstance(msg, dict) or (msg.get("chat") or {}).get("id") is None:
        return {"ok": True}
    chat_id = msg["chat"]["id"]
    try:
        _procesar(chat_id, msg)
    except Exception:
        _responder(chat_id, "No pudimos registrar su mensaje. Intente de nuevo en unos minutos.")
    return {"ok": True}
