"""
Pruebas de la interfaz Flet SIN abrir ventana: se construyen las páginas con un Ctx sin Page y se invocan los
manejadores de eventos (on_click, on_select) igual que haría Flet. Cubren el portal público (sin sesión) y las
operaciones de la consola de administración (con sesión de administrador). NO sustituyen una revisión visual.
Roles, login y sesión: tests/test_flet_roles.py.
"""
import re
from typing import Callable, Iterator, Optional

import flet as ft
import pytest

from interface.flet_app import main as app
from interface.flet_app.contexto import Ctx
from models.schemas import AccionDecision, EstadoPlan, EstadoReporte
from services import auth, workflow

CLAVE = "Clave-Segura-1"
ATRIBUTOS = ("content", "controls", "rows", "cells", "title", "actions", "leading", "trailing")
# El portal ciudadano no debe mostrar nada técnico (ni siquiera como etiqueta o ayuda).
PROHIBIDAS = ("pytest", "pass", "fail", "runner", "gemini", "traza", "auditor", "hash")


def recorrer(c) -> Iterator[ft.Control]:
    if isinstance(c, ft.Control):
        yield c
        for a in ATRIBUTOS:
            v = getattr(c, a, None)
            if isinstance(v, ft.Control):
                yield from recorrer(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    yield from recorrer(x)


def buscar(raiz, tipo, pred: Callable = lambda c: True) -> Optional[ft.Control]:
    return next((c for c in recorrer(raiz) if isinstance(c, tipo) and pred(c)), None)


def boton(raiz, texto: str):
    b = buscar(raiz, (ft.Button, ft.OutlinedButton, ft.FilledButton, ft.TextButton), lambda c: isinstance(c.content, str) and texto in c.content)
    assert b is not None, f"no hay botón «{texto}»"
    return b


def campo(raiz, etiqueta: str):
    c = buscar(raiz, (ft.TextField, ft.Dropdown), lambda c: c.label and etiqueta in str(c.label))
    assert c is not None, f"no hay campo «{etiqueta}»"
    return c


def textos(raiz) -> str:
    return " | ".join(str(c.value) for c in recorrer(raiz) if isinstance(c, ft.Text))


def todo_el_texto(raiz) -> str:
    """Todo lo que una persona podría leer u oír: textos, botones, etiquetas, ayudas, tooltips y errores."""
    partes = []
    for c in recorrer(raiz):
        for atr in ("value", "label", "hint_text", "tooltip", "error_text", "helper_text", "semantics_label", "text"):
            v = getattr(c, atr, None)
            if isinstance(v, str):
                partes.append(v)
        if isinstance(getattr(c, "content", None), str):
            partes.append(c.content)
    return " | ".join(partes)


def construir_ctx(admin: bool = False) -> Ctx:
    c = Ctx(None)
    c.destinos = []
    c._mostrar = lambda pagina: (c.destinos.append(pagina), setattr(c, "pagina_actual", app.resolver(c, pagina)))
    if admin:
        auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
        c.abrir_sesion("admin1", CLAVE)
    return c


@pytest.fixture
def portal():
    return construir_ctx()


@pytest.fixture
def ctx():
    """Contexto con sesión de administrador (lo normal en la consola)."""
    return construir_ctx(admin=True)


def pagina(c, clave):
    return app.construir_pagina(c, clave)


def confirmar_todo():
    for r in workflow.listar_reportes():
        if r.sector:
            workflow.corregir_y_confirmar(r.id, "t", r.sector, r.horas_sin_agua, r.personas_afectadas, r.severidad.value if r.severidad else None, r.calle)


# ================================================================ PORTAL PÚBLICO
def test_portal_paginas_construyen_sin_sesion_y_sin_textos_tecnicos(portal):
    confirmar_todo()
    workflow.generar_plan("t")
    for clave, _, _, _ in app.PORTAL:
        p = pagina(portal, clave)
        t = todo_el_texto(p)
        assert "No se pudo mostrar" not in t, clave
        bajo = t.lower()
        for palabra in PROHIBIDAS:
            assert not re.search(re.escape(palabra), bajo), f"«{palabra}» aparece en el portal ({clave}): {t[:200]}"
    assert not portal.es_admin


def test_portal_inicio_tiene_las_acciones_y_pasos(portal):
    p = pagina(portal, "inicio")
    t = textos(p)
    assert "HIDRO360" in t and "¿Cómo funciona?" in t and "Avisos para su comunidad" in t
    boton(p, "Reportar falta de agua").on_click(None)
    assert portal.destinos[-1] == "reportar"
    boton(p, "Consultar con mi código").on_click(None)
    assert portal.destinos[-1] == "consultar"


def test_portal_reportar_registra_y_muestra_codigo(portal):
    p = pagina(portal, "reportar")
    campo(p, "Cuéntenos").value = "Se rompió una tubería en Chame, llevamos 2 días sin agua, unas 30 familias"
    antes = len(workflow.listar_reportes())
    boton(p, "Enviar reporte").on_click(None)
    nuevos = workflow.listar_reportes()[antes:]
    assert len(nuevos) == 1 and nuevos[0].estado == EstadoReporte.POR_REVISAR
    assert nuevos[0].mensaje_original.startswith("Se rompió una tubería")
    assert re.fullmatch(r"HID-[A-Z0-9]{8}", nuevos[0].codigo_seguimiento)
    t = textos(p)
    assert "Su reporte quedó registrado" in t and nuevos[0].codigo_seguimiento in t
    assert str(nuevos[0].id) not in re.findall(r"\b\d+\b", t.replace(nuevos[0].codigo_seguimiento, ""))   # sin ids internos
    assert nuevos[0].canal == "portal" if hasattr(nuevos[0], "canal") else True


def test_portal_reportar_doble_clic_no_duplica(portal):
    p = pagina(portal, "reportar")
    campo(p, "Cuéntenos").value = "En Vista Alegre no tenemos agua desde ayer"
    antes = len(workflow.listar_reportes())
    enviar = boton(p, "Enviar reporte")                  # el formulario se reemplaza por la confirmación: se conserva el botón
    enviar.on_click(None)
    enviar.on_click(None)                                # segundo clic: misma clave de idempotencia
    assert len(workflow.listar_reportes()) == antes + 1
    # al reabrir el formulario se usa una clave nueva
    clave_vieja = portal.clave_envio
    pagina(portal, "reportar")
    assert portal.clave_envio != clave_vieja


def test_portal_reportar_rechaza_texto_vacio(portal):
    p = pagina(portal, "reportar")
    antes = len(workflow.listar_reportes())
    campo(p, "Cuéntenos").value = "   "
    boton(p, "Enviar reporte").on_click(None)
    assert len(workflow.listar_reportes()) == antes and campo(p, "Cuéntenos").error_text


def test_portal_consultar_muestra_solo_estado_publico(portal):
    p = pagina(portal, "reportar")
    campo(p, "Cuéntenos").value = "Sin agua en El Coco desde hace tres dias, mi nombre es Secreto Privado"
    boton(p, "Enviar reporte").on_click(None)
    codigo = workflow.listar_reportes()[-1].codigo_seguimiento
    c = pagina(portal, "consultar")
    campo(c, "Código").value = codigo.lower()
    boton(c, "Consultar").on_click(None)
    t = textos(c)
    assert codigo in t and "revisión" in t
    assert "Secreto Privado" not in t and "tres dias" not in t


def test_portal_consultar_codigo_inexistente_no_revela_nada(portal):
    c = pagina(portal, "consultar")
    campo(c, "Código").value = "HID-ZZZZZZZZ"
    boton(c, "Consultar").on_click(None)
    assert "No encontramos un reporte" in textos(c)
    campo(c, "Código").value = ""
    boton(c, "Consultar").on_click(None)
    assert campo(c, "Código").error_text


def test_portal_avisos_son_comprensibles_y_sin_datos_internos(portal):
    confirmar_todo()
    plan = workflow.generar_plan("t")
    workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "t")
    t = textos(pagina(portal, "avisos"))
    assert "atención programada" in t or "en evaluación" in t
    for prohibido in ("litros", "cisterna", "borrador", "puntaje", "motivo", "supervisor"):
        assert prohibido not in t.lower()
    for r in workflow.listar_reportes():                      # ningún texto de reportes de otras personas
        assert r.mensaje_original[:30] not in t


