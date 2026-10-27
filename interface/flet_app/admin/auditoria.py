"""Auditoría de negocio: quién decidió qué, cuándo y por qué (cadena de hashes verificable)."""

import json

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import audit


def build(ctx) -> ft.Control:
    ok = audit.verificar_integridad()
    entradas = audit.listar()
    estado = C.nota("Cadena de hashes íntegra ✔" if ok else "¡La cadena de auditoría fue alterada!", "ok" if ok else "bad")
    aviso = C.nota("El responsable de cada registro es la cuenta de administración que inició sesión.", "info")
    f_ent = C.lista_desplegable("Entidad", ["(todas)", "plan", "reporte", "sistema"], "(todas)", width=170)
    f_id = C.campo("Id de entidad", "", width=170)
    salida = ft.Column([], spacing=8)

    def pintar(e=None):
        lista = [x for x in entradas if (f_ent.value in (None, "(todas)") or x.entidad == f_ent.value) and (not (f_id.value or "").strip() or x.entidad_id == f_id.value.strip())]
        filas = [[x.id, str(x.fecha)[:19], x.responsable, x.accion, f"{x.entidad} {x.entidad_id}", x.motivo or "—",
                  ft.IconButton(icon=ft.Icons.VISIBILITY, tooltip="Ver detalle (antes/después, extracción…)", icon_color=T.BLUE,
                                on_click=lambda ev, x=x: ctx.dialogo(f"Entrada #{x.id}: {x.accion}", ft.Container(ft.Column([
                                    ft.Text(json.dumps(x.detalle, ensure_ascii=False, indent=2, default=str), size=11.5, selectable=True),
                                    ft.Text(f"hash {x.hash[:24]}… · previo {x.hash_previo[:24]}…", size=11, color=T.MUTED)], scroll=ft.ScrollMode.AUTO),
                                    width=640, height=420), None))] for x in reversed(lista)]
        salida.controls = [C.tabla(["#", "Fecha", "Responsable", "Acción", "Entidad", "Motivo", ""], filas), ft.Text(f"{len(lista)} entradas", size=11.5, color=T.MUTED)]
        ctx.actualizar() if e is not None else None

    f_ent.on_select = pintar
    f_id.on_submit = pintar
    pintar()
    return C.seccion(C.titulo("Auditoría", "Cada decisión queda con responsable, fecha, motivo y cambios"), estado, aviso,
                     C.panel(ft.Column([ft.Row([f_ent, f_id], spacing=12), salida], spacing=12), "Registro"))
