"""Panel de administración: qué requiere atención ahora (pendientes, plan por decidir, prioridades). Las métricas están en su propia sección."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoPlan, EstadoReporte
from services import workflow


def build(ctx) -> ft.Control:
    reps, planes = workflow.listar_reportes(), workflow.listar_planes()
    por_revisar = sum(r.estado == EstadoReporte.POR_REVISAR for r in reps)
    confirmados = sum(r.estado in (EstadoReporte.CONFIRMADO, EstadoReporte.EN_PLAN) for r in reps)
    propuestos = [p for p in planes if p.estado == EstadoPlan.PROPUESTO]
    prios = workflow.calcular_prioridades()

    usuario = ctx.responsable
    hero = C.hero("CONSOLA DE ADMINISTRACIÓN", f"Hola, {usuario}",
                  "Revise los reportes recibidos, confirme los datos y apruebe los planes. La IA ayuda a leer; las reglas calculan; una persona decide.",
                  [C.boton("Bandeja de reportes", lambda e: ctx.ir("bandeja"), ft.Icons.INBOX),
                   C.boton_claro("Planificación", lambda e: ctx.ir("plan"), ft.Icons.LOCAL_SHIPPING)])
    kpis = ft.Row([
        C.kpi(len(reps), "Reportes totales", ft.Icons.INBOX),
        C.kpi(por_revisar, "Por revisar", ft.Icons.FACT_CHECK, "warn" if por_revisar else ""),
        C.kpi(confirmados, "Confirmados", ft.Icons.CHECK_CIRCLE, "ok"),
        C.kpi(len(propuestos), "Planes por decidir", ft.Icons.LOCAL_SHIPPING, "warn" if propuestos else ""),
    ], spacing=12, wrap=True, run_spacing=12)

    pendientes = []
    if por_revisar:
        pendientes.append(C.boton(f"Revisar {por_revisar} reportes pendientes", lambda e: ctx.ir("revision"), ft.Icons.FACT_CHECK, "secundario"))
    if propuestos:
        pendientes.append(C.boton("Decidir el plan propuesto", lambda e: ctx.ir("plan"), ft.Icons.LOCAL_SHIPPING, "secundario"))
    elif prios and not planes:
        pendientes.append(C.boton("Generar propuesta de plan", lambda e: ctx.ir("plan"), ft.Icons.LOCAL_SHIPPING, "secundario"))
    atencion = C.panel(ft.Row(pendientes, spacing=10, wrap=True) if pendientes else C.nota("Nada pendiente por ahora.", "ok"), "Requiere atención")

    top = ft.Column([ft.Row([ft.Container(ft.Text(f"{i + 1}", size=12, color=T.WHITE, weight=ft.FontWeight.W_800),
                                          bgcolor=T.CATEGORIA_COLOR.get(p.categoria, T.BLUE), width=24, height=24, border_radius=999,
                                          alignment=ft.Alignment.CENTER),
                             ft.Column([ft.Text(p.sector, size=14, weight=ft.FontWeight.W_700, color=T.NAVY),
                                        ft.Text(f"{p.horas_sin_agua or '?'} h · {p.personas_afectadas or '?'} personas · {p.reportes_contados} reporte(s)",
                                                size=11.5, color=T.MUTED)], spacing=0, expand=True),
                             ft.Text(f"{p.puntaje}", size=15, weight=ft.FontWeight.W_800, color=T.NAVY), C.pill_estado(p.categoria)],
                            spacing=10) for i, p in enumerate(prios[:6])], spacing=10) if prios else C.vacio("Aún no hay reportes confirmados.")
    return C.seccion(hero, kpis, atencion, C.panel(top, "Prioridades actuales"))
