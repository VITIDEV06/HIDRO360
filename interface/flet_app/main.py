"""Aplicación Flet de HIDRO360 (TEAM VIFY AI): dos experiencias separadas.

- PORTAL de usuario (portal/*): público, sin login. Reportar, consultar con código, avisos.
- CONSOLA de administración (admin/*): requiere sesión de administrador (login). Operación, seguimiento, sistema y
  un grupo «Diagnóstico y desarrollo».
Aquí no hay reglas de negocio: las páginas usan services.workflow mediante Ctx.ejecutar (con el actor de la sesión).
Ocultar secciones no es seguridad; la autorización real se comprueba en services.auth / workflow."""

import argparse
import asyncio
import importlib
import traceback
from pathlib import Path
from typing import Optional

import flet as ft

import config
from interface.flet_app import components as C
from interface.flet_app import theme as T
from interface.flet_app.contexto import Ctx
from services import workflow

ROOT = Path(__file__).resolve().parents[2]

# ------------------------------------------------------------------ portal público
# (clave, etiqueta, icono, módulo)
PORTAL = [
    ("inicio", "Inicio", ft.Icons.HOME, "inicio"),
    ("reportar", "Reportar", ft.Icons.WATER_DROP, "reportar"),
    ("consultar", "Consultar mi reporte", ft.Icons.SEARCH, "consultar"),
    ("avisos", "Avisos", ft.Icons.CAMPAIGN, "avisos"),
    ("asistente", "Asistente", ft.Icons.CHAT_BUBBLE, "asistente"),
]

# ------------------------------------------------------ consola de administración
GRUPOS_ADMIN = [
    ("OPERACIÓN", [
        ("panel", "Panel", ft.Icons.DASHBOARD, "panel"),
        ("bandeja", "Bandeja de reportes", ft.Icons.INBOX, "bandeja"),
        ("mapa", "Mapa", ft.Icons.MAP, "mapa"),
        ("recomendaciones", "Recomendaciones IA", ft.Icons.AUTO_AWESOME, "recomendaciones"),
        ("revision", "Revisión", ft.Icons.FACT_CHECK, "revision"),
        ("duplicados", "Ubicación y duplicados", ft.Icons.PLACE, "duplicados"),
        ("prioridad", "Prioridades", ft.Icons.INSIGHTS, "prioridad"),
        ("plan", "Plan y aprobación", ft.Icons.LOCAL_SHIPPING, "plan"),
        ("comunicacion", "Comunicación", ft.Icons.MAIL, "comunicacion")]),
    ("FLOTA", [
        ("misiones", "Misiones y flota", ft.Icons.ROUTE, "misiones")]),
    ("SEGUIMIENTO", [
        ("metricas", "Métricas operativas", ft.Icons.BAR_CHART, "metricas"),
        ("auditoria", "Auditoría", ft.Icons.HISTORY, "auditoria")]),
    ("SISTEMA", [
        ("usuarios", "Usuarios", ft.Icons.MANAGE_ACCOUNTS, "usuarios"),
        ("configuracion", "Configuración e integraciones", ft.Icons.SETTINGS, "configuracion")]),
    ("DIAGNÓSTICO Y DESARROLLO", [
        ("trazas", "Trazas", ft.Icons.TIMELINE, "trazas"),
        ("impacto", "Evaluación e impacto", ft.Icons.FACT_CHECK_OUTLINED, "impacto"),
        ("gemini", "Verificar Gemini", ft.Icons.AUTO_AWESOME, "gemini"),
        ("datos_ejemplo", "Cargar datos de ejemplo", ft.Icons.REFRESH, "datos_ejemplo")]),
]
GRUPO_DIAGNOSTICO = "DIAGNÓSTICO Y DESARROLLO"
PAGINAS = [p for _, items in GRUPOS_ADMIN for p in items]     # páginas administrativas
CLAVES_ADMIN = {k for k, _, _, _ in PAGINAS}
CLAVES_PORTAL = {k for k, _, _, _ in PORTAL}
ETIQUETAS = {k: e for k, e, _, _ in PAGINAS + PORTAL}
ETIQUETAS["login"] = "Acceso administración"