def test_portal_no_invoca_funciones_administrativas(portal, monkeypatch):
    """Aunque quien navega sea la ciudadanía, el portal solo usa funciones públicas: ejecutar sin actor no debe fallar por permisos."""
    n = len(workflow.listar_reportes())                 # (conftest da actor admin a la prueba; el portal no lo usa)
    with auth.como(None):
        for clave, _, _, _ in app.PORTAL:
            assert "No se pudo mostrar" not in textos(pagina(portal, clave)), clave
        p = pagina(portal, "reportar")
        campo(p, "Cuéntenos").value = "No hay agua en Arraiján"
        boton(p, "Enviar reporte").on_click(None)
    assert len(workflow.listar_reportes()) == n + 1


# =============================================================== CONSOLA (con sesión)
def test_todas_las_paginas_admin_construyen_sin_caer_en_el_limite_de_errores(ctx):
    confirmar_todo()
    workflow.generar_plan("t")
    for clave, etiqueta, _, _ in app.PAGINAS:
        assert "No se pudo mostrar" not in textos(pagina(ctx, clave)), clave


def test_registro_de_paginas_cubre_los_modulos():
    import importlib
    for _, _, _, modulo in app.PAGINAS:
        assert hasattr(importlib.import_module(f"interface.flet_app.admin.{modulo}"), "build")
    for _, _, _, modulo in app.PORTAL:
        assert hasattr(importlib.import_module(f"interface.flet_app.portal.{modulo}"), "build")
    assert hasattr(importlib.import_module("interface.flet_app.admin.login"), "build")


def test_bandeja_recibe_reporte_con_el_responsable_de_la_sesion(ctx):
    p = pagina(ctx, "bandeja")
    campo(p, "Reporte ciudadano").value = "Se rompió una tubería en Chame, llevamos 2 días sin agua, unas 30 familias"
    antes = len(workflow.listar_reportes())
    boton(p, "Recibir reporte").on_click(None)
    nuevos = workflow.listar_reportes()[antes:]
    assert len(nuevos) == 1 and nuevos[0].estado == EstadoReporte.POR_REVISAR and nuevos[0].sector == "Chame"
    assert ctx.destinos[-1] == "revision" and ctx.seleccion["reporte_id"] == nuevos[0].id
    clave_usada = nuevos[0].clave_idempotencia
    ctx.clave_envio = clave_usada
    campo(p, "Reporte ciudadano").value = "texto distinto pero misma clave"
    boton(p, "Recibir reporte").on_click(None)
    assert len(workflow.listar_reportes()) == antes + 1


