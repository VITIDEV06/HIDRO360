"""Bot de Telegram en local (sondeo largo, long polling). No necesita webhook ni dominio público.

Ejecutar en una segunda terminal de PowerShell, con el mismo proyecto:
    .venv\\Scripts\\python -m scripts.bot_telegram

Requiere TELEGRAM_BOT_TOKEN (en .env o en el entorno) e Internet para hablar con Telegram.
Usa la MISMA base SQLite que la interfaz (HIDRO360_BD=sqlite, HIDRO360_DB) y las mismas funciones de negocio.
Los flujos de conversación viven en memoria: si el bot se reinicia, se cancelan las conversaciones a medias.
"""

import re
import sys
import time

import config
from services import asistente_ia, telegram_api, workflow

CODIGO_RE = re.compile(r"\bHID-[A-Z0-9]{8}\b")
AFIRMA = {"si", "sí", "s", "yes"}
NIEGA = {"no", "n"}
PAUSA_ERROR_S = 5

# chat_id -> {"paso": "descripcion" | "confirmar" | "codigo", "texto": str | None}
conversaciones: dict = {}

AYUDA = ("HIDRO360 · asistente de falta de agua\n\n"
         "/reportar - registrar una falta de agua (paso a paso, con confirmación)\n"
         "/consultar - ver el estado de su reporte con su código HID-XXXXXXXX\n"
         "/ayuda - mostrar este menú\n\n"
         "También puede escribir su pregunta y le orientaremos.")


def _texto_del_mensaje(msg: dict) -> str:
    return (msg.get("text") or "").strip()


def _consultar(codigo: str) -> str:
    try:
        d = workflow.consultar_reporte_publico(codigo.upper())
    except Exception:
        return "No encontramos un reporte con ese código. Revise que sea HID-XXXXXXXX."
    return (f"Código {d['codigo']}\nEstado: {d['estado_publico']}\n"
            f"Ubicación: {'indicada' if d.get('tiene_ubicacion') else 'sin ubicación'}. "
            "No indicamos hora de llegada del servicio.")


def procesar_mensaje(msg: dict, enviar=telegram_api.enviar_mensaje) -> None:
    """Procesa UN mensaje de Telegram y responde con `enviar(chat_id, texto)`. Inyectable para las pruebas."""
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return
    texto = _texto_del_mensaje(msg)
    bajo = texto.lower()
    estado = conversaciones.get(chat)

    if not texto:
        enviar(chat, "Por ahora solo puedo leer texto. Use /reportar para describir el problema.")
        return
    if bajo.startswith("/start") or bajo.startswith("/ayuda"):
        conversaciones.pop(chat, None)
        enviar(chat, AYUDA)
        return
    if bajo.startswith("/reportar"):
        conversaciones[chat] = {"paso": "descripcion", "texto": None}
        enviar(chat, "Describa qué pasa y desde cuándo falta el agua (entre 10 y 1500 caracteres).")
        return
    if bajo.startswith("/consultar"):
        resto = texto[len("/consultar"):].strip()
        if CODIGO_RE.search(resto.upper()):
            conversaciones.pop(chat, None)
            enviar(chat, _consultar(CODIGO_RE.search(resto.upper()).group(0)))
        else:
            conversaciones[chat] = {"paso": "codigo", "texto": None}
            enviar(chat, "Escriba su código de seguimiento (formato HID-XXXXXXXX).")
        return

    if estado and estado["paso"] == "codigo":
        conversaciones.pop(chat, None)
        m = CODIGO_RE.search(texto.upper())
        enviar(chat, _consultar(m.group(0)) if m else "Ese código no tiene el formato HID-XXXXXXXX.")
        return
    if estado and estado["paso"] == "descripcion":
        if not (asistente_ia.LARGO_MIN_REPORTE <= len(texto) <= asistente_ia.LARGO_MAX_REPORTE):
            enviar(chat, "La descripción debe tener entre 10 y 1500 caracteres. Inténtelo de nuevo.")
            return
        conversaciones[chat] = {"paso": "confirmar", "texto": texto}
        enviar(chat, f"Vamos a registrar este reporte:\n\n«{texto}»\n\n¿Lo guardo? Responda SI para guardar o NO para cancelar.")
        return
    if estado and estado["paso"] == "confirmar":
        if bajo in AFIRMA:
            conversaciones.pop(chat, None)
            r = asistente_ia.confirmar_reporte(estado["texto"], origen=f"telegram:{chat}", canal="telegram")
            enviar(chat, f"Reporte registrado. Código de seguimiento: {r['codigo']}. Guárdelo para consultar su estado.")
        elif bajo in NIEGA:
            conversaciones.pop(chat, None)
            enviar(chat, "Reporte cancelado. No se guardó nada.")
        else:
            enviar(chat, "Responda SI para guardar o NO para cancelar.")
        return

    # Texto libre: asistente (Gemini si está configurado; si no, reglas locales).
    r = asistente_ia.responder(texto)
    if r.borrador:
        conversaciones[chat] = {"paso": "confirmar", "texto": r.borrador}
        enviar(chat, f"{r.texto}\n\nBorrador preparado:\n«{r.borrador}»\n¿Lo guardo? Responda SI o NO.")
        return
    extra = f"\n\n({r.aviso})" if r.aviso else ""
    enviar(chat, r.texto + extra)


def ejecutar() -> int:
    if not config.TELEGRAM_BOT_TOKEN():
        print("Falta TELEGRAM_BOT_TOKEN. Defínala en .env o en esta terminal (ver docs/DESPLIEGUE.md).")
        return 1
    telegram_api._llamar("deleteWebhook", {"drop_pending_updates": False})  # el sondeo y el webhook no conviven
    print("Bot de Telegram en local iniciado (sondeo). Ctrl+C para detenerlo.")
    offset = None
    while True:
        try:
            payload = {"timeout": 10, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            updates = telegram_api._llamar("getUpdates", payload)
            if updates is None:
                print("Sin respuesta de Telegram; reintento en unos segundos.")
                time.sleep(PAUSA_ERROR_S)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message")
                if not msg:
                    continue
                try:
                    procesar_mensaje(msg)
                except Exception as e:  # nunca se detiene el bot por un mensaje; no se registra el contenido
                    print(f"Error procesando un mensaje: {type(e).__name__}")
                    try:
                        telegram_api.enviar_mensaje(msg["chat"]["id"], "Ocurrió un problema. Intente de nuevo en unos minutos.")
                    except Exception:
                        pass
        except KeyboardInterrupt:
            print("Bot detenido.")
            return 0


if __name__ == "__main__":
    sys.exit(ejecutar())
