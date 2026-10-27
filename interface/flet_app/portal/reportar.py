"""Portal público: formulario sencillo de reporte con ubicación en el mapa (opcional) y hasta 3 fotos.
Muestra el código de seguimiento al registrarlo. La validación de verdad (firma de la imagen, tamaño, límites) es del
backend; aquí solo hay una comprobación amistosa previa y los errores del servidor se muestran en español claro."""

import uuid
from typing import List, Optional, Tuple

import flet as ft

import config
from interface.flet_app import backend
from interface.flet_app import components as C
from interface.flet_app import mapa as M
from interface.flet_app import theme as T

MAX_FOTOS = getattr(config, "MAX_FOTOS_POR_REPORTE", 3)
MAX_BYTES = getattr(config, "MAX_BYTES_FOTO", 3 * 1024 * 1024)
EXTENSIONES = ("jpg", "jpeg", "png", "webp")


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


class Fotos:
    """Fotos elegidas por la persona (en memoria hasta enviar). `agregar` aplica la comprobación amistosa del cliente."""

    def __init__(self):
        self.items: List[Tuple[bytes, str]] = []

    def agregar(self, archivos) -> List[str]:
        """archivos: objetos con .name, .size y .bytes (FilePickerFile con with_data=True). Devuelve los avisos para mostrar."""
        avisos = []
        for f in archivos or []:
            nombre = getattr(f, "name", "") or "foto"
            datos = getattr(f, "bytes", None)
            ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
            if len(self.items) >= MAX_FOTOS:
                avisos.append(f"Solo puede adjuntar hasta {MAX_FOTOS} fotos; «{nombre}» no se agregó.")
            elif ext not in EXTENSIONES:
                avisos.append(f"«{nombre}» no es una foto admitida. Use JPG, PNG o WEBP.")
            elif not datos:
                avisos.append(f"No pudimos leer «{nombre}». Intente con otra foto.")
            elif len(datos) > MAX_BYTES:
                avisos.append(f"«{nombre}» pesa {_mb(len(datos))}; el máximo es {_mb(MAX_BYTES)} por foto.")
            else:
                self.items.append((bytes(datos), nombre))
        return avisos

    def quitar(self, i: int) -> None:
        if 0 <= i < len(self.items):
            del self.items[i]


