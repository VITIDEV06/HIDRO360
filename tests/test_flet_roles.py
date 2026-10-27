"""Roles en la interfaz: login de administración, sesión, páginas que no se construyen sin sesión y cierre de sesión.
Ocultar botones NO es seguridad; el rechazo en el backend lo prueba tests/test_auth.py. Aquí se verifica que la UI
no construya páginas administrativas sin sesión, que no use un «Responsable» editable y que maneje la sesión caducada."""
import pytest

from interface.flet_app import main as app
from services import auth, workflow
from tests.test_flet import CLAVE, boton, buscar, campo, construir_ctx, pagina, textos, todo_el_texto

import flet as ft


@pytest.fixture
def anonimo():
    c = construir_ctx()
    # sin el actor de administrador que pone conftest: la UI debe apoyarse solo en su propia sesión
    with auth.como(None):
        yield c


def test_login_con_credenciales_invalidas_da_mensaje_generico(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    p = pagina(anonimo, "login")
    campo(p, "Usuario").value = "admin1"
    campo(p, "Contraseña").value = "otra-Clave-9"
    boton(p, "Iniciar sesión").on_click(None)
    assert not anonimo.es_admin and "incorrectos" in textos(p)
    campo(p, "Usuario").value = "noexiste"
    campo(p, "Contraseña").value = "otra-Clave-9"
    boton(p, "Iniciar sesión").on_click(None)
    assert not anonimo.es_admin and "incorrectos" in textos(p)
    assert campo(p, "Contraseña").value == ""            # la contraseña no queda en el campo


def test_campos_de_login_ocultan_la_contrasena_pero_permiten_revelarla(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    c = campo(pagina(anonimo, "login"), "Contraseña")
    assert c.password is True and c.can_reveal_password is True


def test_login_valido_abre_la_consola_en_el_destino_pedido(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.ir("plan")                                       # sin sesión: el destino se recuerda y se muestra el login
    assert anonimo.pagina_actual == "login" and anonimo.destino_tras_login == "plan"
    p = pagina(anonimo, "login")
    campo(p, "Usuario").value = "admin1"
    campo(p, "Contraseña").value = CLAVE
    boton(p, "Iniciar sesión").on_click(None)
    assert anonimo.es_admin and anonimo.actor.usuario == "admin1" and anonimo.destinos[-1] == "plan"
    assert anonimo.responsable == "admin1"


def test_cuenta_sin_rol_admin_no_entra(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    auth.crear_usuario("pepe", CLAVE, auth.Rol.USUARIO)
    p = pagina(anonimo, "login")
    campo(p, "Usuario").value = "pepe"
    campo(p, "Contraseña").value = CLAVE
    boton(p, "Iniciar sesión").on_click(None)
    assert not anonimo.es_admin and anonimo.actor is None


def test_sin_administradores_muestra_instrucciones_y_no_crea_cuentas(anonimo):
    from data import database as db
    for d in db.listar("usuarios"):
        db.eliminar("usuarios", d["usuario"]) if hasattr(db, "eliminar") else None
    if auth.hay_administradores():
        pytest.skip("la base no permite vaciar usuarios en esta prueba")
    p = pagina(anonimo, "login")
    assert "scripts.crear_admin" in textos(p)
    assert buscar(p, ft.TextField) is None             # no hay formulario de alta ni de entrada


def test_paginas_admin_no_se_construyen_sin_sesion(anonimo):
    for clave, _, _, modulo in app.PAGINAS:
        p = pagina(anonimo, clave)
        t = textos(p)
        assert "Administración HIDRO360" in t or "scripts.crear_admin" in t, clave   # es el login, no la página pedida
        assert anonimo.destino_tras_login == clave


def test_paginas_admin_construyen_con_sesion(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.abrir_sesion("admin1", CLAVE)
    for clave, _, _, _ in app.PAGINAS:
        t = textos(pagina(anonimo, clave))
        assert "No se pudo mostrar" not in t and "Administración HIDRO360" not in t, clave


def test_la_consola_no_tiene_campo_responsable_editable(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.abrir_sesion("admin1", CLAVE)
    for clave, _, _, _ in app.PAGINAS:
        raiz = pagina(anonimo, clave)
        assert buscar(raiz, (ft.TextField, ft.Dropdown), lambda c: "responsable" in str(c.label or "").lower()) is None, clave


def test_cerrar_sesion_invalida_el_token(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.abrir_sesion("admin1", CLAVE)
    token = anonimo._token
    assert auth.actor_de_token(token) is not None
    anonimo.cerrar_sesion()
    assert auth.actor_de_token(token) is None and not anonimo.es_admin
    assert "Administración HIDRO360" in textos(pagina(anonimo, "bandeja"))


def test_sesion_expirada_en_una_operacion_devuelve_al_login(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.abrir_sesion("admin1", CLAVE)
    anonimo.pagina_actual = "bandeja"
    auth.cerrar_sesion(anonimo._token)                         # la sesión desaparece por otro lado (caducidad, reinicio)
    ok, _ = anonimo.ejecutar(lambda: workflow.listar_reportes())
    assert not ok and anonimo.destinos[-1] == "login" and anonimo.avisos[-1][0] == "bad"
    assert anonimo.destino_tras_login == "bandeja"


def test_ejecutar_aplica_el_actor_de_la_sesion_y_el_portal_ninguno(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.abrir_sesion("admin1", CLAVE)
    ok, quien = anonimo.ejecutar(lambda: auth.actor())
    assert ok and quien.usuario == "admin1" and quien.rol == auth.Rol.ADMIN
    ok, quien = anonimo.ejecutar(lambda: auth.actor(), publico=True)
    assert ok and quien is None


def test_error_de_autorizacion_se_muestra_sin_cerrar_la_sesion(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    anonimo.abrir_sesion("admin1", CLAVE)

    def prohibido():
        raise auth.ErrorAutorizacion("Esta operación requiere permisos de administrador.")
    ok, _ = anonimo.ejecutar(prohibido)
    assert not ok and anonimo.avisos[-1] == ("bad", "Esta operación requiere permisos de administrador.") and anonimo.es_admin


def test_barra_del_portal_ofrece_acceso_discreto_y_la_consola_cierre_de_sesion():
    from interface.flet_app import main as m
    fuente = open(m.__file__, encoding="utf-8").read()
    assert "Acceso administración" in fuente and "Cerrar sesión" in fuente and "Portal público" in fuente
    assert m.GRUPO_DIAGNOSTICO == "DIAGNÓSTICO Y DESARROLLO"
    assert [g for g, _ in m.GRUPOS_ADMIN] == ["OPERACIÓN", "FLOTA", "SEGUIMIENTO", "SISTEMA", "DIAGNÓSTICO Y DESARROLLO"]


def test_paginas_de_diagnostico_estan_solo_en_la_consola():
    claves_diag = {k for k, *_ in dict(app.GRUPOS_ADMIN)[app.GRUPO_DIAGNOSTICO]}
    assert claves_diag == {"trazas", "impacto", "gemini", "datos_ejemplo"}
    assert not claves_diag & app.CLAVES_PORTAL


def test_paginas_nuevas_son_solo_administrativas_y_no_hay_pagina_de_dispositivos_aparte():
    claves = {k for k, *_ in app.PAGINAS}
    assert {"mapa", "recomendaciones", "misiones"} <= claves and "dispositivos" not in claves
    assert not {"mapa", "recomendaciones", "misiones"} & app.CLAVES_PORTAL


def test_cuenta_usuario_no_llega_a_mapa_recomendaciones_ni_misiones(anonimo):
    auth.crear_usuario("admin1", CLAVE, auth.Rol.ADMIN)
    auth.crear_usuario("pepe", CLAVE, auth.Rol.USUARIO)
    p = pagina(anonimo, "login")
    campo(p, "Usuario").value = "pepe"
    campo(p, "Contraseña").value = CLAVE
    boton(p, "Iniciar sesión").on_click(None)
    for clave in ("mapa", "recomendaciones", "misiones", "bandeja"):
        assert "Administración HIDRO360" in textos(pagina(anonimo, clave)), clave
        assert anonimo.destino_tras_login == clave and not anonimo.es_admin
