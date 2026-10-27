"""Diagnóstico: verificar Gemini (estado del proveedor, conexión y reinicio). Solo administración."""

import flet as ft

import config
from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import ia_gemini


def build(ctx) -> ft.Control:
    est = ia_gemini.estado()
    resultado = ft.Column([], spacing=6)

    def verificar(e):
        resultado.controls = [C.cargando("Consultando Gemini…")]
        ctx.actualizar()
        try:
            modelos = ia_gemini.listar_modelos()
            ok = config.GEMINI_MODEL() in modelos
            resultado.controls = [C.nota(f"Conexión y clave válidas ({len(modelos)} modelos)." + ("" if ok else f" Pero «{config.GEMINI_MODEL()}» no aparece: cambie GEMINI_MODEL."),
                                         "ok" if ok else "warn")]
        except Exception as ex:
            resultado.controls = [C.nota(f"Gemini no respondió ({type(ex).__name__}: {ex}). El sistema sigue en modo local.", "bad")]
        ctx.actualizar()

    def reiniciar(e):
        ia_gemini.reiniciar_estado()
        ctx.toast("Estado del proveedor reiniciado.")
        ctx.refrescar()

    ia = ft.Column([
        ft.Row([ft.Text("Proveedor efectivo:", size=13), C.pill("Gemini" if est["proveedor"] == "gemini" else "Local (reglas)", "info" if est["proveedor"] == "gemini" else "neutro"),
                ft.Text("Clave:", size=13), C.pill("configurada" if est["clave_configurada"] else "no configurada", "ok" if est["clave_configurada"] else "warn")], spacing=10, wrap=True),
        ft.Text(f"Modelo: {est['modelo']} · tiempo máximo {config.AI_TIMEOUT_SECONDS()} s · reintentos {config.AI_MAX_REINTENTOS()}", size=12.5, color=T.MUTED),
        C.nota(f"Gemini suspendido {est['suspendido_s']} s: {est['motivo_suspension']}", "warn") if est["suspendido_s"] else ft.Container(),
        ft.Row([C.boton("Verificar conexión con Gemini", verificar, ft.Icons.AUTO_AWESOME, "secundario", disabled=not est["clave_configurada"]),
                C.boton("Reiniciar estado", reiniciar, ft.Icons.REFRESH, "secundario")], spacing=10, wrap=True),
        resultado,
        ft.Text("Se configura con variables de entorno o el archivo .env (AI_PROVIDER, GEMINI_API_KEY, GEMINI_MODEL). Sin clave el sistema usa el modo local. "
                "Antes de enviar texto a Gemini se ocultan teléfonos y correos. Ver docs/IA_GEMINI.md.", size=11.5, color=T.MUTED)], spacing=10)

    return C.seccion(C.titulo("Verificar Gemini", "Diagnóstico del proveedor de IA (sin cambiar datos de negocio)"), C.panel(ia, "Inteligencia artificial"))
