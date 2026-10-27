"""Componentes reutilizables de la interfaz Flet. Sin lógica de negocio."""

from typing import Any, Callable, Iterable, List, Optional, Sequence

import flet as ft

from interface.flet_app import theme as T


def texto(valor: Any, size: int = 14, color: str = T.INK, bold: bool = False, **kw) -> ft.Text:
    return ft.Text(str(valor), size=size, color=color, weight=ft.FontWeight.W_700 if bold else ft.FontWeight.W_400,
                   selectable=True, **kw)


def titulo(valor: str, sub: Optional[str] = None) -> ft.Control:
    cols: List[ft.Control] = [ft.Text(valor, size=24, weight=ft.FontWeight.W_800, color=T.NAVY)]
    if sub:
        cols.append(ft.Text(sub, size=13, color=T.MUTED))
    return ft.Column(cols, spacing=2)


def panel(contenido: ft.Control, titulo_panel: Optional[str] = None, accion: Optional[ft.Control] = None,
          padding: int = 18, expand: bool = False) -> ft.Container:
    filas: List[ft.Control] = []
    if titulo_panel or accion:
        filas.append(ft.Row([ft.Text(titulo_panel or "", size=16, weight=ft.FontWeight.W_700, color=T.NAVY),
                             accion or ft.Container()], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))
    filas.append(contenido)
    return ft.Container(ft.Column(filas, spacing=12), bgcolor=T.WHITE, border=ft.Border.all(1, T.LINE),
                        border_radius=18, padding=padding, expand=expand,
                        shadow=ft.BoxShadow(blur_radius=18, color="#173d6b0d", offset=ft.Offset(0, 4)))


def pill(etiqueta: str, tipo: str = "neutro") -> ft.Container:
    fondo, fg = T.PILL.get(tipo, T.PILL["neutro"])
    return ft.Container(ft.Text(etiqueta, size=11, weight=ft.FontWeight.W_700, color=fg), bgcolor=fondo,
                        border_radius=999, padding=ft.Padding.symmetric(horizontal=9, vertical=2))


def pill_estado(clave: str) -> ft.Container:
    etiqueta, tipo = T.ESTADO_PILL.get(clave, (clave, "neutro"))
    return pill(etiqueta, tipo)


def ficticio() -> ft.Container:
    return pill("DATOS FICTICIOS", "ficticio")


def kpi(valor: Any, etiqueta: str, icono=None, nivel: str = "", ancho: int = 232) -> ft.Container:
    color = {"warn": T.AMBER, "bad": T.RED, "ok": T.GREEN}.get(nivel, T.BLUE)
    return ft.Container(
        ft.Row([ft.Container(ft.Icon(icono or ft.Icons.WATER_DROP, color=color, size=22), width=44, height=44,
                             bgcolor="#e8f5ff", border_radius=14, alignment=ft.Alignment.CENTER),
                ft.Column([ft.Text(etiqueta, size=11, color=T.MUTED, weight=ft.FontWeight.W_600),
                           ft.Text(str(valor), size=24, weight=ft.FontWeight.W_800, color=T.NAVY)], spacing=0)],
               spacing=12),
        bgcolor=T.WHITE, border=ft.Border.all(1, T.LINE), border_radius=18, padding=16, width=ancho)


def nota(mensaje: str, tipo: str = "warn") -> ft.Container:
    fondo, fg = T.PILL[tipo]
    icono = {"warn": ft.Icons.WARNING_AMBER, "bad": ft.Icons.ERROR_OUTLINE, "ok": ft.Icons.CHECK_CIRCLE}.get(tipo, ft.Icons.INFO_OUTLINE)
    return ft.Container(ft.Row([ft.Icon(icono, color=fg, size=18), ft.Text(mensaje, size=12.5, color=fg, expand=True,
                                                                         selectable=True)], spacing=8,
                               vertical_alignment=ft.CrossAxisAlignment.START),
                        bgcolor=fondo, border_radius=10, padding=ft.Padding.symmetric(horizontal=12, vertical=9))