def resolver(ctx: Ctx, clave: str) -> str:
    """Decide qué página se muestra de verdad: una página administrativa sin sesión admin vuelve al login (y recuerda el destino)."""
    if clave in CLAVES_ADMIN:
        if not ctx.es_admin:
            ctx.destino_tras_login = clave
            return "login"
        return clave
    if clave == "login":
        return "login" if not ctx.es_admin else "panel"
    return clave if clave in CLAVES_PORTAL else "inicio"


def construir_pagina(ctx: Ctx, clave: str) -> ft.Control:
    """Construye una página capturando errores. Las páginas administrativas SOLO se construyen con sesión de administrador
    (y dentro de `auth.como(actor)`); sin ella se devuelve el login."""
    clave = resolver(ctx, clave)
    try:
        if clave == "login":
            return importlib.import_module("interface.flet_app.admin.login").build(ctx)
        if clave in CLAVES_ADMIN:
            modulo = next(m for k, _, _, m in PAGINAS if k == clave)
            with ctx.con_actor():
                return importlib.import_module(f"interface.flet_app.admin.{modulo}").build(ctx)
        modulo = next(m for k, _, _, m in PORTAL if k == clave)
        return importlib.import_module(f"interface.flet_app.portal.{modulo}").build(ctx)
    except Exception as e:
        traceback.print_exc()
        detalle = f" ({type(e).__name__}: {e})" if clave in CLAVES_ADMIN else ""
        return C.seccion(C.titulo(ETIQUETAS.get(clave, clave)),
                         C.nota(f"No se pudo mostrar esta pantalla{detalle}. El resto de la aplicación sigue funcionando.", "bad"))


