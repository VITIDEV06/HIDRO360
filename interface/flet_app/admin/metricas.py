"""Métricas operativas: indicadores, recorrido de reportes y planes, integridad y sectores sin información (todo calculado con datos reales; el mapa y las prioridades tienen su propia página)."""

import flet as ft

import config
from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoPlan, EstadoReporte
from services import audit, tracing, workflow


def build(ctx) -> ft.Control:
    reps, planes = workflow.listar_reportes(), workflow.listar_planes()
    sectores = workflow.listar_sectores()
    cont = {e: sum(r.estado == e for r in reps) for e in EstadoReporte}
    prios = workflow.calcular_prioridades()
    vis = workflow.visibilidad_sectores()
    sin_info = [v for v in vis if v["estado"] in ("sin_informacion_suficiente", "sin_reportes_recientes", "necesidad_no_evaluada")]
    aprobados = sum(p.estado == EstadoPlan.APROBADO for p in planes)
    propuestos = sum(p.estado == EstadoPlan.PROPUESTO for p in planes)

    kpis = ft.Row([
        C.kpi(len(reps), "Reportes totales", ft.Icons.INBOX),
        C.kpi(cont[EstadoReporte.POR_REVISAR], "Por revisar", ft.Icons.FACT_CHECK, "warn" if cont[EstadoReporte.POR_REVISAR] else ""),
        C.kpi(cont[EstadoReporte.CONFIRMADO] + cont[EstadoReporte.EN_PLAN], "Confirmados", ft.Icons.CHECK_CIRCLE, "ok"),
        C.kpi(len(prios), "Sectores priorizados", ft.Icons.INSIGHTS),
        C.kpi(len(sin_info), "Sectores sin información", ft.Icons.VISIBILITY, "warn" if sin_info else ""),
    ], spacing=12, wrap=True, run_spacing=12)

    pasos = ft.Row([ft.Container(ft.Column([ft.Text(str(v), size=18, weight=ft.FontWeight.W_800, color=T.NAVY if not fut else T.MUTED),
                                            ft.Text(n, size=11, color=T.MUTED)], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                                 bgcolor="#f4f4f4" if fut else "#eaf4fc", border=ft.Border.all(1, T.LINE), border_radius=10,
                                 padding=ft.Padding.symmetric(horizontal=12, vertical=8), width=118)
                    for n, v, fut in [("Por revisar", cont[EstadoReporte.POR_REVISAR], False), ("Confirmados", cont[EstadoReporte.CONFIRMADO], False),
                                      ("En plan", cont[EstadoReporte.EN_PLAN], False), ("Duplicados", cont[EstadoReporte.DUPLICADO], False),
                                      ("Rechazados", cont[EstadoReporte.RECHAZADO], False), ("Priorizados", len(prios), False),
                                      ("Planes propuestos", propuestos, False), ("Planes aprobados", aprobados, False)]], spacing=8, wrap=True, run_spacing=8)

    if cont[EstadoReporte.POR_REVISAR]:
        siguiente = C.boton(f"Revisar {cont[EstadoReporte.POR_REVISAR]} reportes pendientes", lambda e: ctx.ir("revision"), ft.Icons.FACT_CHECK, "secundario")
    elif prios and not planes:
        siguiente = C.boton("Generar propuesta de plan", lambda e: ctx.ir("plan"), ft.Icons.LOCAL_SHIPPING, "secundario")
    else:
        siguiente = ft.Container()

    integridad = ft.Column([
        ft.Row([ft.Text("Auditoría", size=13), C.pill_estado("ok" if audit.verificar_integridad() else "error")], spacing=8),
        ft.Row([ft.Text(f"Trazas ({tracing.verificar_integridad()['eventos']} eventos)", size=13),
                C.pill_estado("ok" if tracing.verificar_integridad()["integra"] else "error")], spacing=8),
        ft.Row([ft.Text("Proveedor de IA", size=13), C.pill("Gemini" if config.ai_provider() == "gemini" else "Local (reglas)",
                                                          "info" if config.ai_provider() == "gemini" else "neutro")], spacing=8)], spacing=6)

    info = ft.Column([ft.Text("Sin reportes ≠ sin necesidad: estos sectores no reciben prioridad automática ni cero.", size=11.5, color=T.MUTED),
                      *[ft.Row([ft.Text(v["sector"], size=12.5, expand=True), C.pill("verificar" if v["requiere_verificacion_proactiva"] else v["estado"].replace("_", " "),
                                                                                   "warn" if v["requiere_verificacion_proactiva"] else "neutro")]) for v in sin_info[:7]]],
                     spacing=5) if sin_info else C.nota("Todos los sectores tienen información suficiente.", "ok")

    return C.seccion(
        C.titulo("Métricas operativas", "Estado de reportes, planes y prioridades"),
        ft.Row([ft.Container(), C.ficticio()] if any(s.ficticio for s in sectores) else [ft.Container()], alignment=ft.MainAxisAlignment.END),
        kpis,
        C.panel(ft.Column([pasos, siguiente], spacing=10), "Recorrido operativo"),
        ft.Row([C.boton("Ver prioridades por sector", lambda e: ctx.ir("prioridad"), ft.Icons.INSIGHTS, "secundario"),
                C.boton("Ver el mapa", lambda e: ctx.ir("mapa"), ft.Icons.MAP, "secundario")], spacing=10, wrap=True),
        ft.ResponsiveRow([
            ft.Container(C.panel(integridad, "Integridad y proveedor"), col={"xs": 12, "md": 5}),
            ft.Container(C.panel(info, "Sectores sin información"), col={"xs": 12, "md": 7})], run_spacing=14))
