"""Misiones y flota: terminales por vehículo (registro, token, estado), creación y seguimiento de misiones y eventos
que envía la terminal (llegada / entrega / incidencia). Sustituye a la antigua página «Estado de dispositivos».
El token de una terminal se muestra UNA sola vez, al registrarla o rotarla; después solo existe su hash en el servidor."""

from datetime import datetime
from typing import Any, List

import flet as ft

import config
from interface.flet_app import backend
from interface.flet_app import components as C
from interface.flet_app import theme as T
from models.schemas import EstadoPlan, Parada
from services import workflow

ESTADO_MISION = {"asignada": ("asignada", "info"), "en_curso": ("en curso", "warn"), "completada": ("completada", "ok"), "cancelada": ("cancelada", "bad")}
ESTADO_PARADA = {"pendiente": ("pendiente", "neutro"), "llegada": ("llegó", "warn"), "entregada": ("entregada", "ok"), "incidencia": ("incidencia", "bad")}
RESULTADO_EVENTO = {"aplicado": "ok", "duplicado": "neutro", "rechazado": "bad"}
AUTO_SEGUNDOS = 15


def _v(o, k, d=None):
    return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)


def _val(x):
    return getattr(x, "value", x)


def _hace(f: Any) -> str:
    if not f:
        return "nunca se ha conectado"
    try:
        dt = f if isinstance(f, datetime) else datetime.fromisoformat(str(f))
    except ValueError:
        return str(f)
    seg = max(0, int((datetime.now() - dt).total_seconds()))
    cuando = "hace unos segundos" if seg < 60 else f"hace {seg // 60} min" if seg < 3600 else f"hace {seg // 3600} h" if seg < 86400 else f"hace {seg // 86400} d"
    return f"{dt:%Y-%m-%d %H:%M} ({cuando})"


def _token_de(res: Any) -> str:
    """registrar_dispositivo devuelve (Dispositivo, token); rotar_token puede devolver el token solo o la misma pareja."""
    if isinstance(res, str):
        return res
    if isinstance(res, (tuple, list)):
        for x in reversed(res):
            if isinstance(x, str):
                return x
    return ""