def test_bandeja_rechaza_texto_vacio(ctx):
    p = pagina(ctx, "bandeja")
    antes = len(workflow.listar_reportes())
    campo(p, "Reporte ciudadano").value = "   "
    boton(p, "Recibir reporte").on_click(None)
    assert len(workflow.listar_reportes()) == antes and campo(p, "Reporte ciudadano").error_text


def test_revision_confirma_y_valida(ctx):
    ctx.seleccion["reporte_id"] = 4  # Bejuco (50 personas)
    p = pagina(ctx, "revision")
    campo(p, "Horas sin agua").value = "abc"
    boton(p, "Confirmar datos").on_click(None)
    assert workflow.obtener_reporte(4).estado == EstadoReporte.POR_REVISAR  # número inválido: no se guarda
    campo(p, "Horas sin agua").value = "30"
    campo(p, "Personas afectadas").value = "55"
    boton(p, "Confirmar datos").on_click(None)
    r = workflow.obtener_reporte(4)
    assert r.estado == EstadoReporte.CONFIRMADO and r.horas_sin_agua == 30 and r.personas_afectadas == 55
    assert r.confirmado_por == "admin1"          # el responsable es el usuario autenticado, no un texto editable


def test_revision_sin_sector_no_confirma(ctx):
    r = workflow.crear_reporte("Se rompió una tubería cerca de la escuela", "t")  # sin sector
    ctx.seleccion["reporte_id"] = r.id
    p = pagina(ctx, "revision")
    boton(p, "Confirmar datos").on_click(None)
    assert workflow.obtener_reporte(r.id).estado == EstadoReporte.POR_REVISAR


def test_duplicados_decide_una_persona(ctx):
    ctx.seleccion["reporte_id"] = 2   # La Chorrera, duplicado probable de #1
    p = pagina(ctx, "duplicados")
    assert "Posible duplicado de #" in textos(p)
    boton(p, "Sí, es el mismo incidente").on_click(None)   # primer candidato (el más similar)
    r = workflow.obtener_reporte(2)
    assert r.estado == EstadoReporte.DUPLICADO and r.duplicado_de in (1, 3)
    assert workflow.obtener_reporte(r.duplicado_de).estado == EstadoReporte.POR_REVISAR  # nada se fusiona ni se borra
    assert workflow.obtener_reporte(2).mensaje_original  # el mensaje original se conserva


def test_plan_generar_aprobar_y_rechazar_con_reglas(ctx):
    confirmar_todo()
    p = pagina(ctx, "plan")
    boton(p, "Generar propuesta").on_click(None)
    assert len(workflow.listar_planes()) == 1
    p = pagina(ctx, "plan")
    plan = workflow.listar_planes()[0]
    assert plan.estado == EstadoPlan.PROPUESTO and "PROPUESTA" in textos(p)
    boton(p, "Rechazar plan").on_click(None)       # sin motivo: la interfaz lo pide antes de molestar al servidor
    assert workflow.obtener_plan(plan.id).estado == EstadoPlan.PROPUESTO and campo(p, "Motivo").error_text
    boton(p, "Aprobar plan").on_click(None)
    assert workflow.obtener_plan(plan.id).estado == EstadoPlan.APROBADO
    p = pagina(ctx, "plan")
    assert "ya no se puede modificar" in textos(p)


def test_plan_edicion_exige_motivo_y_respeta_capacidad(ctx):
    confirmar_todo()
    workflow.generar_plan("t")
    p = pagina(ctx, "plan")
    campo(p, "Litros").value = "0"  # quitar la primera asignación
    boton(p, "Guardar modificaciones").on_click(None)
    assert not workflow.listar_planes()[0].editado and ctx.avisos[-1][0] == "bad"        # sin motivo
    campo(p, "Motivo").value = "cisterna reservada"
    boton(p, "Guardar modificaciones").on_click(None)
    assert workflow.listar_planes()[0].editado
    p = pagina(ctx, "plan")
    campo(p, "Litros").value = "999999"
    campo(p, "Motivo").value = "exceso"
    boton(p, "Guardar modificaciones").on_click(None)
    assert ctx.avisos[-1][0] == "bad" and "excede" in ctx.avisos[-1][1]               # capacidad validada en el backend


def test_comunicacion_borrador_local_no_se_envia(ctx):
    confirmar_todo()
    plan = workflow.generar_plan("t")
    p = pagina(ctx, "comunicacion")
    assert "BORRADOR" in textos(p) and "no se envían" in textos(p)
    boton(p, "Redactar con IA").on_click(None)   # sin Gemini: devuelve la plantilla local y lo dice
    assert "origen: local" in textos(p)
    assert workflow.obtener_plan(plan.id).estado == EstadoPlan.PROPUESTO


def test_navegacion_desde_el_panel(ctx):
    p = pagina(ctx, "panel")
    boton(p, "Bandeja de reportes").on_click(None)
    assert ctx.destinos[-1] == "bandeja"


def test_cargar_demo_vive_en_diagnostico_y_pide_confirmacion():
    """La acción destructiva usa ctx.confirmar (diálogo); sin Page se ejecuta directo, por eso se prueba el contrato."""
    import inspect

    from interface.flet_app.admin import datos_ejemplo
    fuente = inspect.getsource(datos_ejemplo)
    assert "ctx.confirmar(" in fuente and "BORRA" in fuente
    grupos = dict(app.GRUPOS_ADMIN)
    assert "datos_ejemplo" in [k for k, *_ in grupos[app.GRUPO_DIAGNOSTICO]]