def main(page: ft.Page) -> None:
    ctx = Ctx(page)
    page.title = config.APP_NAME
    page.bgcolor = T.BG
    page.padding = 0
    page.theme = ft.Theme(color_scheme_seed=T.BLUE)  # sin fuentes remotas: funciona sin internet
    page.theme_mode = ft.ThemeMode.LIGHT

    contenido = ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=26, vertical=20))
    raiz = ft.Container(expand=True)
    estado = {"compacto": False, "diag": False}

    def compacto() -> bool:
        return bool(page.width and page.width < 980)

    # ----------------------------------------------------------------- portal
    def nav_portal(clave, etiqueta, icono):
        activo = clave == ctx.pagina_actual
        color = T.NAVY if activo else "#52627a"
        fila = [ft.Icon(icono, size=20, color=color)] + ([] if estado["compacto"] else [ft.Text(etiqueta, size=13.5, weight=ft.FontWeight.W_700 if activo else ft.FontWeight.W_500, color=color)])
        # alto mínimo 44 px: objetivo táctil cómodo en el celular
        return ft.Container(ft.Row(fila, spacing=6, alignment=ft.MainAxisAlignment.CENTER), padding=ft.Padding.symmetric(horizontal=10 if estado["compacto"] else 12, vertical=10),
                            border_radius=10, bgcolor="#e8f5ff" if activo else None, tooltip=etiqueta, ink=True, height=44,
                            on_click=lambda e, k=clave: ctx.ir(k))

    def armar_portal() -> ft.Control:
        angosto = bool(page.width and page.width < 560)
        marca = ft.Container(ft.Row([C.logo(36 if estado["compacto"] else 40)] + ([] if angosto else [ft.Text(config.APP_NAME, size=20, weight=ft.FontWeight.W_800, color=T.NAVY)]), spacing=10),
                             ink=True, on_click=lambda e: ctx.ir("inicio"), tooltip="Ir al inicio")
        navegacion = ft.Row([nav_portal(k, e, i) for k, e, i, _ in PORTAL], spacing=2)
        if estado["compacto"]:   # una sola fila en el celular: acceso de administración como icono
            acceso = ft.IconButton(ft.Icons.ADMIN_PANEL_SETTINGS, tooltip="Acceso administración (personal autorizado)", on_click=lambda e: ctx.ir("login"),
                                   icon_color=T.MUTED if ctx.pagina_actual != "login" else T.NAVY)
        else:
            acceso = ft.TextButton("Acceso administración", icon=ft.Icons.ADMIN_PANEL_SETTINGS, on_click=lambda e: ctx.ir("login"),
                                   tooltip="Entrada para el personal autorizado", style=ft.ButtonStyle(color=T.MUTED if ctx.pagina_actual != "login" else T.NAVY))
        barra = ft.Container(ft.Row([marca, navegacion, acceso], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    wrap=not estado["compacto"], run_spacing=4),
                             bgcolor=T.WHITE, padding=ft.Padding.symmetric(horizontal=10 if estado["compacto"] else 20, vertical=6), border=ft.Border(bottom=ft.BorderSide(1, T.LINE)))
        pie = ft.Container(ft.Row([ft.Text(f"{config.APP_NAME} · {config.EQUIPO}", size=11.5, color=T.MUTED), C.pill("demostración", "neutro")],
                                  spacing=10, alignment=ft.MainAxisAlignment.CENTER, wrap=True),
                           bgcolor=T.WHITE, padding=ft.Padding.symmetric(horizontal=20, vertical=8), border=ft.Border(top=ft.BorderSide(1, T.LINE)))
        return ft.Column([barra, contenido, pie], spacing=0, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # ------------------------------------------------------------------ admin
    def item_admin(clave, etiqueta, icono):
        activo = clave == ctx.pagina_actual
        col = T.WHITE if activo else "#52627a"
        fila = ft.Row([ft.Icon(icono, size=20, color=col)] + ([] if estado["compacto"] else [ft.Text(etiqueta, size=13.5, weight=ft.FontWeight.W_600, color=col)]), spacing=12)
        return ft.Container(fila, padding=ft.Padding.symmetric(horizontal=14, vertical=10), border_radius=12, bgcolor=T.BLUE if activo else None,
                            tooltip=etiqueta, on_click=lambda e, k=clave: ctx.ir(k), ink=True)

    def alternar_diag(e=None):
        estado["diag"] = not estado["diag"]
        pintar()
        page.update()

    def armar_admin() -> ft.Control:
        nav = []
        for grupo, items in GRUPOS_ADMIN:
            if grupo == GRUPO_DIAGNOSTICO:
                abierto = estado["diag"] or ctx.pagina_actual in {k for k, _, _, _ in items}
                cab = ft.Container(ft.Row([ft.Icon(ft.Icons.BUILD_CIRCLE_OUTLINED, size=18, color=T.MUTED)]
                                          + ([] if estado["compacto"] else [ft.Text(grupo.capitalize(), size=11.5, weight=ft.FontWeight.W_700, color=T.MUTED, expand=True),
                                                                           ft.Icon(ft.Icons.EXPAND_LESS if abierto else ft.Icons.EXPAND_MORE, size=18, color=T.MUTED)]), spacing=10),
                                   padding=ft.Padding.symmetric(horizontal=14, vertical=8), ink=True, on_click=alternar_diag, border_radius=10,
                                   tooltip="Herramientas técnicas: solo para administración y desarrollo")
                nav.append(cab)
                if abierto:
                    nav += [item_admin(k, e, i) for k, e, i, _ in items]
            else:
                if not estado["compacto"]:
                    nav.append(ft.Text(grupo, size=10.5, weight=ft.FontWeight.W_800, color=T.MUTED))
                else:
                    nav.append(ft.Divider(height=6, color=T.LINE))
                nav += [item_admin(k, e, i) for k, e, i, _ in items]
        marca = ft.Row([C.logo(42)] + ([] if estado["compacto"] else [ft.Column([ft.Text(config.APP_NAME, size=18, weight=ft.FontWeight.W_800, color=T.NAVY),
                                                                                  ft.Text(config.EQUIPO, size=10, color=T.MUTED)], spacing=0)]), spacing=10)
        lateral = ft.Container(ft.Column([marca, ft.Divider(height=14, color=T.LINE), ft.Column(nav, spacing=3, scroll=ft.ScrollMode.AUTO, expand=True)], spacing=4, expand=True),
                               bgcolor=T.WHITE, padding=14, width=76 if estado["compacto"] else 262, border=ft.Border(right=ft.BorderSide(1, T.LINE)))
        a = ctx.actor
        quien = ft.Row([ft.Icon(ft.Icons.PERSON, size=18, color=T.NAVY),
                        ft.Text(a.usuario if a else "", size=13, weight=ft.FontWeight.W_700, color=T.NAVY), C.pill(a.rol.value if a else "", "info")], spacing=6)
        acciones = ft.Row([quien, C.boton("Portal público", lambda e: ctx.ir("inicio"), ft.Icons.PUBLIC, "secundario", tooltip="Ver el portal que ve la ciudadanía"),
                           C.boton("Cerrar sesión", cerrar, ft.Icons.LOGOUT, "secundario", tooltip="Terminar la sesión de administración")],
                          spacing=10, wrap=True, run_spacing=6, alignment=ft.MainAxisAlignment.END)
        barra = ft.Container(ft.Row([ft.Text(ETIQUETAS.get(ctx.pagina_actual, ""), size=13, color=T.MUTED, weight=ft.FontWeight.W_600), acciones],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True),
                             bgcolor=T.WHITE, padding=ft.Padding.symmetric(horizontal=20, vertical=10), border=ft.Border(bottom=ft.BorderSide(1, T.LINE)))
        return ft.Row([lateral, ft.Column([barra, contenido], spacing=0, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)], spacing=0, expand=True)

    def cerrar(e=None):
        ctx.cerrar_sesion()
        ctx.toast("Sesión cerrada.", "ok")
        ctx.ir("inicio")

    def bienvenida() -> ft.Control:
        return C.seccion(ft.Container(ft.Column([
            C.logo(100), ft.Text(f"Bienvenido a {config.APP_NAME}", size=26, weight=ft.FontWeight.W_800, color=T.NAVY),
            ft.Text("La base de datos está vacía. Cargue los datos de ejemplo (ficticios) desde «Diagnóstico y desarrollo» para recorrer la demostración.", size=14, color=T.MUTED),
            C.boton("Ir a cargar datos de ejemplo", lambda e: ctx.ir("datos_ejemplo"), ft.Icons.PLAY_ARROW)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12), padding=40, alignment=ft.Alignment.CENTER))

    # ------------------------------------------------------------- ensamblado
    def pintar():
        estado["compacto"] = compacto()
        contenido.padding = ft.Padding.symmetric(horizontal=14 if estado["compacto"] else 26, vertical=14 if estado["compacto"] else 20)
        raiz.content = armar_admin() if ctx.pagina_actual in CLAVES_ADMIN else armar_portal()

    def mostrar(clave: str) -> None:
        clave = resolver(ctx, clave)
        ctx.pagina_actual = clave
        if clave in CLAVES_ADMIN:
            with ctx.con_actor():
                hay_datos = bool(workflow.listar_sectores()) or clave == "datos_ejemplo"
            contenido.content = construir_pagina(ctx, clave) if hay_datos else bienvenida()
        else:
            contenido.content = construir_pagina(ctx, clave)
        pintar()
        page.update()

    ctx._mostrar = mostrar
    page.on_resize = lambda e: (pintar(), page.update())
    page.add(raiz)
    mostrar("inicio")

    async def ajustar_al_arrancar():
        """Flet informa el ancho real un instante después de conectar y no siempre dispara on_resize: se repinta si cambió."""
        for _ in range(6):
            await asyncio.sleep(0.5)
            if compacto() != estado["compacto"]:
                pintar()
                page.update()
                break

    page.run_task(ajustar_al_arrancar)


def run(argv: Optional[list] = None) -> None:
    config.exigir_persistencia_produccion()  # en producción la interfaz NO puede caer a SQLite temporal
    ap = argparse.ArgumentParser(description="HIDRO360 · TEAM VIFY AI (interfaz Flet)")
    ap.add_argument("--web", action="store_true", help="servir como aplicación web (para Raspberry Pi o acceso desde el navegador)")
    ap.add_argument("--host", default="127.0.0.1", help="solo con --web; use 0.0.0.0 solo en una red de confianza (el canal no está cifrado)")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--sin-navegador", action="store_true", help="con --web: no abrir el navegador")
    a = ap.parse_args(argv)
    assets = str(ROOT / "assets")
    if a.web:
        if a.sin_navegador:  # servidor sin abrir el navegador (p. ej. Raspberry Pi sin pantalla)
            import webbrowser
            webbrowser.open = lambda *args, **kw: False
        ft.run(main, view=ft.AppView.WEB_BROWSER, host=a.host, port=a.port, assets_dir=assets)  # no_cdn=True rompe el render (flet-web 1.0.4 no trae las fuentes): ver docs/DECISIONES.md
    else:
        ft.run(main, assets_dir=assets)
