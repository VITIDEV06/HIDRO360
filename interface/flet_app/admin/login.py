"""Acceso a la consola de administración. Mensajes genéricos; el token vive solo en memoria (Ctx).
Las cuentas NO se crean desde la interfaz: python -m scripts.crear_admin."""

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import auth


def build(ctx) -> ft.Control:
    usuario = C.campo("Usuario", expand=True, autofocus=True, prefix_icon=ft.Icons.PERSON, tooltip="Su nombre de usuario de administración")
    clave = C.campo("Contraseña", expand=True, password=True, can_reveal_password=True, prefix_icon=ft.Icons.LOCK,
                    tooltip="Su contraseña (puede mostrarla con el ojo)")
    estado = ft.Column([], spacing=6)
    b = {}

    def entrar(e=None):
        u, p = (usuario.value or "").strip(), clave.value or ""
        if not u or not p:
            estado.controls = [C.nota("Escriba usuario y contraseña.", "warn")]
            ctx.actualizar()
            return
        b["x"].disabled = True
        estado.controls = [C.cargando("Verificando…")]
        ctx.actualizar()
        try:
            ctx.abrir_sesion(u, p)
        except auth.ErrorAutenticacion as ex:
            msg = str(ex) or "Usuario o contraseña incorrectos."
            estado.controls = [C.nota(msg, "bad")]
        except auth.ErrorAutorizacion:
            estado.controls = [C.nota("No se pudo iniciar sesión con esas credenciales.", "bad")]
        except Exception:
            estado.controls = [C.nota("No se pudo iniciar sesión. Intente de nuevo.", "bad")]
        else:
            clave.value = ""
            b["x"].disabled = False
            ctx.avisos.append(("ok", "Sesión iniciada"))
            ctx.ir(ctx.destino_tras_login or "panel")
            return
        clave.value = ""
        b["x"].disabled = False
        ctx.actualizar()

    clave.on_submit = entrar
    usuario.on_submit = entrar
    b["x"] = C.boton("Iniciar sesión", entrar, ft.Icons.LOGIN, tooltip="Entrar a la consola")

    try:
        hay = auth.hay_administradores()
    except Exception:
        hay = True
    if hay:
        cuerpo = ft.Column([ft.Row([usuario]), ft.Row([clave]), b["x"], estado], spacing=12)
    else:
        cuerpo = ft.Column([C.nota("Todavía no existe ninguna cuenta de administración. Por seguridad no se crean desde esta pantalla: "
                                   "en la computadora donde corre HIDRO360 ejecute  python -m scripts.crear_admin  (pide la contraseña sin mostrarla) "
                                   "y vuelva a abrir esta página.", "warn")], spacing=12)

    tarjeta = ft.Container(C.panel(ft.Column([ft.Row([C.logo(46), ft.Column([ft.Text("Administración HIDRO360", size=20, weight=ft.FontWeight.W_800, color=T.NAVY),
                                                                             ft.Text("Solo personal autorizado", size=12, color=T.MUTED)], spacing=0)], spacing=12),
                                              cuerpo,
                                              C.boton("Volver al portal público", lambda e: ctx.ir("inicio"), ft.Icons.ARROW_BACK, "secundario")], spacing=16)),
                           width=440)
    return ft.Column([ft.Container(tarjeta, alignment=ft.Alignment.TOP_CENTER, padding=ft.Padding.symmetric(vertical=24))],
                     scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
