"""Mapa operativo: reportes por estado, cisternas y paradas de misiones activas, con datos reales de `datos_mapa()`.
Solo se dibuja lo que trae coordenadas reales; si no hay nada, se dice. Las teselas necesitan internet."""

import flet as ft

from interface.flet_app import backend
from interface.flet_app import components as C
from interface.flet_app import mapa as M
from interface.flet_app import theme as T

ESTADOS = ["por_revisar", "confirmado", "en_plan", "duplicado", "rechazado"]


def build(ctx) -> ft.Control:
    datos, error = backend.leer("datos_mapa", {})
    cabecera = C.titulo("Mapa", "Dónde están los reportes, las cisternas y las paradas de las misiones activas")
    if error:
        return C.seccion(cabecera, C.nota(f"No se pudieron obtener los datos del mapa: {error}", "bad"))
    pts = M.puntos_de_mapa(datos)
    total_rep = len(datos.get("reportes") or []) if isinstance(datos, dict) else 0
    sin_coord = total_rep - len(pts["reportes"])

    kpis = ft.Row([C.kpi(len(pts["reportes"]), "Reportes en el mapa", ft.Icons.PLACE), C.kpi(len(pts["cisternas"]), "Cisternas ubicadas", ft.Icons.LOCAL_SHIPPING),
                   C.kpi(len(pts["paradas"]), "Paradas activas", ft.Icons.FLAG)], spacing=12, wrap=True, run_spacing=12)
    if not (pts["reportes"] or pts["cisternas"] or pts["paradas"]):
        return C.seccion(cabecera, kpis, C.vacio("Todavía no hay nada con ubicación para mostrar. Cuando la ciudadanía marque su reporte en el mapa, "
                                                 "o una cisterna tenga posición, aparecerá aquí.", ft.Icons.MAP))

    cont = ft.Container()
    checks = {"reportes": ft.Checkbox(label="Reportes", value=True), "cisternas": ft.Checkbox(label="Cisternas", value=True),
              "paradas": ft.Checkbox(label="Paradas de misiones", value=True)}
    f_estado = C.lista_desplegable("Estado del reporte", ["(todos)"] + ESTADOS, "(todos)", width=210)

    def pintar(e=None):
        cont.content = M.general(datos, [k for k, c in checks.items() if c.value], None if f_estado.value in (None, "(todos)") else [f_estado.value])
        if e is not None:
            ctx.actualizar()

    for c in checks.values():
        c.on_change = pintar
    f_estado.on_select = pintar
    pintar()
    notas = [C.nota(f"{sin_coord} reporte(s) no tienen ubicación en el mapa y no aparecen aquí (la ciudadanía no la marcó).", "info")] if sin_coord > 0 else []
    return C.seccion(cabecera, kpis,
                     C.panel(ft.Column([ft.Row([*checks.values(), f_estado], spacing=14, wrap=True, run_spacing=6), M.leyenda(), cont,
                                        ft.Text("Mapa © colaboradores de OpenStreetMap. Las teselas se descargan de internet: sin conexión el fondo se ve gris, pero los marcadores se conservan.",
                                                size=11.5, color=T.MUTED)] + notas, spacing=10), "Mapa operativo"))
