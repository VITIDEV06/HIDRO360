"""Recomendaciones IA (supervisión humana): analizar un reporte, ver la procedencia de CADA etapa (gemini / reglas /
reglas(fallback)), y aprobar, modificar o rechazar. La IA solo propone: la misión existe únicamente tras la decisión de una persona."""

import flet as ft

from interface.flet_app import backend
from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoReporte
from services import workflow

ESTADO_REC = {"propuesta": ("propuesta · espera su decisión", "warn"), "aprobada": ("aprobada", "ok"), "modificada": ("modificada y aprobada", "ok"),
              "rechazada": ("rechazada", "bad")}
ETAPAS = {"validacion": "Validación", "duplicados": "Duplicados", "clasificacion": "Clasificación", "prioridad": "Prioridad",
          "recomendacion": "Recomendación", "asignacion": "Asignación"}


def pill_procedencia(proveedor: str) -> ft.Control:
    """Distingue con claridad de dónde sale cada resultado. «reglas(fallback)» nunca se presenta como IA."""
    p = (proveedor or "").lower()
    if p == "gemini":
        return C.pill("Gemini (IA)", "info")
    if "fallback" in p:
        return C.pill("reglas · respaldo: la IA no respondió", "warn")
    return C.pill("reglas locales (sin IA)", "neutro")


def _valor(o, k, d=None):
    return getattr(o, k, d) if not isinstance(o, dict) else o.get(k, d)


