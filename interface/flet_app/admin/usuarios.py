"""Usuarios de la consola (solo administración): lista con roles y altas/bajas/cambios de rol.

La página usa SOLO funciones del servidor (services.workflow): crear_usuario_admin, listar_usuarios_admin,
desactivar_usuario, reactivar_usuario y cambiar_rol_usuario. Si alguna aún no existe, la acción correspondiente se oculta y se avisa con claridad;
la página nunca se rompe ni inventa filas. Las reglas (último administrador, contraseñas, roles válidos) son del backend:
sus errores se muestran tal cual. El acceso de esta interfaz usa estas cuentas locales (no Supabase)."""

from typing import Any

import flet as ft

import config
from interface.flet_app import backend
from interface.flet_app import components as C
from interface.flet_app import theme as T
from services import auth

ROLES = [(r.value, r.value.capitalize()) for r in auth.Rol]
ACCIONES = ("crear_usuario_admin", "listar_usuarios_admin", "desactivar_usuario", "reactivar_usuario", "cambiar_rol_usuario")


def _v(o: Any, k: str, d=None):
    return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)


def disponibles() -> dict:
    """Qué funciones del servidor existen hoy (la página se adapta; no depende de que todas estén)."""
    return {n: backend.existe(n) for n in ACCIONES}


