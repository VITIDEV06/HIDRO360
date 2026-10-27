"""Planificación y aprobación humana: generar propuesta, editar asignaciones, aprobar o rechazar."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import AccionDecision, Asignacion, EstadoPlan
from services import planner, workflow


def build(ctx) -> ft.Control:
    cist = workflow.listar_cisternas()
    tabla_cist = C.tabla(["Cisterna", "Estado", "Capacidad L", "Disponible L", "Sede", "Elegible para"],
                         [[c.id, c.estado.value, c.capacidad_litros, c.litros_disponibles, c.sector_base or "—",
                           ", ".join(c.sectores_elegibles) if c.sectores_elegibles else "todos"] for c in cist])
    metodo = C.lista_desplegable("Método", list(planner.METODOS), "flujo", width=160)

    boton_generar = C.boton("Generar propuesta de plan", lambda e: generar(e), ft.Icons.PLAY_ARROW)

    def generar(e):
        ok, _ = ctx.ejecutar(lambda: workflow.generar_plan(ctx.responsable, metodo.value or "flujo"),
                             "Propuesta generada. Revísela: aún NO está aprobada.", bloquear=boton_generar)
        if ok:
            ctx.seleccion.pop("plan_id", None)
            ctx.refrescar()

    cabecera = C.panel(ft.Column([tabla_cist, ft.Row([metodo, boton_generar], spacing=12, wrap=True)], spacing=12),
                       "Cisternas")
    planes = workflow.listar_planes()
    if not planes:
        return C.seccion(C.titulo("Planificación y aprobación"), cabecera,
                         C.vacio("Genere una propuesta cuando tenga reportes confirmados.", ft.Icons.LOCAL_SHIPPING))

    plan = next((p for p in planes if p.id == ctx.seleccion.get("plan_id")), planes[-1])
    ctx.seleccion["plan_id"] = plan.id
    selector = C.lista_desplegable("Plan", [(p.id, f"Plan #{p.id} · {p.estado.value} · v{p.version}{' · editado' if p.editado else ''}") for p in reversed(planes)],
                                   str(plan.id), width=330)
    selector.on_select = lambda e: ctx.ir("plan", plan_id=int(selector.value))
    editable = plan.estado == EstadoPlan.PROPUESTO
    rs = plan.resumen

    kpis = ft.Row([C.kpi(rs.get("litros_disponibles"), "Litros disponibles", ft.Icons.WATER_DROP), C.kpi(rs.get("litros_asignados"), "Litros asignados", ft.Icons.LOCAL_SHIPPING, "ok"),
                   C.kpi(len(rs.get("sectores_pendientes", [])), "Sectores pendientes", ft.Icons.WARNING_AMBER, "warn" if rs.get("sectores_pendientes") else ""),
                   C.kpi("flujo (óptimo)" if plan.metodo.startswith("flujo") else "voraz", "Método", ft.Icons.TUNE)], spacing=12, wrap=True, run_spacing=12)

    por_cis = {}
    for a in plan.asignaciones:
        por_cis[a.cisterna_id] = por_cis.get(a.cisterna_id, 0) + a.litros
    uso = ft.Column([C.barra(por_cis.get(c.id, 0), min(c.litros_disponibles, c.capacidad_litros) if c.estado.value == "disponible" else 0,
                             T.BLUE, f"{c.id}: {por_cis.get(c.id, 0)} / {min(c.litros_disponibles, c.capacidad_litros) if c.estado.value == 'disponible' else 0} L ({c.estado.value})")
                     for c in cist], spacing=8)

    filas_ed = []   # (asignacion, dropdown, campo litros)
    for a in plan.asignaciones:
        dd = C.lista_desplegable("Cisterna", [c.id for c in cist], a.cisterna_id, width=150, disabled=not editable)
        lt = C.campo("Litros (0 = quitar)", a.litros, width=150, disabled=not editable, keyboard_type=ft.KeyboardType.NUMBER)
        filas_ed.append((a, dd, lt))
    editor = ft.Column([ft.Row([ft.Text(a.sector, width=150, size=13, weight=ft.FontWeight.W_700, color=T.NAVY), dd, lt,
                                ft.Text(f"prioridad {a.prioridad}", size=11.5, color=T.MUTED), ft.Text(a.nota, size=11.5, color=T.MUTED)],
                               spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)
                        for a, dd, lt in filas_ed], spacing=6) if filas_ed else C.vacio("El plan no tiene asignaciones.")

    pend = C.tabla(["Sector", "Prioridad", "Requeridos L", "Cubiertos L", "Faltan L", "Motivo"],
                   [[p.sector, p.prioridad, p.litros_requeridos, p.litros_cubiertos, p.litros_faltantes, p.motivo] for p in plan.pendientes]) \
        if plan.pendientes else C.nota("Todos los sectores quedan cubiertos.", "ok")

    motivo = ft.TextField(label="Motivo / comentario", multiline=True, min_lines=2, border_radius=10, border_color="#cfdcec",
                          helper="Obligatorio para rechazar, para guardar modificaciones y para aprobar un plan editado.")

    def nuevas():
        out = []
        for a, dd, lt in filas_ed:
            try:
                litros = int(float((lt.value or "0").replace(",", ".")))
            except ValueError:
                raise ValueError(f"Litros inválidos para {a.sector}.")
            if litros > 0:
                out.append(Asignacion(sector=a.sector, cisterna_id=dd.value or a.cisterna_id, litros=litros, prioridad=a.prioridad, nota=a.nota))
        return out

    def guardar(e):
        try:
            lista = nuevas()
        except ValueError as ex:
            ctx.toast(str(ex), "bad")
            return
        ok, _ = ctx.ejecutar(lambda: workflow.editar_plan(plan.id, lista, ctx.responsable, motivo.value or ""), "Modificaciones guardadas y auditadas.")
        if ok:
            ctx.refrescar()

    def decidir(accion, ok_msg, peligro):
        if accion == AccionDecision.RECHAZAR and not (motivo.value or "").strip():
            motivo.error_text = "Escriba por qué rechaza el plan."
            ctx.actualizar()
            return
        motivo.error_text = None
        def hacer():
            ok, _ = ctx.ejecutar(lambda: workflow.decidir_plan(plan.id, accion, ctx.responsable, motivo.value or ""), ok_msg)
            if ok:
                ctx.refrescar()
        ctx.confirmar("Aprobar el plan" if accion == AccionDecision.APROBAR else "Rechazar el plan",
                      f"{ 'Se aprobará' if accion == AccionDecision.APROBAR else 'Se rechazará'} el plan #{plan.id} a nombre de «{ctx.responsable}». "
                      "Queda registrado en la auditoría y no se envía ninguna comunicación.", hacer, peligro)

    acciones = ft.Column([C.nota("Las asignaciones son una PROPUESTA: requieren aprobación humana. Nada se envía ni se publica automáticamente.", "info"), motivo,
                          ft.Row([C.boton("Guardar modificaciones", guardar, ft.Icons.SAVE, "secundario"),
                                  C.boton("Aprobar plan", lambda e: decidir(AccionDecision.APROBAR, "Plan aprobado y registrado en auditoría. Nada se ha enviado.", False), ft.Icons.CHECK_CIRCLE),
                                  C.boton("Rechazar plan", lambda e: decidir(AccionDecision.RECHAZAR, "Plan rechazado y registrado en auditoría.", True), ft.Icons.BLOCK, "peligro")],
                                 spacing=10, wrap=True)], spacing=10) if editable \
        else C.nota(f"El plan está «{plan.estado.value}» y ya no se puede modificar.", "ok" if plan.estado == EstadoPlan.APROBADO else "bad")

    return C.seccion(
        C.titulo("Planificación y aprobación", "Propuesta determinista; una persona la revisa, edita y aprueba"), cabecera,
        ft.Row([selector, C.pill_estado(plan.estado.value)], spacing=12),
        kpis,
        ft.Text(f"Demanda estimada: {plan.litros_por_persona} L/persona (demo) · reglas {plan.version_reglas} · el plan no incluye horas de llegada ni rutas.", size=11.5, color=T.MUTED),
        ft.ResponsiveRow([ft.Container(C.panel(uso, "Uso de cisternas"), col={"xs": 12, "lg": 5}),
                          ft.Container(C.panel(editor, "Asignaciones" + (" (editables)" if editable else "")), col={"xs": 12, "lg": 7})], run_spacing=14),
        C.panel(pend, "Pendientes (no cubiertos)"), C.panel(acciones, "Supervisión humana"))