def test_error_en_una_pagina_no_tumba_la_aplicacion(ctx, monkeypatch):
    import importlib
    mod = importlib.import_module("interface.flet_app.admin.panel")
    monkeypatch.setattr(mod, "build", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "No se pudo mostrar" in textos(pagina(ctx, "panel"))


# ===================================== PORTAL CON MAPA Y FOTOS · MAPA · IA · MISIONES (UI nueva)
import base64
import struct
import zlib
from dataclasses import dataclass
from types import SimpleNamespace

from interface.flet_app import mapa as mapa_ui
from interface.flet_app.portal import reportar as portal_reportar
from services import workflow_ia


def png_valido(color: int = 0) -> bytes:
    """PNG real de 1x1 píxel (la firma binaria es lo que valida el backend)."""
    def trozo(tipo, datos):
        c = struct.pack(">I", len(datos)) + tipo + datos
        return c + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(bytes([0, color, 0, 0]))) + trozo(b"IEND", b""))


@dataclass
class ArchivoDePrueba:
    """DOBLE DE PRUEBA de ft.FilePickerFile (el selector real necesita una ventana)."""
    name: str
    bytes: bytes
    size: int = 0

    def __post_init__(self):
        self.size = len(self.bytes)


class PaginaFalsa:
    """DOBLE DE PRUEBA de ft.Page: lo mínimo para que Ctx muestre diálogos y avisos sin ventana."""
    def __init__(self):
        self.services = []
        self.mostrados = []

    def update(self):
        pass

    def show_dialog(self, d):
        self.mostrados.append(d)

    def pop_dialog(self):
        pass

    def run_task(self, *a, **k):
        pass


def zona_portal(p):
    z = buscar(p, ft.Container, lambda c: isinstance(c.data, dict) and "procesar" in c.data)
    assert z is not None
    return z.data


def todas_las_imagenes(raiz):
    return [c for c in recorrer(raiz) if isinstance(c, ft.Image)]


def test_portal_reporta_con_ubicacion_del_mapa_y_dos_fotos(portal):
    p = pagina(portal, "reportar")
    d = zona_portal(p)
    campo(p, "Cuéntenos").value = "Sin agua en Chame desde ayer, somos 12 familias"
    d["selector"]._tap(SimpleNamespace(coordinates=SimpleNamespace(latitude=8.6, longitude=-79.88)))   # clic en el mapa
    assert d["selector"].punto == (8.6, -79.88)
    d["procesar"]([ArchivoDePrueba("a.png", png_valido(1)), ArchivoDePrueba("b.PNG", png_valido(2))])
    assert len(d["fotos"].items) == 2 and len(todas_las_imagenes(p)) == 2
    boton(p, "Enviar reporte").on_click(None)
    r = workflow.listar_reportes()[-1]
    assert (r.lat, r.lng) == (8.6, -79.88) and r.ubicacion_fuente == "mapa" and len(r.foto_ids) == 2
    t = textos(p)
    assert r.codigo_seguimiento in t and "ubicación en el mapa y 2 fotos" in t and "Copiar código" in todo_el_texto(p)
    data, mime = workflow.obtener_foto(r.id, r.foto_ids[0])          # persistente y recuperable por el administrador
    assert mime == "image/png" and data.startswith(b"\x89PNG")
    # consulta pública: solo informa si hay ubicación y cuántas fotos; nunca coordenadas
    c = pagina(portal, "consultar")
    campo(c, "Código").value = r.codigo_seguimiento
    boton(c, "Consultar").on_click(None)
    tc = textos(c)
    assert "Marcada en el mapa" in tc and "2 fotos" in tc and "8.6" not in tc and "79.88" not in tc


def test_portal_sin_ubicacion_si_elige_no_saber_ubicarlo(portal):
    p = pagina(portal, "reportar")
    d = zona_portal(p)
    campo(p, "Cuéntenos").value = "No hay agua cerca de la escuela de Arraiján"
    d["selector"].fijar(8.9, -79.65)                                   # había marcado un punto…
    boton(p, "No sé ubicarlo en el mapa").on_click(None)               # …y luego prefiere no usarlo
    assert d["selector"].punto is None and "Sin ubicación en el mapa" in textos(p)
    boton(p, "Enviar reporte").on_click(None)
    r = workflow.listar_reportes()[-1]
    assert r.lat is None and r.lng is None and r.ubicacion_fuente is None
    assert "Se guardó también" not in textos(p)


def test_portal_valida_fotos_en_el_cliente_de_forma_amistosa(portal):
    p = pagina(portal, "reportar")
    d = zona_portal(p)
    grande = ArchivoDePrueba("grande.jpg", b"\xff\xd8\xff" + b"0" * (portal_reportar.MAX_BYTES + 1))
    d["procesar"]([ArchivoDePrueba("doc.pdf", b"%PDF-1.4 x"), grande, ArchivoDePrueba("vacia.jpg", b"")])
    t = textos(p)
    assert not d["fotos"].items
    assert "no es una foto admitida" in t and "el máximo es" in t and "No pudimos leer" in t
    d["procesar"]([ArchivoDePrueba(f"f{i}.png", png_valido(i)) for i in range(4)])
    assert len(d["fotos"].items) == portal_reportar.MAX_FOTOS and "hasta 3 fotos" in textos(p)
    quitar = buscar(p, ft.TextButton, lambda c: c.content == "Quitar")
    quitar.on_click(None)
    assert len(d["fotos"].items) == portal_reportar.MAX_FOTOS - 1


