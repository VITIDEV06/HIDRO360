"""Evaluación e impacto: casos de evaluación, línea base vs HIDRO360 y escenario individual (datos SINTÉTICOS)."""

import json
import subprocess
import sys
from pathlib import Path

import flet as ft

from evaluation import impacto
from interface.flet_app import components as C
from interface.flet_app import theme as T

ROOT = Path(__file__).resolve().parents[3]
RESULTADOS = ROOT / "evaluation" / "resultados"


def _json(nombre: str):
    try:
        return json.loads((RESULTADOS / nombre).read_text(encoding="utf-8"))
    except Exception:
        return None


# ------------------------------------------------------------------ pestaña casos
def _casos(ctx) -> ft.Control:
    salida = ft.Column([], spacing=12)

    def pintar():
        g = _json("ultimo.json")
        if not g:
            salida.controls = [C.vacio("Aún no hay resultados. Pulse «Ejecutar casos ahora» o use `python -m evaluation.run_cases`.", ft.Icons.LIST_ALT)]
            return
        f = g["resultados"]
        n = lambda e: sum(x["estado"] == e for x in f)  # noqa: E731
        salida.controls = [
            ft.Text(f"Último resultado: {g['generado']} · versión {g['version']}", size=12, color=T.MUTED),
            ft.Row([C.kpi(n("PASS"), "PASS", ft.Icons.CHECK_CIRCLE, "ok"), C.kpi(n("NEEDS_REVIEW"), "NEEDS_REVIEW", ft.Icons.WARNING_AMBER, "warn" if n("NEEDS_REVIEW") else ""),
                    C.kpi(n("FAIL"), "FAIL", ft.Icons.ERROR_OUTLINE, "bad" if n("FAIL") else "")], spacing=12, wrap=True),
            *[ft.Text(f"• {k}: {v}", size=12.5) for k, v in g["indicadores"].items()],
            C.tabla(["Caso", "Grupo", "Descripción", "Estado", "Explicación"],
                    [[x["id"], x["grupo"], x["descripcion"], C.pill(x["estado"], {"PASS": "ok", "FAIL": "bad"}.get(x["estado"], "warn")), x["explicacion"][:110]] for x in f])]

    def ejecutar(e):
        boton.disabled = True
        salida.controls = [C.cargando("Ejecutando casos con una base temporal (≈40 s)…")]
        ctx.actualizar()
        try:
            p = subprocess.run([sys.executable, "-m", "evaluation.run_cases"], cwd=str(ROOT), capture_output=True, text=True, timeout=400,
                               env={**__import__("os").environ, "PYTHONUTF8": "1"})
            ctx.toast("Casos ejecutados." if p.returncode == 0 else "Hay casos en FAIL: revise el detalle.", "ok" if p.returncode == 0 else "warn")
        except Exception as ex:
            ctx.toast(f"No se pudo ejecutar el runner: {type(ex).__name__}", "bad")
        boton.disabled = False
        pintar()
        ctx.actualizar()

    boton = C.boton("Ejecutar casos ahora", ejecutar, ft.Icons.PLAY_ARROW)
    pintar()
    return ft.Column([C.nota("La guía solo define los 15 casos por grupo; las entradas concretas las redactó el equipo (pendiente de contrastar con los casos "
                             "oficiales). Se ejecutan en un proceso aparte con una base temporal.", "warn"), boton, salida], spacing=12)


# ------------------------------------------------------------- pestaña experimento
def _experimento(ctx) -> ft.Control:
    exp = {"v": _json("impacto.json")}
    cuerpo = ft.Column([], spacing=12)
    n_campo = C.campo("Escenarios por celda", 50, width=190, keyboard_type=ft.KeyboardType.NUMBER)
    sel = C.lista_desplegable("Familia y oferta", [("0", "—")], "0", width=330)

    def pintar_resumen(e=None):
        if not exp["v"]:
            cuerpo.controls = [C.vacio("Ejecute `python -m evaluation.run_impacto` o pulse recalcular.", ft.Icons.BAR_CHART)]
            return
        r = exp["v"]["resumenes"][int(sel.value or 0)]
        m = r["metricas"]
        ev = r["escenarios_agua_clara_mejor_en_persona_horas"]
        ph = m["persona_horas_residuales"]
        gi = m["gini_horas_residuales"]
        cuerpo.controls = [
            C.tabla(["Métrica", "Línea base (quejas)", "HIDRO360"], [[k, v["linea_base"], v["agua_clara"]] for k, v in m.items()]),
            ft.ResponsiveRow([
                ft.Container(C.panel(ft.Column([C.barra(ph["linea_base"], max(ph.values()), T.AMBER, f"Línea base: {ph['linea_base']}"),
                                                C.barra(ph["agua_clara"], max(ph.values()), T.BLUE, f"HIDRO360: {ph['agua_clara']}")], spacing=8),
                                   "Persona-horas sin agua residuales (↓ mejor)"), col={"xs": 12, "md": 6}),
                ft.Container(C.panel(ft.Column([C.barra(r["gini_horas_antes_media"], 1, T.MUTED, f"Antes de entregar agua: {r['gini_horas_antes_media']}"),
                                                C.barra(gi["linea_base"], 1, T.AMBER, f"Línea base: {gi['linea_base']}"),
                                                C.barra(gi["agua_clara"], 1, T.BLUE, f"HIDRO360: {gi['agua_clara']}")], spacing=8),
                                   "Gini de horas residuales (↓ más equitativo)"), col={"xs": 12, "md": 6})], run_spacing=12),
            ft.Text(f"HIDRO360 mejor en {ev['mejor']} escenarios, igual en {ev['igual']}, peor en {ev['peor']}.", size=13, weight=ft.FontWeight.W_700, color=T.NAVY),
            ft.Text("Un Gini menor no demuestra por sí solo una mejor asignación: se interpreta junto con la demanda no cubierta y las persona-horas. "
                    "Limitaciones completas en docs/EXPERIMENTO_IMPACTO.md.", size=11.5, color=T.MUTED)]

    def llenar_selector():
        if exp["v"]:
            sel.options = [ft.DropdownOption(key=str(i), text=f"{r['familia']} · oferta {int(r['ratio_oferta'] * 100)} %") for i, r in enumerate(exp["v"]["resumenes"])]
            sel.value = "0"

    def recalcular(e):
        try:
            n = max(10, min(400, int(float(n_campo.value or 50))))
        except ValueError:
            ctx.toast("Indique un número de escenarios válido.", "bad")
            return
        cuerpo.controls = [C.cargando("Calculando…")]
        ctx.actualizar()
        exp["v"] = impacto.experimento_completo(n)
        llenar_selector()
        pintar_resumen()
        ctx.actualizar()

    sel.on_select = lambda e: (pintar_resumen(), ctx.actualizar())
    llenar_selector()
    pintar_resumen()
    return ft.Column([C.nota("DATOS SINTÉTICOS: describe el comportamiento de los algoritmos bajo supuestos declarados; no es evidencia de impacto real.", "warn"),
                      ft.Row([sel, n_campo, C.boton("Recalcular experimento", recalcular, ft.Icons.REFRESH, "secundario")], spacing=12, wrap=True), cuerpo], spacing=12)