def vacio(mensaje: str, icono=None) -> ft.Container:
    return ft.Container(ft.Column([ft.Icon(icono or ft.Icons.INFO_OUTLINE, color=T.MUTED, size=28),
                                   ft.Text(mensaje, color=T.MUTED, size=13, text_align=ft.TextAlign.CENTER)],
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                        padding=26, alignment=ft.Alignment.CENTER, bgcolor="#f8fbfe", border_radius=14,
                        border=ft.Border.all(1, T.LINE))


def boton(etiqueta: str, on_click: Callable, icono=None, tipo: str = "primario", **kw) -> ft.Control:
    if tipo == "primario":
        return ft.Button(etiqueta, icon=icono, on_click=on_click,
                         style=ft.ButtonStyle(bgcolor=T.BLUE, color=T.WHITE, shape=ft.RoundedRectangleBorder(radius=12),
                                              padding=ft.Padding.symmetric(horizontal=18, vertical=14)), **kw)
    if tipo == "peligro":
        return ft.Button(etiqueta, icon=icono, on_click=on_click,
                         style=ft.ButtonStyle(bgcolor=T.RED, color=T.WHITE, shape=ft.RoundedRectangleBorder(radius=12),
                                              padding=ft.Padding.symmetric(horizontal=18, vertical=14)), **kw)
    return ft.OutlinedButton(etiqueta, icon=icono, on_click=on_click,
                             style=ft.ButtonStyle(color=T.NAVY, shape=ft.RoundedRectangleBorder(radius=12),
                                                  side=ft.BorderSide(1, "#d8e5f3"),
                                                  padding=ft.Padding.symmetric(horizontal=16, vertical=12)), **kw)


def campo(etiqueta: str, valor: Any = "", **kw) -> ft.TextField:
    return ft.TextField(label=etiqueta, value="" if valor is None else str(valor), dense=True, border_radius=10,
                        border_color="#cfdcec", focused_border_color=T.BLUE, **kw)


def lista_desplegable(etiqueta: str, opciones: Sequence[Any], valor: Optional[str] = None, **kw) -> ft.Dropdown:
    """opciones: lista de str o de (clave, texto)."""
    ops = [ft.DropdownOption(key=str(o[0]), text=str(o[1])) if isinstance(o, tuple) else ft.DropdownOption(key=str(o), text=str(o))
           for o in opciones]
    return ft.Dropdown(label=etiqueta, options=ops, value=valor, dense=True, border_radius=10,
                       border_color="#cfdcec", focused_border_color=T.BLUE, **kw)


def tabla(columnas: Sequence[str], filas: Iterable[Sequence[Any]], max_filas: int = 200) -> ft.Control:
    filas = list(filas)[:max_filas]
    if not filas:
        return vacio("Sin datos para mostrar.")
    dt = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(c, size=12, weight=ft.FontWeight.W_700, color=T.NAVY)) for c in columnas],
        rows=[ft.DataRow(cells=[ft.DataCell(c if isinstance(c, ft.Control) else ft.Text("" if c is None else str(c), size=12.5, color=T.INK))
                                for c in fila]) for fila in filas],
        heading_row_color="#eef5fc", column_spacing=22, data_row_min_height=38, data_row_max_height=64,
        horizontal_lines=ft.BorderSide(1, T.LINE), divider_thickness=0)
    return ft.Row([dt], scroll=ft.ScrollMode.AUTO)


def barra(valor: float, maximo: float, color: str = T.BLUE, etiqueta: str = "") -> ft.Control:
    frac = 0.0 if not maximo else max(0.0, min(valor / maximo, 1.0))
    return ft.Column([ft.Text(etiqueta, size=12, color=T.INK) if etiqueta else ft.Container(),
                      ft.ProgressBar(value=frac, color=color, bgcolor=T.LINE, height=9, border_radius=5)], spacing=3)


def cargando(mensaje: str = "Cargando…") -> ft.Control:
    return ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2), ft.Text(mensaje, color=T.MUTED, size=13)], spacing=10)


def seccion(*hijos: ft.Control, spacing: int = 16) -> ft.Column:
    return ft.Column(list(hijos), spacing=spacing, scroll=ft.ScrollMode.AUTO, expand=True)


def logo(tam: int = 44) -> ft.Control:
    """Logo original (JPEG sobre fondo blanco, sin editar): siempre sobre una superficie blanca."""
    return ft.Container(ft.Image(src="logo.jpeg", width=tam, height=tam, fit=ft.BoxFit.CONTAIN, semantics_label="Logo de HIDRO360"),
                        bgcolor=T.WHITE, border_radius=10)


def hero(etiqueta: str, titular: str, texto: str, acciones: Sequence[ft.Control]) -> ft.Container:
    """Bloque degradado azul marino del prototipo."""
    return ft.Container(
        ft.Column([ft.Text(etiqueta, size=11, color="#9ceeff", weight=ft.FontWeight.W_800),
                   ft.Text(titular, size=30, color=T.WHITE, weight=ft.FontWeight.W_800),
                   ft.Text(texto, size=15, color="#d6e9ff"),
                   ft.Row(list(acciones), spacing=12, run_spacing=10, wrap=True)], spacing=12),
        padding=30, border_radius=24,
        gradient=ft.LinearGradient(begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
                                   colors=[T.NAVY, "#0b4b9a", "#087fc9"]))


def boton_claro(etiqueta: str, on_click: Callable, icono=None, **kw) -> ft.Control:
    """Botón con borde claro para usar sobre el degradado."""
    return ft.OutlinedButton(etiqueta, icon=icono, on_click=on_click,
                             style=ft.ButtonStyle(color=T.WHITE, side=ft.BorderSide(1, "#ffffff77"), shape=ft.RoundedRectangleBorder(radius=12),
                                                  padding=ft.Padding.symmetric(horizontal=18, vertical=14)), **kw)