def test_portal_errores_del_backend_se_muestran_en_espanol(portal):
    p = pagina(portal, "reportar")
    d = zona_portal(p)
    campo(p, "Cuéntenos").value = "Sin agua en El Coco"
    d["procesar"]([ArchivoDePrueba("falsa.jpg", b"esto no es una imagen de verdad")])   # pasa el filtro de cliente; el backend la rechaza por firma
    antes = len(workflow.listar_reportes())
    boton(p, "Enviar reporte").on_click(None)
    assert len(workflow.listar_reportes()) == antes
    t = textos(p)
    assert "No pudimos registrar su reporte" in t and "imagen" in t.lower() and portal.avisos[-1][0] == "bad"
    d["fotos"].items.clear()
    d["selector"].fijar(10.0, -70.0)                                                     # fuera de Panamá Oeste: lo rechaza el backend
    boton(p, "Enviar reporte").on_click(None)
    assert len(workflow.listar_reportes()) == antes and portal.avisos[-1][0] == "bad"


def test_portal_con_mapa_no_muestra_nada_tecnico(portal):
    t = todo_el_texto(pagina(portal, "reportar")).lower()
    for palabra in PROHIBIDAS + ("recomendaci", "misión"):
        assert palabra not in t, palabra


def test_mapa_no_inventa_coordenadas():
    pts = mapa_ui.puntos_de_mapa({"reportes": [{"id": 1, "lat": None, "lng": None}, {"id": 2, "lat": 8.9, "lng": -79.7, "estado": "confirmado"}, {"id": 3, "lat": 400, "lng": 1}],
                                  "cisternas": [{"id": "C1", "lat": None, "lng": None}], "paradas": [{"orden": 1, "lat": 8.8, "lng": -79.7}, {"orden": 2, "lat": None, "lng": None}]})
    assert [r["id"] for r in pts["reportes"]] == [2] and pts["cisternas"] == [] and [p["orden"] for p in pts["paradas"]] == [1]
    assert mapa_ui.puntos_de_mapa(None) == {"reportes": [], "cisternas": [], "paradas": []}
    assert "no trae ubicación" in textos(mapa_ui.mini(None, None))


def test_pagina_mapa_con_datos_reales_y_vacia(ctx, monkeypatch):
    r = workflow.crear_reporte("Sin agua en Chame, 20 familias", "t", lat=8.6, lng=-79.88)
    p = pagina(ctx, "mapa")
    t = textos(p)
    assert "No se pudo mostrar" not in t and "Reportes en el mapa" in t and "Mapa operativo" in t
    if mapa_ui.disponible():
        import flet_map as fm
        assert [c for c in recorrer(p) if isinstance(c, fm.Map)] or any(isinstance(getattr(c, "content", None), fm.Map) for c in recorrer(p))
    monkeypatch.setattr(workflow, "datos_mapa", lambda: {"reportes": [], "cisternas": [], "paradas": []})
    assert "Todavía no hay nada con ubicación" in textos(pagina(ctx, "mapa"))
    monkeypatch.delattr(workflow, "datos_mapa")                    # función del servidor ausente: aviso claro, no caída
    t = textos(pagina(ctx, "mapa"))
    assert "No se pudieron obtener los datos del mapa" in t and "No se pudo mostrar" not in t
    assert r.id


def test_bandeja_filtra_y_muestra_detalle_con_fotos_y_coordenadas(ctx):
    r = workflow.crear_reporte("Sin agua en Capira hace 3 dias", "t", lat=8.76, lng=-79.88, fotos=[{"nombre": "x.png", "bytes": png_valido()}])
    p = pagina(ctx, "bandeja")
    campo(p, "Buscar").value = r.codigo_seguimiento.lower()
    campo(p, "Buscar").on_change(None)
    assert f"#{r.id}" in todo_el_texto(p) and "1 de" in textos(p)
    campo(p, "Buscar").value = "texto-que-no-existe-xyz"
    campo(p, "Buscar").on_change(None)
    assert "0 de" in textos(p)
    ctx.seleccion["reporte_id"] = r.id
    p = pagina(ctx, "bandeja")
    t = textos(p)
    assert "Detalle del reporte" in t and "8.76000, -79.88000" in t and "Fotos (1)" in t
    assert len(todas_las_imagenes(p)) == 1
    boton(p, "Revisar y confirmar").on_click(None)
    assert ctx.destinos[-1] == "revision" and ctx.seleccion["reporte_id"] == r.id


def test_bandeja_rechazar_desde_detalle_abre_dialogo_con_motivo(ctx, monkeypatch):
    r = workflow.crear_reporte("Sin agua en Capira hace 3 dias", "t")
    ctx.seleccion["reporte_id"] = r.id
    p = pagina(ctx, "bandeja")
    capturado = {}
    monkeypatch.setattr(ctx, "dialogo", lambda titulo, contenido, acciones=None: capturado.update(titulo=titulo, contenido=contenido, acciones=acciones))
    boton(p, "Rechazar reporte").on_click(None)
    assert f"#{r.id}" in capturado["titulo"] and "Motivo" in todo_el_texto(capturado["contenido"])
    ctx.page = PaginaFalsa()
    caja = buscar(capturado["contenido"], ft.TextField)
    capturado["acciones"][1].on_click(None)                              # sin motivo: lo detiene el cliente, no se llama al servidor
    assert workflow.obtener_reporte(r.id).estado == EstadoReporte.POR_REVISAR and caja.error_text
    caja.value = "reporte de prueba"
    capturado["acciones"][1].on_click(None)
    assert workflow.obtener_reporte(r.id).estado == EstadoReporte.RECHAZADO


