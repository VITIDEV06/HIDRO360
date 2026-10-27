"""Trazas técnicas JSONL: cómo se ejecutó el sistema (componente, duración, estado, Gemini o fallback local)."""

import json

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import tracing


def build(ctx) -> ft.Control:
    integ = tracing.verificar_integridad()
    todos = tracing.leer()
    cab = ft.Row([ft.Text("Cadena de hashes:", size=13), C.pill_estado("ok" if integ["integra"] else "error"),
                  ft.Text(f"{integ['eventos']} eventos" + ("" if integ["integra"] else f" · rota en la línea {integ['linea_rota']}"), size=12, color=T.MUTED)], spacing=8)
    aviso = C.nota("Las trazas explican la ejecución; no contienen el texto completo del ciudadano. La auditoría de negocio (quién decidió qué) está en otra sección.", "info")
    if not todos:
        return C.seccion(C.titulo("Trazas"), aviso, cab, C.vacio("Aún no hay trazas.", ft.Icons.TIMELINE))

    f_run = C.lista_desplegable("Ejecución (run_id)", ["(todas)"] + sorted({e["run_id"] for e in todos}, reverse=True)[:80], "(todas)", width=230)
    f_comp = C.lista_desplegable("Componente", ["(todos)"] + sorted({e["agent"] for e in todos}), "(todos)", width=210)
    f_est = C.lista_desplegable("Estado", ["(todos)", "ok", "fallback", "error"], "(todos)", width=160)
    f_desde = C.campo("Desde (AAAA-MM-DD)", "", width=180)
    salida = ft.Column([], spacing=12)

    def pintar(e=None):
        ev = tracing.leer(run_id=None if f_run.value in (None, "(todas)") else f_run.value, agent=None if f_comp.value in (None, "(todos)") else f_comp.value,
                          status=None if f_est.value in (None, "(todos)") else f_est.value, desde=(f_desde.value or "").strip() or None)
        d = [x["duration_ms"] for x in ev if x["duration_ms"] is not None]
        fallbacks, errores = sum(x["status"] == "fallback" for x in ev), sum(x["status"] == "error" for x in ev)
        kp = ft.Row([C.kpi(len(ev), "Eventos", ft.Icons.TIMELINE), C.kpi(fallbacks, "Fallbacks (IA → local)", ft.Icons.CLOUD_OFF, "warn" if fallbacks else ""),
                     C.kpi(errores, "Errores", ft.Icons.ERROR_OUTLINE, "bad" if errores else ""),
                     C.kpi(f"{sum(d) / len(d):.1f} ms" if d else "—", "Duración media", ft.Icons.SCHEDULE)], spacing=12, wrap=True, run_spacing=12)
        filas = [[x["timestamp"][11:23], x["run_id"], x["agent"], x["action"], C.pill_estado(x["status"]), x["duration_ms"], x["correlation_id"] or "—",
                  ", ".join(x["tools"]) or "—", json.dumps(x["output_summary"], ensure_ascii=False)[:80],
                  ft.IconButton(icon=ft.Icons.VISIBILITY, tooltip="Ver evento completo", icon_color=T.BLUE,
                                on_click=lambda ev_, x=x: ctx.dialogo("Evento de traza", ft.Container(
                                    ft.Text(json.dumps(x, ensure_ascii=False, indent=2), size=11.5, selectable=True), width=640, height=420), None))]
                 for x in reversed(ev)][:250]
        salida.controls = [kp, C.tabla(["Hora", "Run", "Componente", "Acción", "Estado", "ms", "Correlación", "Herramientas", "Salida", ""], filas)]
        ctx.actualizar() if e is not None else None

    for c in (f_run, f_comp, f_est):
        c.on_select = pintar
    f_desde.on_submit = pintar
    pintar()
    return C.seccion(C.titulo("Trazas", "Investigue qué componentes se ejecutaron, cuánto tardaron y qué proveedor de IA se usó"), aviso, cab,
                     C.panel(ft.Column([ft.Row([f_run, f_comp, f_est, f_desde], spacing=12, wrap=True), salida], spacing=14), "Eventos"))
