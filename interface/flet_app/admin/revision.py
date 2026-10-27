"""Revisión y validación: ver lo que entendió el sistema, corregir y confirmar o rechazar con motivo."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoReporte, Severidad
from services import workflow
from services.ingest import TIPOS_INCIDENCIA

VULNERABILIDADES = ["adultos_mayores", "ninos_pequenos", "personas_enfermas"]


def build(ctx) -> ft.Control:
    reps = [r for r in workflow.listar_reportes() if r.estado in (EstadoReporte.POR_REVISAR, EstadoReporte.CONFIRMADO)]
    if not reps:
        return C.seccion(C.titulo("Revisión y validación"), C.vacio("No hay reportes por revisar. Reciba uno en la bandeja.", ft.Icons.FACT_CHECK))
    ids = [r.id for r in reps]
    sel = ctx.seleccion.get("reporte_id")
    r = next((x for x in reps if x.id == sel), reps[0])
    ctx.seleccion["reporte_id"] = r.id

    selector = C.lista_desplegable("Reporte", [(x.id, f"#{x.id} · {x.sector or 'SIN SECTOR'} · {x.estado.value} · {x.mensaje_original[:48]}…") for x in reps],
                                   str(r.id), width=620)
    selector.on_select = lambda e: ctx.ir("revision", reporte_id=int(selector.value))

    # ---- lo que entendió el sistema
    entendido = [ft.Row([C.pill_estado(r.estado.value), C.pill(f"intérprete: {r.interprete}", "info" if r.interprete == "gemini" else "neutro")], spacing=8),
                 ft.Text("Texto original (inalterable)", size=12, color=T.MUTED, weight=ft.FontWeight.W_700),
                 ft.Container(C.texto(r.mensaje_original, 14), bgcolor="#eaf4fc", border_radius=12, padding=14)]
    if r.confianza_declarada is not None:
        entendido.append(C.nota(f"Confianza declarada por el modelo: {r.confianza_declarada:.0%} (autoevaluación subjetiva, NO probabilidad calibrada).", "info"))
    if r.datos_faltantes:
        entendido.append(C.nota("Faltan: " + ", ".join(r.datos_faltantes), "bad"))
    if r.datos_inciertos:
        entendido.append(C.nota("Inciertos: " + ", ".join(r.datos_inciertos), "warn"))
    entendido += [ft.Text("• " + n, size=12, color=T.MUTED) for n in r.notas]
    if r.evidencia:
        entendido.append(ft.ExpansionTile(title=ft.Text("Fragmentos del mensaje que respaldan la extracción", size=13),
                                          controls=[ft.Text(f"{k}: «{v}»", size=12) for k, v in r.evidencia.items()]))
    if r.sector_candidatos and not r.sector:
        entendido.append(ft.Text("Opciones de sector: " + ", ".join(f"{c['sector']} ({c['metodo']}, {c['similitud']:.0%})" for c in r.sector_candidatos), size=12, color=T.NAVY))

    # ---- formulario de corrección
    catalogo = [s.nombre for s in workflow.listar_sectores()]
    f_sector = C.lista_desplegable("Sector (catálogo)", catalogo, r.sector if r.sector in catalogo else None)
    f_horas = C.campo("Horas sin agua (vacío = desconocido)", r.horas_sin_agua if r.horas_sin_agua is not None else "", keyboard_type=ft.KeyboardType.NUMBER, width=230)
    f_pers = C.campo("Personas afectadas (vacío = desconocido)", r.personas_afectadas if r.personas_afectadas is not None else "", keyboard_type=ft.KeyboardType.NUMBER, width=230)
    f_sev = C.lista_desplegable("Severidad", ["(sin dato)"] + [s.value for s in Severidad], r.severidad.value if r.severidad else "(sin dato)", width=230)
    f_tipo = C.lista_desplegable("Tipo de incidencia", ["(sin dato)"] + list(TIPOS_INCIDENCIA), r.tipo_incidencia or "(sin dato)", width=230)
    f_calle = C.campo("Calle / referencia", r.calle or r.referencia_ubicacion or "")
    f_vul = ft.Column([ft.Checkbox(label=v.replace("_", " "), value=v in r.vulnerabilidades, data=v) for v in VULNERABILIDADES], spacing=0)
    f_ese = C.campo("Servicio esencial afectado (hospital, centro de salud…; vacío = ninguno)", r.servicio_esencial or "")
    error = ft.Text("", color=T.RED, size=12.5)

    def numero(campo, tipo):
        v = (campo.value or "").strip().replace(",", ".")
        if not v:
            return None
        try:
            return tipo(float(v)) if tipo is int else float(v)
        except ValueError:
            raise ValueError(f"«{campo.label}» debe ser un número.")

    boton_confirmar = C.boton("Confirmar datos y ubicación" if r.estado == EstadoReporte.POR_REVISAR else "Guardar correcciones",
                              lambda e: confirmar(e), ft.Icons.CHECK_CIRCLE)

    def confirmar(e):
        error.value = ""
        try:
            horas, pers = numero(f_horas, float), numero(f_pers, int)
        except ValueError as ex:
            error.value = str(ex)
            ctx.actualizar()
            return
        if (horas is not None and horas < 0) or (pers is not None and pers < 0):
            error.value = "Las horas y las personas no pueden ser negativas."
            ctx.actualizar()
            return
        if not f_sector.value:
            error.value = "Elija el sector del catálogo para confirmar la ubicación."
            ctx.actualizar()
            return
        ok, _ = ctx.ejecutar(lambda: workflow.corregir_y_confirmar(
            r.id, ctx.responsable, f_sector.value, horas or None, pers or None,
            None if f_sev.value in (None, "(sin dato)") else f_sev.value, (f_calle.value or "").strip() or None,
            servicio_esencial=(f_ese.value or "").strip() or None,
            vulnerabilidades=[c.data for c in f_vul.controls if c.value],
            tipo_incidencia=None if f_tipo.value in (None, "(sin dato)") else f_tipo.value),
            f"Reporte #{r.id} confirmado. Revise posibles duplicados.", bloquear=boton_confirmar)
        if ok:
            ctx.refrescar()

    def rechazar(e):
        def hacer(motivo: str) -> bool:
            ok, _ = ctx.ejecutar(lambda: workflow.rechazar_reporte(r.id, ctx.responsable, motivo), f"Reporte #{r.id} rechazado.")
            if ok:
                ctx.seleccion.pop("reporte_id", None)
                ctx.refrescar()
            return ok

        ctx.pedir_motivo(f"Rechazar el reporte #{r.id}", "Motivo del rechazo (obligatorio)", hacer, "Rechazar")

    form = ft.Column([f_sector, ft.Row([f_horas, f_pers], spacing=12, wrap=True), ft.Row([f_sev, f_tipo], spacing=12, wrap=True), f_calle,
                      ft.Text("Personas vulnerables afectadas", size=12, color=T.MUTED), f_vul, f_ese, error,
                      ft.Row([boton_confirmar,
                              C.boton("Rechazar", rechazar, ft.Icons.BLOCK, "secundario"),
                              C.boton("Ver duplicados", lambda e: ctx.ir("duplicados", reporte_id=r.id), ft.Icons.CONTENT_COPY, "secundario")],
                             spacing=10, wrap=True)], spacing=10)
    return C.seccion(C.titulo("Revisión y validación", "Confirme o corrija lo que entendió el sistema antes de priorizar"), selector,
                     ft.ResponsiveRow([ft.Container(C.panel(ft.Column(entendido, spacing=10), "Lo que entendió el sistema"), col={"xs": 12, "lg": 6}),
                                       ft.Container(C.panel(form, "Corrección humana"), col={"xs": 12, "lg": 6})], run_spacing=14))
