"""Portal público: inicio con titular, acciones principales, avisos y cómo funciona."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from interface.flet_app.portal import avisos as pagina_avisos
from interface.flet_app.portal import datos


def _tarjeta(icono, titulo, texto, boton) -> ft.Control:
    return C.panel(ft.Column([ft.Row([ft.Container(ft.Icon(icono, color=T.BLUE, size=26), width=48, height=48, bgcolor="#e8f5ff",
                                                   border_radius=14, alignment=ft.Alignment.CENTER),
                                      ft.Text(titulo, size=17, weight=ft.FontWeight.W_700, color=T.NAVY, expand=True)], spacing=12),
                              ft.Text(texto, size=13.5, color=T.MUTED), boton], spacing=12))


def _paso(n: int, titulo: str, texto: str) -> ft.Control:
    return ft.Row([ft.Container(ft.Text(str(n), color=T.WHITE, weight=ft.FontWeight.W_800), width=34, height=34, bgcolor=T.BLUE,
                                border_radius=999, alignment=ft.Alignment.CENTER),
                   ft.Column([ft.Text(titulo, size=14.5, weight=ft.FontWeight.W_700, color=T.NAVY),
                              ft.Text(texto, size=13, color=T.MUTED)], spacing=1, expand=True)],
                  spacing=12, vertical_alignment=ft.CrossAxisAlignment.START)


def build(ctx) -> ft.Control:
    hero = C.hero("HIDRO360 · PANAMÁ OESTE", "¿Le falta el agua? Avísenos.",
                  "Cuéntenos qué ocurre en su comunidad. Su reporte ayuda a que las autoridades vean dónde hace falta apoyo primero.",
                  [C.boton("Reportar falta de agua", lambda e: ctx.ir("reportar"), ft.Icons.WATER_DROP, tooltip="Abrir el formulario de reporte"),
                   C.boton_claro("Consultar mi reporte", lambda e: ctx.ir("consultar"), ft.Icons.SEARCH, tooltip="Ver el estado con su código")])
    acciones = ft.ResponsiveRow([
        ft.Container(_tarjeta(ft.Icons.EDIT_NOTE, "Reportar falta de agua", "Escriba con sus palabras qué pasa y dónde. Le daremos un código para seguir su reporte.",
                              C.boton("Hacer un reporte", lambda e: ctx.ir("reportar"), ft.Icons.ARROW_FORWARD)), col={"xs": 12, "md": 6}),
        ft.Container(_tarjeta(ft.Icons.PENDING_ACTIONS, "Consultar mi reporte", "Ingrese su código de seguimiento para ver en qué estado está.",
                              C.boton("Consultar con mi código", lambda e: ctx.ir("consultar"), ft.Icons.SEARCH, "secundario")), col={"xs": 12, "md": 6})],
        run_spacing=14)
    items = datos.avisos()
    avisos = C.panel(ft.Column([pagina_avisos.lista_avisos(items, 3),
                                C.boton("Ver todos los avisos", lambda e: ctx.ir("avisos"), ft.Icons.CAMPAIGN, "secundario") if items else ft.Container()],
                               spacing=12), "Avisos para su comunidad")
    como = C.panel(ft.Column([_paso(1, "Cuéntenos qué pasa", "Describa el problema y el lugar. No necesita crear una cuenta."),
                              _paso(2, "Reciba su código", "Al enviar, verá un código de seguimiento. Guárdelo."),
                              _paso(3, "Siga el estado", "Con el código puede ver si su reporte fue recibido, está en revisión, fue confirmado o tiene atención programada.")], spacing=14),
                   "¿Cómo funciona?")
    return C.seccion(hero, acciones, avisos, como)
