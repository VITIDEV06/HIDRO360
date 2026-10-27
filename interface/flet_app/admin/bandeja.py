"""Bandeja de reportes: recibir un reporte manual (con idempotencia), filtrar por estado/sector/texto y ver el detalle
(fotos, coordenadas, mini-mapa) con las acciones de confirmar, rechazar con motivo y pedir un análisis asistido."""

import uuid

import flet as ft

from interface.flet_app import backend
from interface.flet_app import components as C
from interface.flet_app import mapa as M
from interface.flet_app import theme as T
from models.schemas import EstadoReporte
from services import workflow

TODOS = "(todos)"


def _fecha(r) -> str:
    return str(r.fecha_creacion)[:16].replace("T", " ")


def build(ctx) -> ft.Control:
    reps = workflow.listar_reportes()
    texto = ft.TextField(label="Reporte ciudadano (texto tal como llegó)", multiline=True, min_lines=3, max_lines=6,
                         hint_text="Ej.: En Vista Alegre no tenemos agua desde ayer", border_radius=12,
                         border_color="#cfdcec", focused_border_color=T.BLUE, expand=True)
    estado_envio = ft.Row([], spacing=8)
    boton_envio = None

    def enviar(e):
        valor = (texto.value or "").strip()
        if not valor:
            texto.error_text = "El texto del reporte no puede estar vacío."
            ctx.actualizar()
            return
        texto.error_text = None
        boton_envio.disabled = True
        estado_envio.controls = [C.cargando("Guardando e interpretando…")]
        ctx.actualizar()
        ok, rep = ctx.ejecutar(lambda: workflow.crear_reporte(valor, ctx.responsable, clave_idempotencia=ctx.clave_envio))
        boton_envio.disabled = False
        estado_envio.controls = []
        if ok:
            ctx.clave_envio = uuid.uuid4().hex
            ctx.toast(f"Reporte #{rep.id} guardado ({rep.interprete}). Revíselo y confírmelo.")
            ctx.ir("revision", reporte_id=rep.id)
        else:
            ctx.actualizar()

    boton_envio = C.boton("Recibir reporte", enviar, ft.Icons.ADD)
    nuevo = C.panel(ft.Column([ft.Row([texto]), ft.Row([boton_envio, estado_envio], spacing=14),
                               ft.Text("Sirve para registrar a mano reportes recibidos por teléfono u otros canales. Los reportes del portal llegan solos.",
                                       size=11.5, color=T.MUTED)], spacing=10),
                    "Registrar un reporte a mano")

    f_estado = C.lista_desplegable("Estado", [TODOS] + [e.value for e in EstadoReporte], TODOS, width=190)
    f_sector = C.lista_desplegable("Sector", [TODOS] + sorted({r.sector for r in reps if r.sector}), TODOS, width=210)
    f_texto = C.campo("Buscar texto o código", "", width=250, prefix_icon=ft.Icons.SEARCH)
    f_inc = ft.Switch(label="Solo con faltantes/inciertos", value=False)
    lista = ft.Column([], spacing=0)
    detalle = ft.Column([], spacing=12)
    pagina = [None]

    # ------------------------------------------------------------------ detalle
    def ver_foto_grande(rid, fid):
        ok, res = ctx.ejecutar(lambda: backend.llamar("obtener_foto", rid, fid))
        if ok and res:
            ctx.dialogo("Foto del reporte", ft.Image(src=res[0], width=560, fit=ft.BoxFit.CONTAIN))

    def fotos_de(r) -> ft.Control:
        if not r.foto_ids:
            return C.nota("Este reporte no trae fotos.", "info")
        miniaturas = []
        for fid in r.foto_ids:
            datos, error = backend.leer("obtener_foto", None, r.id, fid)
            if datos:
                miniaturas.append(ft.Container(ft.Image(src=datos[0], width=130, height=100, fit=ft.BoxFit.COVER, border_radius=10),
                                               ink=True, tooltip="Ver más grande", on_click=lambda e, i=r.id, f=fid: ver_foto_grande(i, f)))
            else:
                miniaturas.append(C.nota(f"No se pudo leer una foto: {error}", "warn"))
        return ft.Row(miniaturas, spacing=10, wrap=True, run_spacing=10)

    def rechazar(r):
        def hacer(motivo: str) -> bool:
            ok, _ = ctx.ejecutar(lambda: workflow.rechazar_reporte(r.id, ctx.responsable, motivo), f"Reporte #{r.id} rechazado.")
            if ok:
                ctx.refrescar()
            return ok

        ctx.pedir_motivo(f"Rechazar el reporte #{r.id}", "Motivo del rechazo (obligatorio)", hacer, "Rechazar")

    def analizar(r):
        ok, rec = ctx.ejecutar(lambda: backend.llamar("analizar_reporte", r.id, ctx.responsable),
                               f"Análisis del reporte #{r.id} listo. Es una propuesta: usted decide.")
        if ok:
            ctx.ir("recomendaciones", reporte_id=r.id, recomendacion_id=getattr(rec, "id", None))

    def mostrar(rid: int, actualizar: bool = True):
        with ctx.con_actor():            # los manejadores de eventos corren fuera de la construcción de la página: sin esto no hay actor
            _mostrar(rid, actualizar)

    def _mostrar(rid: int, actualizar: bool):
        r = next((x for x in reps if x.id == rid), None)
        if r is None:
            detalle.controls = []
            return
        ctx.seleccion["reporte_id"] = rid
        ubicacion = (f"{r.lat:.5f}, {r.lng:.5f}" + (f" (fuente: {r.ubicacion_fuente})" if r.ubicacion_fuente else "")) \
            if M.coordenadas_validas(r.lat, r.lng) else "Sin coordenadas (solo sector o referencia)."
        datos = [("Código", r.codigo_seguimiento or "—"), ("Recibido", _fecha(r)), ("Canal", getattr(r, "canal", "—")),
                 ("Sector", r.sector or "sin sector"), ("Calle / referencia", r.calle or r.referencia_ubicacion or "—"),
                 ("Horas sin agua", "—" if r.horas_sin_agua is None else r.horas_sin_agua),
                 ("Personas afectadas", "—" if r.personas_afectadas is None else r.personas_afectadas),
                 ("Severidad", r.severidad.value if r.severidad else "—"), ("Ubicación", ubicacion)]
        ficha = ft.Column([ft.Row([ft.Text(k, size=12, color=T.MUTED, width=130), C.texto(v, 13)], spacing=8) for k, v in datos], spacing=3)
        puede = r.estado in (EstadoReporte.POR_REVISAR, EstadoReporte.CONFIRMADO)
        acciones = ft.Row([
            C.boton("Revisar y confirmar" if r.estado == EstadoReporte.POR_REVISAR else "Corregir datos", lambda e: ctx.ir("revision", reporte_id=r.id),
                    ft.Icons.FACT_CHECK) if puede else ft.Container(),
            C.boton("Rechazar reporte", lambda e: rechazar(r), ft.Icons.BLOCK, "secundario") if puede else ft.Container(),
            C.boton("Ver duplicados", lambda e: ctx.ir("duplicados", reporte_id=r.id), ft.Icons.CONTENT_COPY, "secundario"),
            C.boton("Analizar reporte", lambda e: analizar(r), ft.Icons.AUTO_AWESOME, "secundario",
                    tooltip="Pide una recomendación asistida (propuesta; la decisión es suya)")], spacing=10, wrap=True)
        if r.motivo_rechazo:
            ficha.controls.append(C.nota(f"Motivo del rechazo: {r.motivo_rechazo}", "bad"))
        detalle.controls = [C.panel(ft.Column([
            ft.Row([ft.Text(f"Reporte #{r.id}", size=18, weight=ft.FontWeight.W_800, color=T.NAVY), C.pill_estado(r.estado.value)], spacing=10),
            ft.Text("Texto original (inalterable)", size=12, color=T.MUTED, weight=ft.FontWeight.W_700),
            ft.Container(C.texto(r.mensaje_original, 14), bgcolor="#eaf4fc", border_radius=12, padding=14), ficha, acciones], spacing=10), "Detalle del reporte"),
            ft.ResponsiveRow([
                ft.Container(C.panel(M.mini(r.lat, r.lng), "Ubicación en el mapa"), col={"xs": 12, "lg": 6}),
                ft.Container(C.panel(fotos_de(r), f"Fotos ({len(r.foto_ids)})"), col={"xs": 12, "lg": 6})], run_spacing=14)]
        if actualizar:
            ctx.actualizar()
            if ctx.page is not None and pagina[0] is not None:       # el detalle está arriba: llevar la vista hasta él
                ctx.page.run_task(pagina[0].scroll_to, offset=0, duration=250)

    # ------------------------------------------------------------------- lista
    def pintar(e=None):
        q = (f_texto.value or "").strip().lower()
        filas = []
        for r in reversed(reps):
            if f_estado.value not in (None, TODOS) and r.estado.value != f_estado.value:
                continue
            if f_sector.value not in (None, TODOS) and r.sector != f_sector.value:
                continue
            if f_inc.value and not (r.datos_faltantes or r.datos_inciertos):
                continue
            if q and q not in r.mensaje_original.lower() and q not in (r.codigo_seguimiento or "").lower() and q not in (r.sector or "").lower():
                continue
            icono = [ft.Icon(ft.Icons.PLACE, size=15, color=T.GREEN, tooltip="Con ubicación en el mapa")] if M.coordenadas_validas(r.lat, r.lng) else []
            if r.foto_ids:
                icono.append(ft.Icon(ft.Icons.PHOTO_CAMERA, size=15, color=T.BLUE, tooltip=f"{len(r.foto_ids)} foto(s)"))
            filas.append([ft.TextButton(f"#{r.id}", tooltip="Ver detalle", on_click=lambda e, rid=r.id: mostrar(rid)), C.pill_estado(r.estado.value), r.sector or "—", ft.Row(icono or [ft.Text("—", size=12)], spacing=4),
                          r.severidad.value if r.severidad else "—", r.interprete,
                          ft.Text((r.mensaje_original[:70] + "…") if len(r.mensaje_original) > 70 else r.mensaje_original, size=12, width=330),
                          ft.IconButton(icon=ft.Icons.VISIBILITY, tooltip="Ver detalle", icon_color=T.BLUE,
                                        on_click=lambda e, rid=r.id: mostrar(rid))])
        lista.controls = [C.tabla(["ID", "Estado", "Sector", "Mapa/fotos", "Severidad", "Intérprete", "Mensaje", ""], filas),
                          ft.Text(f"{len(filas)} de {len(reps)} reportes · el mensaje original nunca se modifica. "
                                  f"{'Datos ficticios marcados en cada reporte de demo.' if any(r.ficticio for r in reps) else ''}",
                                  size=11.5, color=T.MUTED)]
        if e is not None:
            ctx.actualizar()

    for ctl in (f_estado, f_sector):
        ctl.on_select = pintar
    f_texto.on_change = pintar
    f_inc.on_change = pintar
    pintar()
    sel = ctx.seleccion.get("reporte_id")
    if sel is not None:
        mostrar(sel, actualizar=False)
    pagina[0] = C.seccion(C.titulo("Bandeja de reportes", "Filtre, abra el detalle y decida qué hacer con cada reporte"), detalle,
                          C.panel(ft.Column([ft.Row([f_estado, f_sector, f_texto, f_inc], spacing=12, wrap=True), lista], spacing=12), "Reportes"),
                          nuevo)
    return pagina[0]
