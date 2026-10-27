"""Configuración e integraciones: parámetros vigentes (solo lectura) y estado de IA y tokens de la API."""

import flet as ft

import config
from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import ia_gemini


def build(ctx) -> ft.Control:
    est = ia_gemini.estado()
    ia = ft.Column([
        ft.Row([ft.Text("Proveedor de IA:", size=13), C.pill("Gemini" if est["proveedor"] == "gemini" else "Local (reglas)", "info" if est["proveedor"] == "gemini" else "neutro"),
                ft.Text("Clave:", size=13), C.pill("configurada" if est["clave_configurada"] else "no configurada", "ok" if est["clave_configurada"] else "warn")], spacing=10, wrap=True),
        ft.Text(f"Modelo: {est['modelo']} · tiempo máximo {config.AI_TIMEOUT_SECONDS()} s · reintentos {config.AI_MAX_REINTENTOS()}", size=12.5, color=T.MUTED),
        ft.Row([C.boton("Ir a verificar Gemini", lambda e: ctx.ir("gemini"), ft.Icons.AUTO_AWESOME, "secundario")]),
        ft.Text("Se configura con variables de entorno o el archivo .env (AI_PROVIDER, GEMINI_API_KEY, GEMINI_MODEL). Sin clave el sistema usa el modo local. "
                "Antes de enviar texto a IA se ocultan teléfonos y correos.", size=11.5, color=T.MUTED)], spacing=10)
    api = ft.Row([ft.Text("Token de operador de la API:", size=13), C.pill("configurado" if config.API_TOKEN() else "sin configurar", "ok" if config.API_TOKEN() else "warn"),
                  ft.Text("Token de dispositivo:", size=13), C.pill("configurado" if config.DEVICE_TOKEN() else "sin configurar", "ok" if config.DEVICE_TOKEN() else "warn")],
                 spacing=10, wrap=True)

    params = C.tabla(["Parámetro", "Valor"], [
        ["Versión de reglas", config.VERSION_REGLAS],
        ["Pesos horas / población / vulnerabilidad", f"{config.PESO_HORAS} / {config.PESO_POBLACION} / {config.PESO_VULNERABILIDAD}"],
        ["Topes horas / personas", f"{config.HORAS_TOPE:g} h / {config.PERSONAS_TOPE}"], ["Litros por persona", config.LITROS_POR_PERSONA],
        ["Umbral de duplicados", config.UMBRAL_DUPLICADO], ["Ventana de duplicados", f"{config.VENTANA_DUPLICADO_HORAS} h"],
        ["Base de datos", str(config.DB_PATH)], ["Archivo de trazas", str(config.TRACE_PATH)]])
    return C.seccion(C.titulo("Configuración", "Parámetros deterministas de demostración (no son política oficial) y estado de la IA"),
                     C.panel(ia, "Inteligencia artificial"), C.panel(api, "Integraciones (API)"), C.panel(params, "Parámetros (se cambian en config.py)"),
                     C.nota("Sesiones en memoria (se pierden al reiniciar), sin recuperación de contraseña ni segundo factor, y sin HTTPS propio: use una red de confianza.", "warn"))
