"""Asistente local de demostración: responde con reglas y preguntas frecuentes fijas.

No usa inteligencia artificial, ni Internet, ni Telegram. No guarda nada ni envía incidencias a ninguna institución:
solo orienta al ciudadano hacia el formulario o la consulta. Su identidad es explícita en pantalla.
"""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T

TEMAS = {
    "reportar": ("Cómo reportar", "Para reportar: escriba qué pasa y desde cuándo, indique la comunidad o el sector, "
                 "marque el lugar en el mapa si puede y pulse «Enviar reporte». Al terminar le damos un código de seguimiento."),
    "consultar": ("Cómo consultar", "Para consultar, escriba el código que recibió (formato HID-XXXXXXXX) en «Consultar mi reporte». "
                  "Solo verá el estado general de su reporte."),
    "estados": ("Qué significan los estados",
                "Recibido o en revisión: el reporte llegó y una persona lo revisa. "
                "Confirmado: fue validado y está en evaluación. "
                "En atención: está programado, pero no tenemos una hora de llegada confirmada. "
                "Agrupado: es el mismo problema de otro reporte. "
                "No procede: no puede atenderse como solicitud."),
    "informacion": ("Qué información dar",
                    "Ayuda indicar la calle o una referencia cercana, desde cuándo falta el agua, cuántas personas están afectadas, "
                    "si hay niños pequeños, adultos mayores o personas enfermas, y una foto si es posible (sin mostrar personas)."),
    "continua": ("Si el problema continúa",
                 "Consulte el estado de su reporte con su código. Si el problema continúa o es una emergencia, llame al 911."),
    "hora": ("Hora de llegada",
             "No podemos indicar una hora de llegada del servicio. Consulte el estado de su reporte o los avisos de su comunidad."),
}

NO_ENTIENDO = ("No entendí su pregunta. Puede elegir un tema: cómo reportar, cómo consultar, qué significan los estados, "
               "qué información dar o qué hacer si el problema continúa.")

# Palabras clave en orden de prioridad (la primera coincidencia gana).
REGLAS = [
    ("hora", ("hora", "cuando llega", "cuándo llega", "a qué hora")),
    ("continua", ("continua", "continúa", "sigue", "todavía", "todavia", "emergencia")),
    ("estados", ("estado", "significa", "recibido", "confirmado", "atención", "atencion")),
    ("consultar", ("consult", "código", "codigo", "seguimiento", "HID-")),
    ("informacion", ("informacion", "información", "datos", "qué poner", "que poner")),
    ("reportar", ("reportar", "reporte", "falta", "sin agua", "no hay agua", "denunciar")),
]


def responder(texto: str):
    """Devuelve (tema, respuesta). Función pura: no toca base de datos ni red."""
    t = (texto or "").strip()
    if not t:
        return None, "Escriba su pregunta o elija un tema."
    bajo = t.lower()
    for tema, palabras in REGLAS:
        if any(p.lower() in bajo for p in palabras):
            return tema, TEMAS[tema][1]
    return None, NO_ENTIENDO


def build(ctx) -> ft.Control:
    from services import asistente_ia  # import tardío: la pantalla de temas sigue funcionando si falla el orquestador

    origen = ft.Text("", size=12, color=T.MUTED, italic=True)
    respuesta = ft.Text("", size=14.5, color=T.INK)
    cargando = ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2),
                       ft.Text("Procesando su pregunta…", size=13, color=T.MUTED)], visible=False, spacing=10)
    tarjeta_respuesta = ft.Container(
        ft.Column([ft.Text("Asistente", size=13, weight=ft.FontWeight.W_700, color=T.BLUE), respuesta, origen], spacing=6),
        bgcolor=T.WHITE, border=ft.Border.all(1, T.LINE), border_radius=14, padding=16, visible=False)
    entrada = ft.TextField(hint_text="Escriba su pregunta, por ejemplo: ¿cómo reporto?", multiline=False, expand=True,
                           border_radius=10, text_size=14)
    borrador = {"texto": None}
    boton_guardar = C.boton("Confirmar y guardar reporte", lambda e: guardar(), ft.Icons.CHECK, "primario", visible=False)

    def mostrar_tarjeta(texto, etiqueta_origen):
        respuesta.value = texto
        origen.value = etiqueta_origen
        cargando.visible = False
        tarjeta_respuesta.visible = True
        ctx.actualizar()

    def mostrar_tema(clave):
        etiqueta, texto = TEMAS[clave]
        borrador["texto"] = None
        boton_guardar.visible = False
        mostrar_tarjeta(texto, "Pregunta frecuente (reglas locales)")

    def preguntar(_):
        pregunta = (entrada.value or "").strip()
        if not pregunta:
            return
        borrador["texto"] = None
        boton_guardar.visible = False
        tarjeta_respuesta.visible = False
        cargando.visible = True
        ctx.actualizar()
        ok, r = ctx.ejecutar(lambda: asistente_ia.responder(pregunta), publico=True)
        if not ok or r is None:
            cargando.visible = False
            ctx.actualizar()
            return
        etiqueta = {"gemini": "Respuesta generada con Gemini",
                    "reglas": "Respuesta con reglas locales",
                    "reglas(fallback)": "Respuesta con reglas locales (Gemini no respondió)"}.get(r.origen, "Respuesta")
        if r.herramientas:
            etiqueta += " · consultó: " + ", ".join(r.herramientas)
        if r.borrador:
            borrador["texto"] = r.borrador
            boton_guardar.visible = True
            etiqueta += " · borrador sin guardar"
        mostrar_tarjeta(r.texto, etiqueta)

    def guardar():
        if not borrador["texto"]:
            return
        ok, res = ctx.ejecutar(lambda: asistente_ia.confirmar_reporte(borrador["texto"]), publico=True, bloquear=boton_guardar)
        if ok:
            borrador["texto"] = None
            boton_guardar.visible = False
            mostrar_tarjeta(f"Reporte registrado. Código de seguimiento: {res['codigo']}. Guárdelo para consultar su estado.",
                            "Reporte guardado")

    chips = [ft.OutlinedButton(etiqueta, on_click=lambda e, k=k: mostrar_tema(k))
             for k, (etiqueta, _) in TEMAS.items()]

    return C.seccion(
        C.titulo("Asistente de HIDRO360", "Le orientamos para reportar y consultar"),
        C.nota("Asistente de demostración local. Puede consultar reportes y preparar borradores; "
               "nunca guarda un reporte sin su confirmación ni envía incidencias a ninguna institución.", "info"),
        ft.Row(chips, wrap=True, spacing=8, run_spacing=8),
        ft.Row([entrada, C.boton("Preguntar", preguntar, ft.Icons.SEND, "primario")], spacing=10),
        cargando,
        tarjeta_respuesta,
        boton_guardar,
        ft.Row([C.boton("Hacer un reporte", lambda e: ctx.ir("reportar"), ft.Icons.EDIT_NOTE, "secundario"),
                C.boton("Consultar mi reporte", lambda e: ctx.ir("consultar"), ft.Icons.SEARCH, "secundario")], spacing=10),
    )