# --------------------------------------------------------------- pestaña escenario
def _escenario(ctx) -> ft.Control:
    fam = C.lista_desplegable("Familia", list(impacto.FAMILIAS), impacto.FAMILIAS[0], width=210)
    ratio = C.lista_desplegable("Oferta (% de la demanda)", [(str(r), f"{int(r * 100)} %") for r in impacto.RATIOS_OFERTA], str(impacto.RATIOS_OFERTA[1]), width=210)
    semilla = C.campo("Semilla", 7, width=120, keyboard_type=ft.KeyboardType.NUMBER)
    salida = ft.Column([], spacing=10)

    def pintar(e=None):
        try:
            ev = impacto.evaluar_escenario(impacto.generar_escenario(int(float(semilla.value or 7)), fam.value, float(ratio.value)))
        except ValueError:
            ctx.toast("La semilla debe ser un número.", "bad")
            return
        filas = [[s, v["horas"], v["personas"], v["demanda"], v["reportes"], v["vulnerabilidad"], ev["linea_base"]["litros_por_sector"][s],
                  ev["agua_clara"]["litros_por_sector"][s], ev["linea_base"]["horas_residuales"][s], ev["agua_clara"]["horas_residuales"][s]]
                 for s, v in ev["verdad"].items()]
        salida.controls = [
            ft.Text(f"Gini antes: {ev['gini_horas_antes']} · después: línea base {ev['linea_base']['gini_horas_residuales']} / HIDRO360 {ev['agua_clara']['gini_horas_residuales']}", size=13),
            C.tabla(["Sector", "Horas reales", "Personas", "Demanda L", "Reportes", "Vulnerab.", "L base", "L AC", "h res. base", "h res. AC"], filas),
            ft.Text("Sin reportes → sin asignación en ambos métodos. Invisibles con necesidad: " + (", ".join(ev["invisibles_con_necesidad"]) or "ninguno")
                    + " · marcados para verificación: " + (", ".join(ev["invisibles_marcados_para_verificar"]) or "ninguno"), size=11.5, color=T.MUTED)]
        ctx.actualizar() if e is not None else None

    for c in (fam, ratio):
        c.on_select = pintar
    semilla.on_submit = pintar
    pintar()
    return ft.Column([C.nota("Escenario sintético reproducible por semilla; ambos métodos usan los mismos reportes, cisternas y planificador.", "warn"),
                      ft.Row([fam, ratio, semilla, C.boton("Ver", pintar, ft.Icons.PLAY_ARROW, "secundario")], spacing=12, wrap=True), salida], spacing=12)


def build(ctx) -> ft.Control:
    vistas = {"casos": _casos, "experimento": _experimento, "escenario": _escenario}
    actual = ctx.seleccion.get("impacto_tab", "casos")
    contenedor = ft.Container(vistas[actual](ctx))

    def cambiar(clave):
        ctx.seleccion["impacto_tab"] = clave
        ctx.refrescar()

    botones = ft.Row([C.boton(t, lambda e, k=k: cambiar(k), None, "primario" if k == actual else "secundario")
                      for k, t in (("casos", "Casos de evaluación"), ("experimento", "Línea base vs HIDRO360"), ("escenario", "Escenario individual"))],
                     spacing=8, wrap=True)
    return C.seccion(C.titulo("Evaluación e impacto", "Resultados reproducibles; los datos del experimento son sintéticos"), botones, C.panel(contenedor))
