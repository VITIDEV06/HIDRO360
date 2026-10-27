"""Portal público: avisos comprensibles para la comunidad."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from interface.flet_app.portal import datos


def lista_avisos(items, maximo: int = 50) -> ft.Control:
    if not items:
        return C.vacio("Por ahora no hay avisos publicados. Si le falta el agua, puede reportarlo.", ft.Icons.NOTIFICATIONS_NONE)
    tarjetas = []
    for a in items[:maximo]:
        cuerpo = [ft.Text(a["titulo"], size=15, weight=ft.FontWeight.W_700, color=T.NAVY)] if a.get("titulo") else []
        if a.get("mensaje"):
            cuerpo.append(ft.Text(a["mensaje"], size=13.5, color=T.INK))
        tarjetas.append(ft.Container(ft.Row([ft.Icon(ft.Icons.CAMPAIGN, color=T.BLUE, size=24), ft.Column(cuerpo, spacing=3, expand=True)],
                                            spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
                                     bgcolor=T.WHITE, border=ft.Border.all(1, T.LINE), border_radius=14, padding=14))
    return ft.Column(tarjetas, spacing=10)


def build(ctx) -> ft.Control:
    return C.seccion(C.titulo("Avisos", "Información útil para su comunidad"), lista_avisos(datos.avisos()),
                     C.nota("Los avisos se actualizan según los reportes recibidos. No indican hora de llegada del servicio.", "info"))
