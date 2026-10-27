"""Diagnóstico y desarrollo: cargar / reiniciar los datos de ejemplo (FICTICIOS). Destructivo: pide confirmación."""

import flet as ft

from interface.flet_app import components as C
from services import workflow


def build(ctx) -> ft.Control:
    def cargar(e):
        def hacer():
            ok, n = ctx.ejecutar(lambda: workflow.cargar_datos_demo(ctx.responsable))
            if ok:
                ctx.toast(f"Datos FICTICIOS cargados: {n['reportes']} reportes, {n['cisternas']} cisternas, {n['sectores']} sectores.")
                ctx.seleccion.clear()
                ctx.ir("panel")
        ctx.confirmar("Cargar / reiniciar datos de ejemplo",
                      "Esto BORRA todos los reportes, planes, decisiones, auditoría y catálogos de la base local y carga datos ficticios. "
                      "Si necesita conservar algo, copie antes el archivo de la base de datos. ¿Continuar?", hacer, peligro=True)

    return C.seccion(C.titulo("Cargar datos de ejemplo", "Para demostraciones y pruebas; no use esta opción con datos reales"),
                     C.nota("Reemplaza el contenido de la base local por reportes, sectores y cisternas FICTICIOS. Es irreversible: copie antes el archivo de la base si necesita conservarla.", "bad"),
                     C.panel(ft.Row([C.ficticio(), C.boton("Cargar datos de ejemplo", cargar, ft.Icons.REFRESH, "peligro")], spacing=12), "Datos de ejemplo"))