def build(ctx) -> ft.Control:
    cabecera = C.titulo("Recomendaciones IA", "Propuestas de apoyo para decidir; la IA no aprueba, no asigna y no calcula la prioridad por sí sola")
    nota = C.nota("Cada recomendación es una PROPUESTA. Solo cuando una persona la aprueba (o la modifica) se crea la misión. "
                  "Cada etapa indica si la hizo Gemini, las reglas locales o las reglas por respaldo (fallback) si la IA falló.", "info")
    reps = [r for r in workflow.listar_reportes() if r.estado != EstadoReporte.RECHAZADO]
    recs, error = backend.leer("listar_recomendaciones", [])
    if error and not recs:
        return C.seccion(cabecera, nota, C.nota(f"No se pudieron cargar las recomendaciones: {error}", "bad"))
    recs = list(recs or [])
    cisternas = workflow.listar_cisternas()

    # ---------------------------------------------------------------- analizar
    sel_rep = ctx.seleccion.get("reporte_id")
    ids = [r.id for r in reps]
    f_rep = C.lista_desplegable("Reporte a analizar", [(r.id, f"#{r.id} · {r.sector or 'sin sector'} · {r.mensaje_original[:50]}…") for r in reps],
                                str(sel_rep) if sel_rep in ids else None, width=560)
    estado_analisis = ft.Row([], spacing=8)

    def analizar(e):
        if not f_rep.value:
            ctx.toast("Elija primero el reporte que quiere analizar.", "warn")
            return
        rid = int(f_rep.value)
        estado_analisis.controls = [C.cargando("Analizando…")]
        ctx.actualizar()
        ok, rec = ctx.ejecutar(lambda: backend.llamar("analizar_reporte", rid, ctx.responsable),
                               f"Análisis del reporte #{rid} listo. Revise la propuesta.")
        estado_analisis.controls = []
        if ok:
            ctx.ir("recomendaciones", reporte_id=rid, recomendacion_id=_valor(rec, "id"))
        else:
            ctx.actualizar()

    panel_analizar = C.panel(ft.Column([ft.Row([f_rep]), ft.Row([C.boton("Analizar reporte", analizar, ft.Icons.AUTO_AWESOME, tooltip="Genera una recomendación (propuesta)"),
                                                                 estado_analisis], spacing=12)], spacing=10), "Pedir un análisis")

    # ------------------------------------------------------------------ filtro
    f_estado = C.lista_desplegable("Mostrar", [("propuesta", "Esperando decisión"), ("todas", "Todas")], "propuesta", width=220)
    lista = ft.Column([], spacing=14)

    def decidir(rec, accion, motivo, cisterna=None, paradas=None):
        etiqueta = {"aprobar": "aprobada", "modificar": "modificada y aprobada", "rechazar": "rechazada"}[accion]
        ok, res = ctx.ejecutar(lambda: backend.llamar("decidir_recomendacion", _valor(rec, "id"), accion, ctx.responsable, (motivo or "").strip(),
                                                      cisterna_id=cisterna, paradas=paradas), f"Recomendación #{_valor(rec, 'id')} {etiqueta}.")
        if not ok:
            return
        mision_id = _valor(res, "mision_id")
        if mision_id is None:
            with ctx.con_actor():
                nueva, _ = backend.leer("obtener_recomendacion", None, _valor(rec, "id"))
            mision_id = _valor(nueva, "mision_id") if nueva is not None else None
        if mision_id is not None:
            ctx.toast(f"Se creó la misión #{mision_id}. Aquí puede seguir su avance.", "ok")
            ctx.ir("misiones", mision_id=mision_id)      # muestra la misión creada
        else:
            ctx.refrescar()

    def tarjeta(rec) -> ft.Control:
        etapas = [[ETAPAS.get(_valor(x, "nombre"), _valor(x, "nombre")), pill_procedencia(_valor(x, "proveedor")),
                   C.pill_estado(_valor(x, "estado", "")), f"{_valor(x, 'duracion_ms', 0)} ms",
                   ft.Text(str(_valor(x, "resumen", "")), size=12, width=300)] for x in (_valor(rec, "etapas", []) or [])]
        estado = str(_valor(rec, "estado").value if hasattr(_valor(rec, "estado"), "value") else _valor(rec, "estado"))
        etiqueta, tipo = ESTADO_REC.get(estado, (estado, "neutro"))
        datos = []
        prioridad = _valor(rec, "prioridad_sugerida")
        if prioridad is not None:
            datos.append(C.kpi(f"{prioridad:.0f}" if isinstance(prioridad, (int, float)) else prioridad, f"Prioridad sugerida{' · ' + _valor(rec, 'categoria') if _valor(rec, 'categoria') else ''}", ft.Icons.INSIGHTS, ancho=260))
        if _valor(rec, "cisterna_sugerida"):
            datos.append(C.kpi(_valor(rec, "cisterna_sugerida"), "Cisterna sugerida", ft.Icons.LOCAL_SHIPPING, ancho=220))
        duplicados = _valor(rec, "posibles_duplicados", []) or []
        partes = [ft.Row([ft.Text(f"Recomendación #{_valor(rec, 'id')} · reporte #{_valor(rec, 'reporte_id')}", size=16, weight=ft.FontWeight.W_800, color=T.NAVY),
                          C.pill(etiqueta, tipo), pill_procedencia(_valor(rec, "proveedor"))], spacing=10, wrap=True, run_spacing=4),
                  ft.Text(f"Generada {str(_valor(rec, 'fecha'))[:16].replace('T', ' ')}", size=11.5, color=T.MUTED)]
        if datos:
            partes.append(ft.Row(datos, spacing=12, wrap=True, run_spacing=12))
        if _valor(rec, "accion_sugerida"):
            partes.append(ft.Container(ft.Text(_valor(rec, "accion_sugerida"), size=13.5, color=T.INK, selectable=True), bgcolor="#eaf4fc", border_radius=12, padding=12))
        partes.append(ft.Text("Etapas del análisis y quién las hizo", size=12.5, weight=ft.FontWeight.W_700, color=T.NAVY))
        partes.append(C.tabla(["Etapa", "Procedencia", "Resultado", "Duración", "Resumen"], etapas) if etapas else C.nota("Sin detalle de etapas.", "warn"))
        if duplicados:
            partes.append(C.nota("Posibles duplicados (solo sugeridos; nada se fusiona): " + ", ".join(f"#{d}" for d in duplicados), "warn"))
        paradas = list(_valor(rec, "paradas_sugeridas", []) or [])
        if paradas:
            partes.append(ft.Text("Paradas sugeridas", size=12.5, weight=ft.FontWeight.W_700, color=T.NAVY))
            partes.append(C.tabla(["Orden", "Sector", "Litros", "Reportes", "Instrucción"],
                                  [[_valor(p, "orden"), _valor(p, "sector"), _valor(p, "litros"), ", ".join(f"#{i}" for i in _valor(p, "reporte_ids", []) or []) or "—",
                                    _valor(p, "instruccion") or "—"] for p in paradas]))
        else:
            partes.append(C.nota("La recomendación no incluye paradas: no hay ninguna misión que crear con ella.", "info"))

        if estado == "propuesta":
            motivo = C.campo("Motivo (obligatorio para rechazar o modificar)", "", expand=True)
            f_cis = C.lista_desplegable("Cisterna", [c.id for c in cisternas], _valor(rec, "cisterna_sugerida"), width=170)
            litros = [C.campo(f"Litros · parada {_valor(p, 'orden')} · {_valor(p, 'sector')} (0 = quitar)", _valor(p, "litros"), width=300,
                              keyboard_type=ft.KeyboardType.NUMBER) for p in paradas]
            editor = ft.Column([ft.Text("Ajuste la cisterna y los litros de cada parada antes de aprobar.", size=12, color=T.MUTED), f_cis,
                                ft.Row(litros, spacing=10, wrap=True, run_spacing=8)], spacing=8, visible=False)

            def aprobar(e):
                ctx.confirmar("Aprobar la recomendación", f"Se aprobará la recomendación #{_valor(rec, 'id')} a nombre de «{ctx.responsable}» y se creará la misión "
                              "para la cisterna indicada. Queda registrado en la auditoría.", lambda: decidir(rec, "aprobar", motivo.value))

            def rechazar(e):
                if not (motivo.value or "").strip():
                    motivo.error_text = "Escriba por qué la rechaza."
                    ctx.actualizar()
                    return
                motivo.error_text = None
                decidir(rec, "rechazar", motivo.value)

            def modificar(e):
                if not editor.visible:
                    editor.visible = True
                    ctx.actualizar()
                    return
                if not (motivo.value or "").strip():
                    motivo.error_text = "Explique qué cambió y por qué."
                    ctx.actualizar()
                    return
                nuevas = []
                try:
                    for p, campo_l in zip(paradas, litros):
                        n = int(float((campo_l.value or "0").replace(",", ".")))
                        if n < 0:
                            raise ValueError
                        if n > 0:
                            nuevas.append(p.model_copy(update={"litros": n, "orden": len(nuevas) + 1}) if hasattr(p, "model_copy") else {**p, "litros": n, "orden": len(nuevas) + 1})
                except ValueError:
                    ctx.toast("Los litros deben ser números enteros (0 o más).", "bad")
                    return
                if not nuevas:
                    ctx.toast("Debe quedar al menos una parada con litros. Para descartarla use «Rechazar».", "bad")
                    return
                motivo.error_text = None
                decidir(rec, "modificar", motivo.value, cisterna=f_cis.value, paradas=nuevas)

            partes += [ft.Divider(height=4, color=T.LINE), ft.Row([motivo]), editor,
                       ft.Row([C.boton("Aprobar", aprobar, ft.Icons.CHECK_CIRCLE, tooltip="Aprueba y crea la misión"),
                               C.boton("Modificar", modificar, ft.Icons.EDIT, "secundario", tooltip="Cambia cisterna o litros y aprueba (pide motivo)"),
                               C.boton("Rechazar", rechazar, ft.Icons.BLOCK, "peligro", tooltip="Descarta la propuesta (motivo obligatorio)")], spacing=10, wrap=True)]
        else:
            if _valor(rec, "decidida_por"):
                partes.append(ft.Text(f"Decidió: {_valor(rec, 'decidida_por')}" + (f" · motivo: {_valor(rec, 'motivo_decision')}" if _valor(rec, "motivo_decision") else ""),
                                      size=12.5, color=T.INK))
            if _valor(rec, "mision_id") is not None:
                mid = _valor(rec, "mision_id")
                partes.append(ft.Row([C.nota(f"Misión #{mid} creada a partir de esta decisión.", "ok"),
                                      C.boton("Ver misión", lambda e, m=mid: ctx.ir("misiones", mision_id=m), ft.Icons.LOCAL_SHIPPING, "secundario")], spacing=10, wrap=True))
        resaltada = ctx.seleccion.get("recomendacion_id") == _valor(rec, "id")
        panel = C.panel(ft.Column(partes, spacing=10))
        if resaltada:
            panel.border = ft.Border.all(2, T.BLUE)
        return panel

    def pintar(e=None):
        visibles = [r for r in recs if f_estado.value == "todas" or str(_valor(_valor(r, "estado"), "value", _valor(r, "estado"))) == "propuesta"]
        destacada = ctx.seleccion.get("recomendacion_id")
        visibles.sort(key=lambda r: (_valor(r, "id") != destacada, -int(_valor(r, "id", 0) or 0)))
        lista.controls = [tarjeta(r) for r in visibles] or [C.vacio(
            "No hay recomendaciones esperando decisión. Elija un reporte y pulse «Analizar reporte»." if f_estado.value != "todas" else
            "Aún no se ha analizado ningún reporte.", ft.Icons.AUTO_AWESOME)]
        if e is not None:
            ctx.actualizar()

    f_estado.on_select = pintar
    pintar()
    return C.seccion(cabecera, nota, panel_analizar, ft.Row([f_estado]), lista)