def build(ctx) -> ft.Control:
    cabecera = C.titulo("Usuarios", "Cuentas de la consola de administración y sus roles")
    nota_sesion = C.nota("Las sesiones viven en la memoria de este programa: se pierden al reiniciarlo. El acceso de esta pantalla usa las cuentas locales "
                         "(no inicia sesión con Supabase). Las contraseñas nunca se muestran.", "info")
    d = disponibles()
    if not d["listar_usuarios_admin"]:
        return C.seccion(cabecera, nota_sesion, C.nota(
            "La gestión de usuarios desde la interfaz todavía no está disponible en esta versión del servidor. Mientras tanto, cree cuentas con  "
            "python -m scripts.crear_admin  en la computadora donde corre HIDRO360.", "warn"))

    cuerpo = ft.Column([], spacing=10)
    yo = ctx.responsable

    def pintar(e=None):
        with ctx.con_actor():
            usuarios, error = backend.leer("listar_usuarios_admin", [])
        usuarios = list(usuarios or [])
        if error:
            cuerpo.controls = [C.nota(f"No se pudo leer la lista de usuarios: {error}", "bad")]
        else:
            filas = []
            for u in sorted(usuarios, key=lambda x: str(_v(x, "usuario"))):
                nombre = str(_v(u, "usuario"))
                activo = bool(_v(u, "activo", True))
                rol = str(getattr(_v(u, "rol"), "value", _v(u, "rol")))
                acc = []
                if d["cambiar_rol_usuario"]:
                    otro = next((v for v, _ in ROLES if v != rol), rol)
                    acc.append(ft.TextButton(f"Pasar a {otro}", on_click=lambda ev, n=nombre, r=otro: cambiar_rol(n, r),
                                             tooltip="Cambia el rol de la cuenta (pide confirmación)"))
                if activo and d["desactivar_usuario"]:
                    acc.append(ft.TextButton("Desactivar", on_click=lambda ev, n=nombre: desactivar(n), style=ft.ButtonStyle(color=T.RED)))
                if not activo and d["reactivar_usuario"]:
                    acc.append(ft.TextButton("Reactivar", on_click=lambda ev, n=nombre: reactivar(n)))
                filas.append([nombre + ("  (usted)" if nombre == yo else ""), C.pill(rol, "info" if rol == auth.Rol.ADMIN.value else "neutro"),
                              C.pill("activa" if activo else "desactivada", "ok" if activo else "bad"), str(_v(u, "origen") or "local"),
                              str(_v(u, "creado") or "—")[:16].replace("T", " "), ft.Row(acc, spacing=0, wrap=True)])
            cuerpo.controls = [C.tabla(["Usuario", "Rol", "Estado", "Origen", "Creada", "Acciones"], filas)] if filas \
                else [C.vacio("No hay cuentas registradas.", ft.Icons.PEOPLE)]
        if e is not None:
            ctx.actualizar()

    def accion(nombre_fn: str, titulo: str, mensaje: str, ok_msg: str, *args, peligro: bool = True):
        def hacer():
            ctx.ejecutar(lambda: backend.llamar(nombre_fn, *args), ok_msg)
            pintar()          # salió bien o el servidor lo impidió (p. ej. último administrador): se vuelve a leer la verdad
            ctx.actualizar()
        ctx.confirmar(titulo, mensaje, hacer, peligro)

    def desactivar(n: str):
        accion("desactivar_usuario", "Desactivar la cuenta",
               f"La cuenta «{n}» ya no podrá iniciar sesión. Si es el último administrador activo, el servidor lo impedirá.", f"Cuenta «{n}» desactivada.", n)

    def reactivar(n: str):
        accion("reactivar_usuario", "Reactivar la cuenta", f"La cuenta «{n}» podrá iniciar sesión de nuevo.", f"Cuenta «{n}» reactivada.", n, peligro=False)

    def cambiar_rol(n: str, rol: str):
        accion("cambiar_rol_usuario", "Cambiar el rol", f"La cuenta «{n}» pasará a ser «{rol}». Si es el último administrador, el servidor lo impedirá.",
               f"Rol de «{n}» cambiado a {rol}.", n, rol, peligro=rol != auth.Rol.ADMIN.value)

    panel_lista = C.panel(cuerpo, "Cuentas")
    pintar()

    if not d["crear_usuario_admin"]:
        return C.seccion(cabecera, nota_sesion, panel_lista,
                         C.nota("Crear cuentas desde la interfaz todavía no está disponible: use  python -m scripts.crear_admin.", "warn"))

    supabase = config.AUTH_BACKEND() == "supabase"
    f_user = C.campo("Correo de la cuenta de Supabase" if supabase else "Usuario (3-40: letras, números, punto o guion)", "", width=320)
    f_clave = C.campo("Contraseña (mín. 10, mayúsculas, minúsculas y número)", "", width=320, password=True, can_reveal_password=True, visible=not supabase)
    f_rol = C.lista_desplegable("Rol", ROLES, auth.Rol.USUARIO.value, width=200)
    msg = ft.Column([], spacing=6)
    btn = {}

    def crear(e=None):
        u, p = (f_user.value or "").strip(), f_clave.value or ""
        if not u or (not p and not supabase):
            msg.controls = [C.nota("Escriba el correo." if supabase else "Escriba el usuario y la contraseña.", "warn")]
            ctx.actualizar()
            return
        msg.controls = []

        def hacer():
            ok, _ = ctx.ejecutar(lambda: backend.llamar("crear_usuario_admin", u, f_rol.value or auth.Rol.USUARIO.value, None if supabase else p),
                                 f"Cuenta «{u}» creada.", bloquear=btn["x"])
            if ok:
                f_user.value = ""
                f_clave.value = ""
                pintar()
                ctx.actualizar()
        ctx.confirmar("Crear la cuenta", f"Se creará la cuenta «{u}» con rol «{f_rol.value}». Quedará registrado en la auditoría.", hacer, peligro=False)

    btn["x"] = C.boton("Crear cuenta", crear, ft.Icons.PERSON_ADD)
    nueva = C.panel(ft.Column([ft.Row([f_user, f_clave], spacing=12, wrap=True),
                               C.nota("Con Supabase Auth la contraseña se gestiona en Supabase: aquí solo se crea el perfil (rol y estado).", "info") if supabase else ft.Container(),
                               ft.Row([f_rol, btn["x"]], spacing=12, wrap=True), msg,
                               ft.Text("El rol «administrador» entra a esta consola; «usuario» no.", size=11.5, color=T.MUTED)], spacing=10), "Nueva cuenta")
    return C.seccion(cabecera, nota_sesion, panel_lista, nueva)