def confirmar_con_sector(sector="Chame"):
    r = workflow.crear_reporte(f"Sin agua en {sector} hace 30 horas, unas 40 personas", "t")
    return workflow.corregir_y_confirmar(r.id, "t", sector, 30, 40, "alta", None)


def test_recomendaciones_analizar_y_aprobar_crea_mision(ctx):
    r = confirmar_con_sector("Chame")
    ctx.seleccion["reporte_id"] = r.id
    p = pagina(ctx, "recomendaciones")
    assert "No se pudo mostrar" not in textos(p) and "PROPUESTA" in textos(p)
    boton(p, "Analizar reporte").on_click(None)
    recs = workflow_ia.listar_recomendaciones()
    assert len(recs) == 1 and recs[0].estado.value == "propuesta" and ctx.destinos[-1] == "recomendaciones"
    p = pagina(ctx, "recomendaciones")
    t = textos(p)
    assert f"Recomendación #{recs[0].id}" in t and "Etapas del análisis" in t and "reglas locales (sin IA)" in todo_el_texto(p)
    if not recs[0].cisterna_sugerida:
        pytest.skip("el análisis local no propuso cisterna en esta base")
    boton(p, "Aprobar").on_click(None)                               # sin Page, ctx.confirmar ejecuta directo
    rec = workflow_ia.obtener_recomendacion(recs[0].id)
    assert rec.estado.value == "aprobada" and rec.mision_id and rec.decidida_por == "admin1"
    assert workflow.obtener_mision(rec.mision_id).creada_por == "admin1"
    p = pagina(ctx, "recomendaciones")
    campo_est = campo(p, "Mostrar")
    campo_est.value = "todas"
    campo_est.on_select(None)
    assert f"Misión #{rec.mision_id} creada" in textos(p)


def test_recomendaciones_rechazar_exige_motivo_y_modificar_cambia_litros(ctx):
    r = confirmar_con_sector("Capira")
    workflow_ia.analizar_reporte(r.id, "t")
    rec = workflow_ia.listar_recomendaciones()[0]
    p = pagina(ctx, "recomendaciones")
    boton(p, "Rechazar").on_click(None)                               # sin motivo: no cambia nada y el campo avisa
    assert workflow_ia.obtener_recomendacion(rec.id).estado.value == "propuesta"
    assert campo(p, "Motivo (obligatorio").error_text
    if rec.paradas_sugeridas:
        boton(p, "Modificar").on_click(None)                          # primer clic: muestra el editor
        boton(p, "Modificar").on_click(None)                          # segundo clic sin motivo: no guarda
        assert workflow_ia.obtener_recomendacion(rec.id).estado.value == "propuesta"
        campo(p, "Motivo (obligatorio").value = "ajusto la carga"
        campo(p, "Litros · parada 1").value = "50"
        boton(p, "Modificar").on_click(None)
        rec2 = workflow_ia.obtener_recomendacion(rec.id)
        assert rec2.estado.value == "modificada" and rec2.paradas_sugeridas[0].litros == 50 and rec2.mision_id
    else:
        campo(p, "Motivo (obligatorio").value = "no procede"
        boton(p, "Rechazar").on_click(None)
        assert workflow_ia.obtener_recomendacion(rec.id).estado.value == "rechazada"


def test_recomendaciones_distingue_procedencia_por_etapa():
    from interface.flet_app.admin.recomendaciones import pill_procedencia
    assert "Gemini" in todo_el_texto(pill_procedencia("gemini"))
    assert "respaldo" in todo_el_texto(pill_procedencia("reglas(fallback)")) and "Gemini" not in todo_el_texto(pill_procedencia("reglas(fallback)"))
    assert "sin IA" in todo_el_texto(pill_procedencia("reglas"))


def test_misiones_registrar_terminal_muestra_el_token_una_sola_vez(ctx, monkeypatch):
    p = pagina(ctx, "misiones")
    assert "No se pudo mostrar" not in textos(p)
    cap = {}
    monkeypatch.setattr(ctx, "dialogo", lambda titulo, contenido, acciones=None: cap.update(titulo=titulo, contenido=contenido))
    monkeypatch.setattr(ctx, "confirmar", lambda t, m, on_si, peligro=False: on_si())
    cisterna = workflow.listar_cisternas()[0].id
    boton(p, "Registrar terminal").on_click(None)                    # primera cisterna sin terminal
    assert "Terminal registrada" in cap["titulo"]

    def token_en(contenido):
        return [str(c.value) for c in recorrer(contenido) if isinstance(c, ft.Text) and c.font_family == "monospace"]
    token = token_en(cap["contenido"])
    assert token and "NO se volverá a mostrar" in todo_el_texto(cap["contenido"])
    assert workflow.autenticar_dispositivo(token[0]) is not None       # el token mostrado es el real
    p = pagina(ctx, "misiones")
    assert token[0] not in todo_el_texto(p)                          # nunca vuelve a aparecer
    assert any(d.cisterna_id == cisterna and d.activo for d in workflow.listar_dispositivos())
    cap.clear()
    buscar(p, ft.IconButton, lambda c: c.tooltip == "Rotar token").on_click(None)
    nuevo = token_en(cap["contenido"])
    assert nuevo and nuevo[0] != token[0] and workflow.autenticar_dispositivo(token[0]) is None
    buscar(pagina(ctx, "misiones"), ft.IconButton, lambda c: c.tooltip == "Desactivar terminal").on_click(None)
    assert not next(d for d in workflow.listar_dispositivos() if d.cisterna_id == cisterna).activo