def build(ctx) -> ft.Control:
    if ctx.envio_hecho:               # el formulario se reabre tras un envío exitoso: nueva clave de idempotencia
        ctx.clave_envio = uuid.uuid4().hex
        ctx.envio_hecho = False
    texto = ft.TextField(label="Cuéntenos qué está pasando", multiline=True, min_lines=4, max_lines=8, max_length=1500, border_radius=12,
                         hint_text="Ej.: En mi calle no tenemos agua desde ayer y somos varias familias", border_color="#cfdcec",
                         focused_border_color=T.BLUE, expand=True, tooltip="Describa el problema con sus palabras")
    comunidad = C.campo("Comunidad o sector (opcional)", expand=True, hint_text="Ej.: Barrio Colón", tooltip="Ayuda a ubicar su reporte")
    estado = ft.Row([], spacing=8)
    zona = ft.Container()
    envio = {"boton": None}

    # ------------------------------------------------------------------ ubicación
    estado_mapa = {"sin_mapa": False}
    lugar = ft.Text("Toque el mapa para marcar el lugar del problema.", size=13, color=T.MUTED)
    cuerpo_mapa = ft.Column([], spacing=8)
    boton_alt = {"x": None}

    def al_elegir(lat, lng):
        lugar.value = f"Lugar marcado ({lat:.5f}, {lng:.5f}). Puede tocar otro punto para corregirlo."
        lugar.color = T.GREEN
        ctx.actualizar()

    selector = M.Selector(al_elegir)

    def pintar_mapa():
        if estado_mapa["sin_mapa"]:
            selector.limpiar()
            lugar.value = "Sin ubicación en el mapa: usaremos solo la comunidad o la referencia que escribió."
            lugar.color = T.MUTED
            cuerpo_mapa.controls = [lugar]
            boton_alt["x"].content = "Prefiero marcarlo en el mapa"
        else:
            if selector.punto is None:
                lugar.value = "Toque el mapa para marcar el lugar del problema."
                lugar.color = T.MUTED
            cuerpo_mapa.controls = [lugar, selector.control,
                                    ft.Text("Si el mapa se ve gris o vacío, probablemente no hay internet: puede enviar su reporte igual, solo con la descripción.",
                                            size=11.5, color=T.MUTED)]
            boton_alt["x"].content = "No sé ubicarlo en el mapa"

    def alternar_mapa(e=None):
        estado_mapa["sin_mapa"] = not estado_mapa["sin_mapa"]
        pintar_mapa()
        ctx.actualizar()

    boton_alt["x"] = C.boton("No sé ubicarlo en el mapa", alternar_mapa, ft.Icons.HELP_OUTLINE, "secundario",
                             tooltip="Enviar el reporte sin marcar un punto en el mapa")
    pintar_mapa()

    # ---------------------------------------------------------------------- fotos
    fotos = Fotos()
    lista_fotos = ft.Row([], spacing=10, wrap=True, run_spacing=10)
    avisos_fotos = ft.Column([], spacing=6)

    def pintar_fotos():
        lista_fotos.controls = []
        for i, (datos, nombre) in enumerate(fotos.items):
            lista_fotos.controls.append(ft.Container(ft.Column([
                ft.Image(src=datos, width=110, height=84, fit=ft.BoxFit.COVER, border_radius=10),
                ft.Text(nombre if len(nombre) < 18 else nombre[:15] + "…", size=11, color=T.MUTED),
                ft.TextButton("Quitar", on_click=lambda e, k=i: (fotos.quitar(k), pintar_fotos(), ctx.actualizar()))], spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER), width=124))

    def procesar(archivos):
        avisos = fotos.agregar(archivos)
        avisos_fotos.controls = [C.nota(a, "warn") for a in avisos]
        pintar_fotos()
        ctx.actualizar()

    async def elegir_fotos(e):
        selector_archivos = ctx.servicio("archivos", ft.FilePicker)
        if selector_archivos is None:
            return
        quedan = MAX_FOTOS - len(fotos.items)
        if quedan <= 0:
            procesar([])
            avisos_fotos.controls = [C.nota(f"Ya agregó {MAX_FOTOS} fotos. Quite una para cambiarla.", "warn")]
            ctx.actualizar()
            return
        try:
            elegidos = await selector_archivos.pick_files(dialog_title="Elija hasta 3 fotos", file_type=ft.FilePickerFileType.CUSTOM,
                                                          allowed_extensions=list(EXTENSIONES), allow_multiple=True, with_data=True)
        except Exception:
            avisos_fotos.controls = [C.nota("No pudimos abrir el selector de fotos en este equipo. Puede enviar el reporte sin fotos.", "warn")]
            ctx.actualizar()
            return
        procesar(elegidos)

    boton_fotos = C.boton("Agregar fotos", elegir_fotos, ft.Icons.ADD_A_PHOTO, "secundario", tooltip="Hasta 3 fotos (JPG, PNG o WEBP)")
    boton_fotos.data = fotos            # las pruebas llaman a fotos.agregar / procesan igual que el selector
    boton_fotos.key = "agregar_fotos"

    # --------------------------------------------------------------------- envío
    def confirmacion(codigo, con_ubicacion: bool, n_fotos: int) -> ft.Control:
        filas = [ft.Icon(ft.Icons.CHECK_CIRCLE, color=T.GREEN, size=44),
                 ft.Text("Su reporte quedó registrado", size=22, weight=ft.FontWeight.W_800, color=T.NAVY),
                 ft.Text("Gracias por avisar. Será revisado por el equipo.", size=14, color=T.MUTED)]
        extras = []
        if con_ubicacion:
            extras.append("ubicación en el mapa")
        if n_fotos:
            extras.append(f"{n_fotos} foto{'s' if n_fotos != 1 else ''}")
        if extras:
            filas.append(ft.Text("Se guardó también: " + " y ".join(extras) + ".", size=13, color=T.INK))
        if codigo:
            async def copiar(e):
                portapapeles = ctx.servicio("portapapeles", ft.Clipboard)
                if portapapeles is not None:
                    try:
                        await portapapeles.set(codigo)
                        ctx.toast("Código copiado. Guárdelo para consultar su reporte.")
                        return
                    except Exception:
                        pass
                ctx.toast("No se pudo copiar automáticamente. Selecciónelo con el cursor y cópielo.", "warn")
            filas += [ft.Text("Su código de seguimiento", size=12, color=T.MUTED),
                      ft.Row([ft.Container(ft.Text(codigo, size=28, weight=ft.FontWeight.W_800, color=T.NAVY, selectable=True),
                                           bgcolor="#eaf4fc", border_radius=12, padding=ft.Padding.symmetric(horizontal=18, vertical=10)),
                              C.boton("Copiar código", copiar, ft.Icons.CONTENT_COPY, "secundario", tooltip="Copiar el código al portapapeles")],
                             spacing=12, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                      ft.Text("Guárdelo (anótelo o tome captura): lo necesitará para consultar el estado de su reporte.", size=13, color=T.INK)]
        filas.append(ft.Row([C.boton("Consultar mi reporte", lambda e: ctx.ir("consultar", codigo=codigo or ""), ft.Icons.SEARCH) if codigo else ft.Container(),
                             C.boton("Enviar otro reporte", lambda e: ctx.ir("reportar"), ft.Icons.ADD, "secundario")], spacing=12, wrap=True))
        return C.panel(ft.Column(filas, spacing=10, horizontal_alignment=ft.CrossAxisAlignment.START))

    def enviar(e):
        valor = (texto.value or "").strip()
        if not valor:
            texto.error_text = "Escriba qué está pasando para poder enviar el reporte."
            ctx.actualizar()
            return
        texto.error_text = None
        sector = (comunidad.value or "").strip()
        completo = f"{valor}\nComunidad o sector: {sector}" if sector else valor
        punto: Optional[Tuple[float, float]] = None if estado_mapa["sin_mapa"] else selector.punto
        adjuntas = list(fotos.items)
        envio["boton"].disabled = True
        estado.controls = [C.cargando("Enviando su reporte…")]
        ctx.actualizar()
        ok, rep = ctx.ejecutar(lambda: backend.llamar(
            "crear_reporte", completo, ctx.responsable, canal="portal", clave_idempotencia=ctx.clave_envio,
            lat=punto[0] if punto else None, lng=punto[1] if punto else None, fotos=[{"nombre": n, "bytes": b} for b, n in adjuntas] or None), publico=True)
        envio["boton"].disabled = False
        estado.controls = []
        if ok:
            ctx.envio_hecho = True
            zona.content = confirmacion(getattr(rep, "codigo_seguimiento", None), bool(punto), len(adjuntas))
        else:
            detalle = ctx.avisos[-1][1] if ctx.avisos and ctx.avisos[-1][0] == "bad" else ""
            estado.controls = [C.nota("No pudimos registrar su reporte. " + (detalle or "Revise el texto e intente de nuevo."), "bad")]
        ctx.actualizar()

    envio["boton"] = C.boton("Enviar reporte", enviar, ft.Icons.SEND, tooltip="Enviar el reporte")
    formulario = C.panel(ft.Column([
        ft.Row([texto]), ft.Row([comunidad]),
        ft.Text("¿Dónde ocurre?", size=14, weight=ft.FontWeight.W_700, color=T.NAVY), cuerpo_mapa, ft.Row([boton_alt["x"]]),
        ft.Text("Fotos (opcional)", size=14, weight=ft.FontWeight.W_700, color=T.NAVY),
        ft.Text(f"Hasta {MAX_FOTOS} fotos JPG, PNG o WEBP de hasta {_mb(MAX_BYTES)} cada una. No fotografíe a personas.", size=12, color=T.MUTED),
        ft.Row([boton_fotos]), avisos_fotos, lista_fotos,
        ft.Row([envio["boton"], estado], spacing=14, wrap=True),
        ft.Text("No escriba contraseñas ni datos bancarios. Si es una emergencia, llame al 911.", size=12, color=T.MUTED)], spacing=12),
        "Reportar falta de agua")
    zona.content = formulario
    zona.data = {"fotos": fotos, "procesar": procesar, "selector": selector, "alternar_mapa": alternar_mapa}
    return C.seccion(C.titulo("Reportar falta de agua", "Es rápido y no necesita crear una cuenta"), zona)
