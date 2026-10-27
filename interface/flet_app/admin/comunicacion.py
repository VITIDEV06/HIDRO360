"""Comunicaciones pendientes: borradores internos. Nada se envía ni se publica desde aquí."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoPlan
from services import workflow


def build(ctx) -> ft.Control:
    planes = workflow.listar_planes()
    cabecera = C.nota("Solo borradores internos: no se envían ni se publican y no prometen hora de llegada. Cualquier envío real requiere "
                      "aprobación humana e integración autorizada (no implementada).", "warn")
    if not planes:
        return C.seccion(C.titulo("Comunicación"), cabecera, C.vacio("Genere un plan para ver los borradores.", ft.Icons.MAIL))
    plan = next((p for p in planes if p.id == ctx.seleccion.get("plan_id")), planes[-1])
    selector = C.lista_desplegable("Plan", [(p.id, f"Plan #{p.id} · {p.estado.value}") for p in reversed(planes)], str(plan.id), width=300)
    selector.on_select = lambda e: ctx.ir("comunicacion", plan_id=int(selector.value))
    estado = C.pill("aprobado: puede usarse tras revisión humana", "ok") if plan.estado == EstadoPlan.APROBADO else C.pill("sin aprobar: no usar", "warn")
    tarjetas = []
    for b in plan.borradores_comunicacion:
        caja = ft.Column([ft.Text(b["texto"], size=12.5, selectable=True)], spacing=8)

        def redactar(e, sector=b["sector"], caja=caja):
            caja.controls = [C.cargando("Redactando borrador…")]
            ctx.actualizar()
            ok, res = ctx.ejecutar(lambda: workflow.redactar_borrador_ia(plan.id, sector))
            caja.controls = [ft.Row([C.pill(f"origen: {res['origen']}", "info" if res["origen"] == "gemini" else "neutro"), C.pill("BORRADOR — no enviado", "warn")], spacing=8),
                             ft.Text(res["texto"], size=12.5, selectable=True)] if ok else caja.controls
            ctx.actualizar()

        tarjetas.append(C.panel(ft.Column([
            ft.Row([ft.Text(b["sector"], size=15, weight=ft.FontWeight.W_700, color=T.NAVY), C.pill(b["tipo"], "info"), C.pill("BORRADOR — no enviado", "warn")], spacing=8, wrap=True),
            caja, *([C.boton("Redactar con IA", redactar, ft.Icons.AUTO_AWESOME, "secundario")] if b["tipo"] == "asignación propuesta" else [])], spacing=8)))
    return C.seccion(C.titulo("Comunicación", "Borradores informativos por sector"), cabecera, ft.Row([selector, estado], spacing=12),
                     *(tarjetas or [C.vacio("El plan no tiene borradores.")]))