def test_misiones_crear_manual_ver_avance_eventos_y_cancelar(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "confirmar", lambda t, m, on_si, peligro=False: on_si())
    cis = next(c for c in workflow.listar_cisternas() if c.estado.value == "disponible")
    p = pagina(ctx, "misiones")
    campo(p, "Sector de la parada").value = "Chame"
    campo(p, "Litros").value = "2000"
    boton(p, "Agregar parada").on_click(None)
    campo(p, "Cisterna").value = cis.id
    campo(p, "Motivo o comentario").value = "prueba manual"
    boton(p, "Crear misión manual").on_click(None)
    ms = workflow.listar_misiones()
    assert len(ms) == 1 and ms[0].cisterna_id == cis.id and ms[0].creada_por == "admin1" and ms[0].paradas[0].litros == 2000
    workflow.registrar_dispositivo(cis.id, "T1")
    workflow.dispositivo_recibir_eventos(cis.id, [{"uid": "evento-0001", "tipo": "llegada", "mision_id": ms[0].id, "parada_orden": 1}])
    p = pagina(ctx, "misiones")
    t = textos(p)
    assert f"Misión #{ms[0].id}" in t and "llegó" in t and "Avance: 0 de 1" in t
    boton(p, "Actualizar").on_click(None)                            # el botón relee del servidor sin romper
    boton(p, "Cancelar misión").on_click(None)                       # sin motivo
    assert workflow.obtener_mision(ms[0].id).estado.value == "en_curso"
    campo(p, "Motivo para cancelar").value = "ya no hace falta"
    boton(p, "Cancelar misión").on_click(None)
    assert workflow.obtener_mision(ms[0].id).estado.value == "cancelada"


def test_misiones_desde_plan_aprobado(ctx):
    confirmar_todo()
    plan = workflow.generar_plan("t")
    plan = workflow.decidir_plan(plan.id, AccionDecision.APROBAR, "t")
    cis = plan.asignaciones[0].cisterna_id
    p = pagina(ctx, "misiones")
    campo(p, "Cisterna").value = cis
    boton(p, "Crear misión desde el plan").on_click(None)
    ms = workflow.listar_misiones()
    assert len(ms) == 1 and ms[0].plan_id == plan.id and ms[0].cisterna_id == cis


def test_manejadores_de_eventos_usan_la_sesion_aunque_no_haya_actor_ambiente(ctx):
    """Flet ejecuta los manejadores fuera de la construcción de la página (hilos sin actor): las lecturas del detalle de la bandeja
    y de «Actualizar» en misiones deben apoyarse en la sesión del Ctx, no en el actor ambiente que la fixture deja puesto."""
    r = workflow.crear_reporte("Sin agua en Capira hace 3 dias", "t", lat=8.76, lng=-79.88, fotos=[{"nombre": "x.png", "bytes": png_valido()}])
    p = pagina(ctx, "bandeja")
    abrir = buscar(p, ft.TextButton, lambda c: c.content == f"#{r.id}")
    with auth.como(None):
        abrir.on_click(None)
    assert "Detalle del reporte" in textos(p) and "No se pudo leer una foto" not in todo_el_texto(p) and len(todas_las_imagenes(p)) == 1
    m = pagina(ctx, "misiones")
    with auth.como(None):
        boton(m, "Actualizar").on_click(None)
    assert "Parte de los datos no está disponible" not in todo_el_texto(m)


# ============================================ v3: seguimiento fiel, usuarios, honestidad de la interfaz
def test_consulta_linea_de_progreso_fiel_a_los_estados_reales():
    from interface.flet_app.portal import consultar as K
    base = {"codigo": "HID-ABCDEFGH", "sector": None, "fecha_creacion": "2026-10-17T10:00"}
    casos = {"Recibido, en revisión.": ("por_revisar", 1), "Confirmado, en evaluación.": ("confirmado", 2),
             "Atención programada, hora aún sin confirmar.": ("en_plan", 3)}
    for texto, (clave, pos) in casos.items():
        d = {**base, "estado_publico": texto}
        assert K.estado_de(d) == clave
        panel = K._resultado(d)
        assert buscar(panel, ft.ResponsiveRow) is not None
        assert "no indica fecha ni hora" in textos(panel)          # nunca se promete hora
    # la clave `estado` del servidor, si existe, manda sobre el texto
    assert K.estado_de({**base, "estado": "en_plan", "estado_publico": "x"}) == "en_plan"
    for texto, clave in (("Agrupado con otro reporte del mismo problema.", "duplicado"), ("No procede como reporte.", "rechazado")):
        d = {**base, "estado_publico": texto}
        assert K.estado_de(d) == clave
        panel = K._resultado(d)
        assert buscar(panel, ft.ResponsiveRow) is None             # estado terminal: sin línea de progreso
        assert K.TERMINALES[clave][0] in textos(panel) or K.TERMINALES[clave][0] in todo_el_texto(panel)


