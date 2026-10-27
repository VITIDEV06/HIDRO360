"""Ubicación y posibles duplicados. El sistema sugiere; nada se fusiona sin una decisión humana."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoReporte
from services import geoloc, workflow


def build(ctx) -> ft.Control:
    reps = [r for r in workflow.listar_reportes() if r.estado in (EstadoReporte.POR_REVISAR, EstadoReporte.CONFIRMADO)]
    if not reps:
        return C.seccion(C.titulo("Ubicación y duplicados"), C.vacio("No hay reportes activos.", ft.Icons.PLACE))
    r = next((x for x in reps if x.id == ctx.seleccion.get("reporte_id")), reps[0])
    ctx.seleccion["reporte_id"] = r.id
    selector = C.lista_desplegable("Reporte", [(x.id, f"#{x.id} · {x.sector or 'SIN SECTOR'} · {x.estado.value} · {x.mensaje_original[:48]}…") for x in reps],
                                   str(r.id), width=620)
    selector.on_select = lambda e: ctx.ir("duplicados", reporte_id=int(selector.value))

    sec = geoloc.resolver_sector(r.sector, workflow.listar_sectores())
    if sec is None:
        ubic = C.nota("Sin sector reconocido. Corríjalo en «Revisión y validación»." + (
            " Opciones: " + ", ".join(c["sector"] for c in r.sector_candidatos) if r.sector_candidatos else ""), "bad")
    else:
        ubic = ft.Column([ft.Row([C.kpi(sec.nombre, "Sector", ft.Icons.PLACE), C.kpi(f"{sec.poblacion_estimada:,}".replace(",", " "), "Población estimada", ft.Icons.PEOPLE),
                                  C.kpi(sec.vulnerabilidad.value, "Vulnerabilidad", ft.Icons.SHIELD)], spacing=12, wrap=True),
                          C.nota("Ubicación propuesta, aún sin confirmar (vaya a Revisión y validación).", "warn") if r.estado == EstadoReporte.POR_REVISAR else ft.Container(),
                          ft.Text(f"Coordenadas del catálogo (ficticias): {sec.lat}, {sec.lng}", size=11.5, color=T.MUTED)], spacing=10)

    cands = workflow.sugerir_duplicados(r.id) if sec else []
    tarjetas = []
    for c in cands:
        otro = workflow.obtener_reporte(c.reporte_id)
        comentario = C.campo("Comentario (opcional)", "")

        def decidir(es, otro_id=otro.id, com=comentario):
            def hacer():
                ok, _ = ctx.ejecutar(lambda: workflow.resolver_duplicado(r.id, otro_id, es, ctx.responsable, com.value or ""),
                                     f"#{r.id} marcado como duplicado de #{otro_id}." if es else "Decisión registrada: no son duplicados.")
                if ok:
                    ctx.refrescar()
            if es:     # excluye el reporte del cálculo de prioridad: se confirma
                ctx.confirmar("Marcar como duplicado", f"El reporte #{r.id} dejará de contar en la prioridad y quedará agrupado con #{otro_id}. Queda en la auditoría.", hacer)
            else:
                hacer()

        tarjetas.append(C.panel(ft.Column([
            ft.Row([ft.Text(f"Posible duplicado de #{otro.id}", size=15, weight=ft.FontWeight.W_700, color=T.NAVY),
                    C.pill(f"similitud {c.similitud:.0%}", "warn"), C.pill("dudoso", "warn")], spacing=8, wrap=True),
            *[ft.Text("• " + m, size=12, color=T.MUTED) for m in c.motivos],
            ft.Text(f"Este reporte: {r.mensaje_original}", size=12), ft.Text(f"#{otro.id}: {otro.mensaje_original}", size=12),
            comentario,
            ft.Row([C.boton("Sí, es el mismo incidente (excluir del cálculo)", lambda e, f=decidir: f(True), ft.Icons.CHECK),
                    C.boton("No, son casos distintos", lambda e, f=decidir: f(False), ft.Icons.CLOSE, "secundario")], spacing=10, wrap=True)],
            spacing=8)))
    cuerpo = tarjetas or ([C.nota("Sin posibles duplicados con el umbral actual.", "ok")] if sec else [])
    if r.duplicado_de:
        cuerpo.append(C.nota(f"Este reporte fue marcado como duplicado de #{r.duplicado_de}.", "info"))
    return C.seccion(C.titulo("Ubicación y duplicados", "El sistema solo sugiere; una repetición puede significar que el problema continúa"),
                     selector, C.panel(ubic, "Ubicación"), *cuerpo)