def build(ctx) -> ft.Control:
    cisternas = workflow.listar_cisternas()
    sectores = {s.nombre: s for s in workflow.listar_sectores()}
    aprobados = [p for p in workflow.listar_planes() if p.estado == EstadoPlan.APROBADO]

    cabecera = C.titulo("Misiones y flota", "Un vehículo = una cisterna = una terminal. Cree misiones y siga lo que reporta cada terminal")
    aviso = C.nota("Las terminales (ESP32-S3) no están probadas en placa todavía: lo que ve aquí viene del servidor. La terminal solo recibe su misión y envía "
                   "llegada, entrega o incidencia; no aprueba nada ni lee reportes.", "warn")
    refresco = ft.Text("", size=11.5, color=T.MUTED)
    flota_col, misiones_col, eventos_col = ft.Column([], spacing=10), ft.Column([], spacing=12), ft.Column([], spacing=10)
    f_estado = C.lista_desplegable("Estado de la misión", [("activas", "Activas"), ("todas", "Todas")] + [(k, v[0]) for k, v in ESTADO_MISION.items()], "activas", width=220)

    # -------------------------------------------------------------- token una vez
    def mostrar_token(titulo: str, cisterna_id: str, token: str):
        if not token:
            ctx.toast("La operación se hizo, pero el servidor no devolvió un token para mostrar.", "warn")
            return
        ctx.dialogo(titulo, ft.Column([
            C.nota("Copie este token ahora y guárdelo en un lugar seguro. NO se volverá a mostrar: si se pierde hay que generar uno nuevo.", "warn"),
            ft.Text(f"Terminal de la cisterna {cisterna_id}", size=13, color=T.MUTED),
            ft.Container(ft.Text(token, size=15, selectable=True, font_family="monospace", color=T.NAVY), bgcolor="#eaf4fc", border_radius=10, padding=12)],
            spacing=10, tight=True, width=460),
            [ft.TextButton("Ya lo guardé", on_click=lambda e: ctx.page.pop_dialog())])

    # ---------------------------------------------------------------- acciones de flota
    def registrar(cisterna_id: str, nombre: str = ""):
        ok, res = ctx.ejecutar(lambda: backend.llamar("registrar_dispositivo", cisterna_id, nombre or f"Terminal {cisterna_id}"),
                               f"Terminal de {cisterna_id} registrada.")
        if ok:
            mostrar_token("Terminal registrada: guarde su token", cisterna_id, _token_de(res))
            repintar()

    def rotar(cisterna_id: str):
        def hacer():
            ok, res = ctx.ejecutar(lambda: backend.llamar("rotar_token", cisterna_id), f"Se generó un token nuevo para {cisterna_id}.")
            if ok:
                mostrar_token("Token nuevo: guárdelo", cisterna_id, _token_de(res))
                repintar()
        ctx.confirmar("Rotar el token", f"El token actual de la terminal de {cisterna_id} dejará de funcionar y habrá que cargar el nuevo en el equipo.", hacer, True)

    def desactivar(cisterna_id: str):
        def hacer():
            ok, _ = ctx.ejecutar(lambda: backend.llamar("desactivar_dispositivo", cisterna_id), f"Terminal de {cisterna_id} desactivada.")
            if ok:
                repintar()
        ctx.confirmar("Desactivar la terminal", f"La terminal de {cisterna_id} dejará de poder conectarse hasta que se registre de nuevo.", hacer, True)

    def cancelar(mision, motivo_campo):
        if not (motivo_campo.value or "").strip():
            motivo_campo.error_text = "Escriba el motivo de la cancelación."
            ctx.actualizar()
            return
        motivo_campo.error_text = None

        def hacer():
            ok, _ = ctx.ejecutar(lambda: backend.llamar("cancelar_mision", _v(mision, "id"), ctx.responsable, motivo_campo.value.strip()),
                                 f"Misión #{_v(mision, 'id')} cancelada.")
            if ok:
                repintar()
        ctx.confirmar("Cancelar la misión", f"Se cancelará la misión #{_v(mision, 'id')}. Queda registrado en la auditoría.", hacer, True)

    # ----------------------------------------------------------------- bloques de datos
    def pintar_flota(disp: List[Any], misiones: List[Any]):
        por_cis = {_v(d, "cisterna_id"): d for d in disp}
        activa = {}
        for m in misiones:
            if _val(_v(m, "estado")) in ("asignada", "en_curso"):
                activa.setdefault(_v(m, "cisterna_id"), _v(m, "id"))
        filas = []
        for c in cisternas:
            d = por_cis.get(c.id)
            if d is None:
                acc = ft.Row([ft.TextButton("Registrar terminal", icon=ft.Icons.ADD, on_click=lambda e, i=c.id: registrar(i), tooltip="Crea la identidad y el token de la terminal")])
                filas.append([c.id, acc, C.pill(_val(c.estado), "neutro"), C.pill("sin terminal", "warn"), "—", "—", f"#{activa[c.id]}" if c.id in activa else "—"])
                continue
            on = bool(_v(d, "activo", True))
            acc = ft.Row([ft.IconButton(ft.Icons.AUTORENEW, tooltip="Rotar token", icon_color=T.BLUE, on_click=lambda e, i=c.id: rotar(i)),
                          ft.IconButton(ft.Icons.POWER_SETTINGS_NEW, tooltip="Desactivar terminal", icon_color=T.RED, on_click=lambda e, i=c.id: desactivar(i), disabled=not on)]
                         if on else [ft.TextButton("Registrar de nuevo", icon=ft.Icons.ADD, on_click=lambda e, i=c.id: registrar(i, _v(d, "nombre", "")))], spacing=0)
            filas.append([c.id, acc, C.pill(_val(c.estado), "neutro"), f"{_v(d, 'nombre') or 'terminal'}", C.pill("activa" if on else "desactivada", "ok" if on else "bad"),
                          _hace(_v(d, "ultima_conexion")), f"#{activa[c.id]}" if c.id in activa else "—"])
        flota_col.controls = [C.tabla(["Cisterna", "Acciones", "Estado", "Terminal", "Terminal activa", "Última conexión", "Misión en curso"], filas)] if filas \
            else [C.vacio("No hay cisternas registradas.", ft.Icons.LOCAL_SHIPPING)]

    def tarjeta_mision(m) -> ft.Control:
        paradas = list(_v(m, "paradas", []) or [])
        hechas = sum(_val(_v(p, "estado")) == "entregada" for p in paradas)
        est = _val(_v(m, "estado"))
        etiqueta, tipo = ESTADO_MISION.get(est, (est, "neutro"))
        origen = "plan aprobado #%s" % _v(m, "plan_id") if _v(m, "plan_id") else ("recomendación IA #%s (aprobada por una persona)" % _v(m, "recomendacion_id")
                                                                                if _v(m, "recomendacion_id") else "creada a mano")
        filas = [[_v(p, "orden"), _v(p, "sector"), _v(p, "litros"), C.pill(*ESTADO_PARADA.get(_val(_v(p, "estado")), (_val(_v(p, "estado")), "neutro"))),
                  "—" if _v(p, "litros_entregados") is None else _v(p, "litros_entregados"), _v(p, "nota") or "—"] for p in paradas]
        cuerpo = [ft.Row([ft.Text(f"Misión #{_v(m, 'id')} · {_v(m, 'cisterna_id')}", size=16, weight=ft.FontWeight.W_800, color=T.NAVY), C.pill(etiqueta, tipo)], spacing=10, wrap=True),
                  ft.Text(f"Creada {str(_v(m, 'creada'))[:16].replace('T', ' ')} por {_v(m, 'creada_por')} · origen: {origen}" + (f" · {_v(m, 'motivo')}" if _v(m, "motivo") else ""),
                          size=12, color=T.MUTED),
                  C.barra(hechas, len(paradas) or 1, T.GREEN, f"Avance: {hechas} de {len(paradas)} paradas entregadas"),
                  C.tabla(["Orden", "Sector", "Litros", "Estado", "Entregados", "Nota"], filas)]
        if est in ("asignada", "en_curso"):
            motivo = C.campo("Motivo para cancelar", "", width=320)
            cuerpo.append(ft.Row([motivo, C.boton("Cancelar misión", lambda e, mm=m, mc=motivo: cancelar(mm, mc), ft.Icons.CANCEL, "peligro")], spacing=10, wrap=True))
        panel = C.panel(ft.Column(cuerpo, spacing=8))
        if ctx.seleccion.get("mision_id") == _v(m, "id"):
            panel.border = ft.Border.all(2, T.BLUE)
        return panel

    def pintar_misiones(misiones: List[Any]):
        filtro = f_estado.value or "activas"
        vis = [m for m in misiones if filtro == "todas" or (filtro == "activas" and _val(_v(m, "estado")) in ("asignada", "en_curso")) or _val(_v(m, "estado")) == filtro]
        vis.sort(key=lambda m: -int(_v(m, "id", 0) or 0))
        misiones_col.controls = [tarjeta_mision(m) for m in vis] or [C.vacio("No hay misiones con este filtro. Cree una abajo o apruebe una recomendación.", ft.Icons.ROUTE)]

    def pintar_eventos(eventos: List[Any]):
        filas = [[str(_v(x, "fecha_servidor"))[:19].replace("T", " "), _v(x, "cisterna_id"), f"#{_v(x, 'mision_id')}", _v(x, "parada_orden"),
                  {"llegada": "llegó", "entrega": "entregó", "incidencia": "incidencia"}.get(_v(x, "tipo"), _v(x, "tipo")),
                  "—" if _v(x, "litros_entregados") is None else _v(x, "litros_entregados"), _v(x, "nota") or "—",
                  C.pill(_v(x, "resultado", ""), RESULTADO_EVENTO.get(_v(x, "resultado"), "neutro"))] for x in sorted(eventos, key=lambda x: str(_v(x, "fecha_servidor")), reverse=True)[:60]]
        eventos_col.controls = [C.tabla(["Recibido", "Cisterna", "Misión", "Parada", "Evento", "Litros", "Nota", "Resultado"], filas)] if filas \
            else [C.nota("Sin eventos: ninguna terminal ha enviado todavía llegada, entrega ni incidencia.", "info")]

    def repintar(e=None, desde_auto: bool = False):
        with ctx.con_actor():            # también se llama desde manejadores y desde la tarea periódica, fuera de la construcción
            _repintar(e, desde_auto)

    def _repintar(e, desde_auto):
        errores = []
        disp, er = backend.leer("listar_dispositivos", [])
        errores += [er] if er else []
        misiones, er = backend.leer("listar_misiones", [])
        errores += [er] if er else []
        eventos, er = backend.leer("listar_eventos", [])
        errores += [er] if er else []
        pintar_flota(disp or [], misiones or [])
        pintar_misiones(misiones or [])
        pintar_eventos(eventos or [])
        refresco.value = f"Actualizado {datetime.now():%H:%M:%S}" + (f" · se actualiza solo cada {AUTO_SEGUNDOS} s" if ctx.page else "")
        if errores:
            flota_col.controls.insert(0, C.nota("Parte de los datos no está disponible todavía: " + " · ".join(dict.fromkeys(errores)), "warn"))
        if e is not None or desde_auto:
            ctx.actualizar()

    f_estado.on_select = lambda e: repintar(e)

    # ------------------------------------------------------------------ crear misión
    sel_cis = C.lista_desplegable("Cisterna", [c.id for c in cisternas], cisternas[0].id if cisternas else None, width=170)
    motivo_m = C.campo("Motivo o comentario (opcional)", "", expand=True)
    sel_plan = C.lista_desplegable("Plan aprobado", [(p.id, f"Plan #{p.id} · {len(p.asignaciones)} asignaciones") for p in aprobados],
                                   str(aprobados[-1].id) if aprobados else None, width=300)
    nuevas: List[Parada] = []
    lista_paradas = ft.Row([], spacing=6, wrap=True, run_spacing=6)
    sel_sector = C.lista_desplegable("Sector de la parada", sorted(sectores), None, width=240)
    litros = C.campo("Litros", "", width=120, keyboard_type=ft.KeyboardType.NUMBER)

    def pintar_nuevas():
        lista_paradas.controls = [ft.Chip(label=ft.Text(f"{p.orden}. {p.sector} · {p.litros} L"), on_delete=lambda e, k=p.orden: quitar(k)) for p in nuevas] \
            or [ft.Text("Aún no agregó paradas.", size=12, color=T.MUTED)]

    def quitar(orden):
        nuevas[:] = [p for p in nuevas if p.orden != orden]
        for i, p in enumerate(nuevas, 1):
            p.orden = i
        pintar_nuevas()
        ctx.actualizar()

    def agregar(e=None):
        try:
            n = int(float((litros.value or "").replace(",", ".")))
        except ValueError:
            n = 0
        if not sel_sector.value or n <= 0:
            ctx.toast("Elija el sector y escriba los litros (un número mayor que 0).", "bad")
            return
        s = sectores.get(sel_sector.value)
        nuevas.append(Parada(orden=len(nuevas) + 1, sector=sel_sector.value, litros=n, lat=getattr(s, "lat", None), lng=getattr(s, "lng", None)))
        litros.value = ""
        pintar_nuevas()
        ctx.actualizar()

    def crear_manual(e=None):
        if not sel_cis.value or not nuevas:
            ctx.toast("Elija la cisterna y agregue al menos una parada.", "bad")
            return
        ok, m = ctx.ejecutar(lambda: backend.llamar("crear_mision", sel_cis.value, list(nuevas), ctx.responsable, motivo=(motivo_m.value or "").strip()),
                             "Misión creada.")
        if ok:
            nuevas.clear()
            ctx.seleccion["mision_id"] = _v(m, "id")
            ctx.refrescar()

    def crear_desde_plan(e=None):
        if not sel_plan.value or not sel_cis.value:
            ctx.toast("Elija un plan aprobado y la cisterna.", "bad")
            return
        ok, m = ctx.ejecutar(lambda: backend.llamar("crear_mision_desde_plan", int(sel_plan.value), sel_cis.value, ctx.responsable),
                             "Misión creada desde el plan aprobado.")
        if ok:
            ctx.seleccion["mision_id"] = _v(m, "id")
            ctx.refrescar()

    pintar_nuevas()
    desde_plan = ft.Column([ft.Text("Desde un plan aprobado", size=13, weight=ft.FontWeight.W_700, color=T.NAVY),
                            ft.Row([sel_plan, C.boton("Crear misión desde el plan", crear_desde_plan, ft.Icons.PLAYLIST_ADD_CHECK)], spacing=12, wrap=True)], spacing=8) \
        if aprobados else C.nota("Todavía no hay un plan aprobado. Aprobar un plan en «Plan» habilita crear misiones desde él.", "info")
    manual = ft.Column([ft.Text("Misión manual", size=13, weight=ft.FontWeight.W_700, color=T.NAVY),
                        ft.Row([sel_sector, litros, C.boton("Agregar parada", agregar, ft.Icons.ADD, "secundario")], spacing=12, wrap=True),
                        lista_paradas, ft.Row([motivo_m]), C.boton("Crear misión manual", crear_manual, ft.Icons.ROUTE)], spacing=8)
    crear = C.panel(ft.Column([ft.Row([sel_cis]), desde_plan, ft.Divider(height=6, color=T.LINE), manual], spacing=10), "Crear una misión")

    # ------------------------------------------------------------------ contrato API
    tokens = ft.Row([ft.Text("Token de operador:", size=13), C.pill("configurado" if config.API_TOKEN() else "sin configurar (solo localhost)", "ok" if config.API_TOKEN() else "warn"),
                     ft.Text("Token de terminales general:", size=13), C.pill("configurado" if config.DEVICE_TOKEN() else "sin configurar", "ok" if config.DEVICE_TOKEN() else "warn")],
                    spacing=10, wrap=True)
    tecnico = ft.ExpansionTile(title=ft.Text("Contrato con la API para terminales", size=13), controls=[ft.Container(ft.Column([tokens, C.tabla(
        ["Método", "Ruta", "Uso"], [["GET", "/api/v1/dispositivo/mision", "la terminal pide su misión activa (token por vehículo)"],
                                    ["POST", "/api/v1/dispositivo/eventos", "llegada, entrega o incidencia (idempotente por uid)"],
                                    ["POST", "/api/v1/dispositivo/latido", "señal de vida y versión de firmware"],
                                    ["GET", "/api/v1/salud", "comprobación de salud (pública)"]])], spacing=8), padding=10)])

    actualizar = C.boton("Actualizar", lambda e: repintar(e), ft.Icons.REFRESH, "secundario", tooltip="Vuelve a leer flota, misiones y eventos")
    repintar()
    ctx.auto_refrescar("misiones", AUTO_SEGUNDOS, lambda: repintar(desde_auto=True))
    return C.seccion(
        cabecera, aviso, ft.Row([actualizar, refresco], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        C.panel(flota_col, "Flota y terminales"), crear,
        C.panel(ft.Column([ft.Row([f_estado]), misiones_col], spacing=10), "Misiones y avance por parada"),
        C.panel(eventos_col, "Eventos recibidos de las terminales"), tecnico)
