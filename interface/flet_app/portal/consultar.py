"""Portal público: consulta del estado de un reporte con su código de seguimiento (solo consultar_reporte_publico)."""

import re

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from interface.flet_app.portal import datos
from services import workflow


# Línea de progreso FIEL a los estados reales del dominio (EstadoReporte): por_revisar -> confirmado -> en_plan.
# «duplicado» y «rechazado» son estados terminales y se explican aparte (no hay línea de progreso).
PASOS = [("recibido", "Recibido"), ("revision", "En revisión"), ("confirmado", "Confirmado"), ("programada", "Atención programada")]
POSICION = {"por_revisar": 1, "confirmado": 2, "en_plan": 3}
TERMINALES = {
    "duplicado": ("Agrupado con otro reporte",
                  "Su reporte describe el mismo problema que otro ya recibido, así que se juntaron para no contarlo dos veces. "
                  "No necesita volver a reportar: lo que se decida para ese problema también lo cubre."),
    "rechazado": ("No procede como reporte",
                  "El equipo revisó su reporte y no corresponde a una falta de agua que pueda atenderse. "
                  "Si el problema continúa, puede enviar un reporte nuevo con más detalle (dónde y desde cuándo)."),
}
SIN_PROMESA = "Este estado no indica fecha ni hora de llegada del servicio."


def estado_de(d: dict) -> str:
    """Estado real del reporte. Usa la clave `estado` si el servidor la entrega; si no, lo deduce del texto público que SÍ entrega."""
    e = str(d.get("estado") or "").lower()
    if e in POSICION or e in TERMINALES:
        return e
    t = str(d.get("estado_publico") or "").lower()
    if t.startswith("atención programada") or t.startswith("atencion programada"):
        return "en_plan"
    if t.startswith("confirmado"):
        return "confirmado"
    if t.startswith("agrupado"):
        return "duplicado"
    if t.startswith("no procede"):
        return "rechazado"
    return "por_revisar"


def linea_progreso(estado: str) -> ft.Control:
    pos = POSICION[estado]
    nodos = []
    for i, (_, etiqueta) in enumerate(PASOS):
        hecho, actual = i < pos, i == pos
        color = T.GREEN if hecho else (T.BLUE if actual else "#c3cfdd")
        nodos.append(ft.Container(ft.Column([
            ft.Container(ft.Icon(ft.Icons.CHECK if hecho else (ft.Icons.RADIO_BUTTON_CHECKED if actual else ft.Icons.RADIO_BUTTON_UNCHECKED), color=T.WHITE if hecho else color, size=18),
                         width=30, height=30, bgcolor=color if hecho else None, border=None if hecho else ft.Border.all(2, color), border_radius=999, alignment=ft.Alignment.CENTER),
            ft.Text(etiqueta, size=12, color=T.NAVY if (hecho or actual) else T.MUTED, weight=ft.FontWeight.W_700 if actual else ft.FontWeight.W_500,
                    text_align=ft.TextAlign.CENTER)], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            col={"xs": 6, "sm": 3}, padding=ft.Padding.symmetric(vertical=4)))
    return ft.ResponsiveRow(nodos, run_spacing=4, spacing=4)


def _resultado(d: dict) -> ft.Control:
    est = estado_de(d)
    filas = [("Código", d.get("codigo")), ("Estado", d.get("estado_publico")), ("Sector", d.get("sector")),
             ("Recibido", datos.fecha_legible(d.get("fecha_creacion")))]
    if "tiene_ubicacion" in d:       # solo si el servidor lo informa; nunca se muestran coordenadas ni fotos
        filas.append(("Ubicación", "Marcada en el mapa" if d.get("tiene_ubicacion") else "Sin ubicación en el mapa"))
    if "n_fotos" in d:
        n = int(d.get("n_fotos") or 0)
        filas.append(("Fotos", "Sin fotos" if n == 0 else f"{n} foto{'s' if n != 1 else ''} recibida{'s' if n != 1 else ''}"))
    cuerpo = [ft.Row([ft.Text(k, size=12.5, color=T.MUTED, width=90),
                      ft.Text(str(v), size=15, weight=ft.FontWeight.W_700 if k == "Estado" else ft.FontWeight.W_400,
                              color=T.NAVY if k == "Estado" else T.INK, selectable=True, expand=True)], spacing=10)
              for k, v in filas if v]
    if est in TERMINALES:
        titulo, explicacion = TERMINALES[est]
        progreso = C.nota(f"{titulo}. {explicacion}", "info" if est == "duplicado" else "warn")
    else:
        progreso = ft.Column([linea_progreso(est), ft.Text(SIN_PROMESA, size=12, color=T.MUTED)], spacing=6)
    return C.panel(ft.Column([progreso, ft.Divider(height=4, color=T.LINE)] + cuerpo, spacing=10), "Estado de su reporte")


def build(ctx) -> ft.Control:
    codigo = C.campo("Código de seguimiento", ctx.seleccion.pop("codigo", ""), hint_text="HID-XXXXXXXX", expand=True,
                     tooltip="Es el código que recibió al enviar su reporte", autofocus=True)
    salida = ft.Column([], spacing=10)
    b = {}

    def consultar(e=None):
        valor = re.sub(r"\s+", "", codigo.value or "").upper()
        if not valor:
            codigo.error_text = "Escriba su código de seguimiento."
            ctx.actualizar()
            return
        if not re.fullmatch(r"[A-Z0-9-]{6,20}", valor):
            codigo.error_text = "El código solo lleva letras, números y guion (por ejemplo HID-ABC12345)."
            ctx.actualizar()
            return
        codigo.error_text = None
        b["x"].disabled = True
        salida.controls = [C.cargando("Buscando su reporte…")]
        ctx.actualizar()
        ok, res = ctx.ejecutar(lambda: workflow.consultar_reporte_publico(valor), publico=True)
        b["x"].disabled = False
        if ok and isinstance(res, dict) and res:
            salida.controls = [_resultado(res)]
        else:
            salida.controls = [C.nota("No encontramos un reporte con ese código. Revise que esté bien escrito (por ejemplo HID-ABC12345).", "warn")]
        ctx.actualizar()

    codigo.on_submit = consultar
    b["x"] = C.boton("Consultar", consultar, ft.Icons.SEARCH, tooltip="Consultar el estado")
    if codigo.value:
        consultar()
    return C.seccion(C.titulo("Consultar mi reporte", "Escriba el código que recibió al enviarlo"),
                     C.panel(ft.Column([ft.Row([codigo]), b["x"]], spacing=12)), salida,
                     C.nota("Por privacidad solo mostramos el estado general de su reporte.", "info"))