def test_consulta_valida_el_formato_del_codigo_antes_de_llamar(portal):
    p = pagina(portal, "consultar")
    campo(p, "Código").value = "no es un codigo!"
    boton(p, "Consultar").on_click(None)
    assert campo(p, "Código").error_text and "HID-" in campo(p, "Código").error_text


def test_consulta_de_un_reporte_real_muestra_progreso(portal):
    r = workflow.crear_reporte("Sin agua en Capira desde ayer", "t")
    portal.seleccion["codigo"] = r.codigo_seguimiento
    p = pagina(portal, "consultar")
    assert buscar(p, ft.ResponsiveRow) is not None and "En revisión" in todo_el_texto(p)


def test_metricas_sin_relleno_ni_mapa_inventado(ctx):
    p = pagina(ctx, "metricas")
    t = todo_el_texto(p)
    assert "futuro" not in t and "esquemático" not in t.lower() and "Dispositivos" not in t


def test_ejecutar_bloquea_el_boton_mientras_trabaja_y_lo_libera():
    c = construir_ctx(admin=True)
    b = ft.Button("x")
    visto = {}
    ok, v = c.ejecutar(lambda: visto.setdefault("durante", b.disabled) or 7, bloquear=b)
    assert ok and visto["durante"] is True and b.disabled is False
    ok, _ = c.ejecutar(lambda: 1 / 0, bloquear=b)
    assert not ok and b.disabled is False                          # también se libera si falla


def test_pedir_motivo_exige_motivo_en_el_cliente(monkeypatch):
    c = construir_ctx(admin=True)
    c.page = PaginaFalsa()
    cap = {}
    monkeypatch.setattr(c, "dialogo", lambda t, cont, acc=None: cap.update(cont=cont, acc=acc))
    llamadas = []
    c.pedir_motivo("Cancelar", "Motivo", lambda m: llamadas.append(m) or True, "Cancelar misión")
    caja = buscar(cap["cont"], ft.TextField)
    cap["acc"][1].on_click(None)
    assert llamadas == [] and caja.error_text
    caja.value = "  porque sí "
    cap["acc"][1].on_click(None)
    assert llamadas == ["porque sí"] and caja.error_text is None


def test_usuarios_sin_funciones_del_servidor_no_se_rompe(ctx, monkeypatch):
    for n in ("listar_usuarios_admin", "crear_usuario_admin", "desactivar_usuario", "reactivar_usuario", "cambiar_rol_usuario"):
        monkeypatch.delattr(workflow, n, raising=False)
    p = pagina(ctx, "usuarios")
    t = todo_el_texto(p)
    assert "todavía no está disponible" in t and "No se pudo mostrar" not in t


def test_usuarios_lista_confirma_y_muestra_el_error_del_ultimo_admin(ctx, monkeypatch):
    estado = {"u": [{"usuario": "admin1", "rol": "administrador", "activo": True, "creado": "2026-10-01T10:00"},
                    {"usuario": "ana", "rol": "usuario", "activo": True, "creado": "2026-10-02T10:00"}]}
    llamadas = []

    def desactivar(usuario):
        if usuario == "admin1":
            raise workflow.ErrorDeNegocio("No se puede dejar el sistema sin administradores activos.")
        llamadas.append(usuario)
        next(x for x in estado["u"] if x["usuario"] == usuario)["activo"] = False

    monkeypatch.setattr(workflow, "listar_usuarios_admin", lambda: list(estado["u"]), raising=False)
    monkeypatch.setattr(workflow, "desactivar_usuario", desactivar, raising=False)
    p = pagina(ctx, "usuarios")
    t = todo_el_texto(p)
    assert "admin1" in t and "(usted)" in t and "administrador" in t and "ana" in t
    desactivar_btns = [b for b in recorrer(p) if isinstance(b, ft.TextButton) and b.content == "Desactivar"]
    assert len(desactivar_btns) == 2
    desactivar_btns[1].on_click(None)                               # confirmar() sin página ejecuta directo
    assert llamadas == ["ana"]
    desactivar_btns[0].on_click(None)
    assert ctx.avisos[-1] == ("bad", "No se puede dejar el sistema sin administradores activos.")


def test_usuarios_exige_sesion_de_administrador(portal):
    assert app.resolver(portal, "usuarios") == "login"


def test_usuarios_con_backend_real_crea_cambia_y_protege_al_ultimo_admin(ctx):
    p = pagina(ctx, "usuarios")
    assert "admin1" in todo_el_texto(p) and "(usted)" in todo_el_texto(p)
    campo(p, "Usuario").value = "ana"
    campo(p, "Contraseña").value = CLAVE
    boton(p, "Crear cuenta").on_click(None)          # confirmar() sin página ejecuta directo
    assert any(u["usuario"] == "ana" for u in workflow.listar_usuarios_admin())
    p = pagina(ctx, "usuarios")
    sin_admin = [b for b in recorrer(p) if isinstance(b, ft.TextButton) and b.content == "Desactivar"]
    sin_admin[0].on_click(None)                      # admin1 es el único administrador activo
    assert ctx.avisos[-1][0] == "bad" and "administradores" in ctx.avisos[-1][1]
    assert next(u for u in workflow.listar_usuarios_admin() if u["usuario"] == "admin1")["activo"] is True
