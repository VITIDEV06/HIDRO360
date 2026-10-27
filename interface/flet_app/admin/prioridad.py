"""Priorización por sector: puntaje, factores y razones, resumen con IA (opcional) y visibilidad de sectores."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import workflow


def build(ctx) -> ft.Control:
    prios = workflow.calcular_prioridades()
    vis = workflow.visibilidad_sectores()
    if prios:
        maximo = max(p.puntaje for p in prios) or 1
        detalle = []
        for p in prios:
            resumen = ft.Column([], spacing=6)
            boton = None

            def explicar(e, sector=p.sector, caja=resumen):
                caja.controls = [C.cargando("Redactando resumen…")]
                ctx.actualizar()
                ok, res = ctx.ejecutar(lambda: workflow.explicar_prioridad_ia(sector))
                caja.controls = [ft.Row([C.pill(f"origen: {res['origen']}", "info" if res["origen"] == "gemini" else "neutro")]),
                                 ft.Text(res["texto"], size=12.5, selectable=True)] if ok else []
                ctx.actualizar()

            detalle.append(ft.ExpansionTile(
                title=ft.Row([ft.Text(p.sector, size=14, weight=ft.FontWeight.W_700, color=T.NAVY),
                              ft.Text(f"{p.puntaje} pts", size=13, color=T.MUTED), C.pill_estado(p.categoria)], spacing=10),
                controls=[ft.Container(ft.Column([
                    C.tabla(["Factor", "Dato", "Normalizado", "Peso", "Puntos", "Explicación"],
                            [[f.nombre, f.valor, f.normalizado, f.peso, f.contribucion, f.explicacion] for f in p.factores]),
                    *[ft.Text("• " + x, size=12, color=T.MUTED) for x in p.razones],
                    ft.Text(f"Reportes considerados: {p.reportes_ids}", size=11.5, color=T.MUTED),
                    C.boton("Explicar con IA", explicar, ft.Icons.AUTO_AWESOME, "secundario"), resumen], spacing=8), padding=14)]))
        ranking = ft.Column([ft.Row([ft.Text(f"{i + 1}", width=22, color=T.MUTED), ft.Text(p.sector, width=130, size=13, weight=ft.FontWeight.W_600),
                                     ft.Container(C.barra(p.puntaje, maximo, T.CATEGORIA_COLOR.get(p.categoria, T.BLUE)), expand=True),
                                     ft.Text(f"{p.puntaje}", width=48, size=13, weight=ft.FontWeight.W_800, color=T.NAVY), C.pill_estado(p.categoria)],
                                    spacing=10) for i, p in enumerate(prios)], spacing=8)
        cuerpo = [C.panel(ranking, "Ranking de sectores"), C.panel(ft.Column(detalle, spacing=2), "Desglose y razones")]
    else:
        cuerpo = [C.vacio("Aún no hay reportes confirmados. Confirme reportes en «Revisión y validación».", ft.Icons.INSIGHTS)]

    tabla_vis = C.tabla(["Sector", "Estado", "Confirmados", "Por revisar", "Vulnerabilidad", "Antecedentes (ficticios)", "Verificar", "Fuente"],
                        [[v["sector"], v["estado"].replace("_", " "), v["reportes_confirmados"], v["reportes_por_revisar"], v["vulnerabilidad"],
                          v["antecedentes_interrupciones"] if v["antecedentes_interrupciones"] is not None else "sin dato",
                          C.pill("verificar", "warn") if v["requiere_verificacion_proactiva"] else "", v["fuente_datos"]] for v in vis])
    return C.seccion(
        C.titulo("Priorización", "40 % horas sin agua + 30 % población afectada + 30 % vulnerabilidad · las quejas repetidas no suman"),
        *cuerpo,
        C.panel(ft.Column([C.nota("Ausencia de reportes no es ausencia de necesidad: estos sectores no reciben prioridad crítica ni cero; se marcan para verificación.", "warn"),
                           tabla_vis], spacing=10), "Visibilidad de sectores"))
