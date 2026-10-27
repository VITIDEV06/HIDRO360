"""Contexto compartido por las páginas: navegación, sesión, avisos y diálogos.

- Las páginas llaman a services.workflow SOLO a través de `ejecutar()`, que (1) ejecuta la operación dentro de
  `auth.como(actor)` (el responsable es el usuario autenticado, nunca un texto editable), (2) traduce los errores de
  negocio / autenticación / autorización en avisos comprensibles y (3) devuelve al login si la sesión expiró.
- El token de sesión vive solo en memoria de este objeto (no se escribe en disco ni en registros).
- Ocultar botones no es seguridad: la autorización real se comprueba en el backend (services.auth)."""

import asyncio
import traceback
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, Tuple

import flet as ft

import config
from interface.flet_app import theme as T
from services import auth
from services.workflow import ErrorDeNegocio

RESPONSABLE_PUBLICO = "ciudadano (portal)"


class Ctx:
    def __init__(self, page: Optional[ft.Page] = None):
        self.page = page
        self.pagina_actual = "inicio"
        self.seleccion: dict = {}
        self.clave_envio = uuid.uuid4().hex     # idempotencia: un doble clic repite la clave y no duplica
        self.envio_hecho = False                # el portal acaba de registrar un reporte (rota la clave al reabrir el formulario)
        self.destino_tras_login = "panel"
        self._mostrar: Optional[Callable[[str], None]] = None
        self.avisos: list = []                  # historial de avisos (útil en pruebas)
        self._token: Optional[str] = None       # solo en memoria
        self._servicios: dict = {}              # servicios de Flet (selector de archivos, portapapeles): uno por sesión de pantalla
        self._auto: Optional[object] = None     # marca del auto-refresco vigente

    # ------------------------------------------------------------------- sesión
    @property
    def actor(self) -> Optional[auth.Actor]:
        a = auth.actor_de_token(self._token)
        if a is None:
            self._token = None
        return a

    @property
    def es_admin(self) -> bool:
        a = self.actor
        return bool(a and a.rol == auth.Rol.ADMIN)

    @property
    def responsable(self) -> str:
        """Quién actúa: el usuario autenticado (no editable). En el portal público, un identificador genérico."""
        a = self.actor
        return a.usuario if a else RESPONSABLE_PUBLICO

    def abrir_sesion(self, usuario: str, clave: str) -> auth.Actor:
        """Inicia sesión y exige rol administrador. Lanza ErrorAutenticacion / ErrorAutorizacion."""
        token = auth.iniciar_sesion(usuario, clave)
        a = auth.actor_de_token(token)
        if a is None or a.rol != auth.Rol.ADMIN:
            auth.cerrar_sesion(token)
            raise auth.ErrorAutorizacion("Esta cuenta no tiene acceso a la consola de administración.")
        self._token = token
        return a

    def cerrar_sesion(self) -> None:
        auth.cerrar_sesion(self._token)
        self._token = None
        self.seleccion.clear()

    @contextmanager
    def con_actor(self) -> Iterator[Optional[auth.Actor]]:
        """Contexto con el actor de la sesión (para construir páginas administrativas)."""
        with auth.como(self.actor) as a:
            yield a

    # ---------------------------------------------------------------- navegación
    def ir(self, pagina: str, **seleccion) -> None:
        self.seleccion.update(seleccion)
        if self._mostrar:
            self._mostrar(pagina)
        else:
            self.pagina_actual = pagina

    def refrescar(self) -> None:
        self.ir(self.pagina_actual)

    def actualizar(self) -> None:
        if self.page:
            self.page.update()

    # -------------------------------------------------------------------- avisos
    def toast(self, mensaje: str, tipo: str = "ok") -> None:
        self.avisos.append((tipo, mensaje))
        if not self.page:
            return
        fondo = {"ok": T.GREEN, "warn": T.AMBER, "bad": T.RED}.get(tipo, T.NAVY)
        self.page.show_dialog(ft.SnackBar(ft.Text(mensaje, color=T.WHITE), bgcolor=fondo, duration=4500,
                                          behavior=ft.SnackBarBehavior.FLOATING))

    def _sesion_expirada(self, mensaje: str) -> None:
        self._token = None
        self.toast(mensaje, "bad")
        self.destino_tras_login = self.pagina_actual if self.pagina_actual not in ("login", "inicio") else "panel"
        self.ir("login")

    def ejecutar(self, fn: Callable[[], Any], ok_msg: Optional[str] = None, publico: bool = False,
                 bloquear: Optional[ft.Control] = None) -> Tuple[bool, Any]:
        """Ejecuta una operación de negocio. Devuelve (ok, valor).
        bloquear: botón que se deshabilita mientras dura la operación (evita dobles envíos) y se rehabilita al terminar.
        publico=True: operación del portal ciudadano (sin actor). Si no, se ejecuta como el administrador de la sesión;
        sin sesión válida se rechaza y se vuelve al login. Errores esperados → aviso rojo claro."""
        if bloquear is not None:
            bloquear.disabled = True
            self.actualizar()
            try:
                return self.ejecutar(fn, ok_msg, publico)
            finally:
                bloquear.disabled = False
                self.actualizar()
        actor = None if publico else self.actor
        if not publico and actor is None:
            self._sesion_expirada("Su sesión terminó. Inicie sesión de nuevo.")
            return False, None
        try:
            with auth.como(actor):
                valor = fn()
        except auth.ErrorAutenticacion as e:
            if publico:
                self.toast(str(e), "bad")
            else:
                self._sesion_expirada(str(e) or "Su sesión terminó. Inicie sesión de nuevo.")
            return False, None
        except auth.ErrorAutorizacion as e:
            self.toast(str(e) or "No tiene permisos para esta operación.", "bad")
            return False, None
        except ErrorDeNegocio as e:
            self.toast(str(e), "bad")
            return False, None
        except Exception as e:  # error inesperado: se informa sin ocultarlo y sin tumbar la interfaz
            self.toast(f"Error inesperado: {type(e).__name__}: {e}" if actor else "Ocurrió un problema. Intente de nuevo en unos minutos.", "bad")
            traceback.print_exc()
            return False, None
        if ok_msg:
            self.toast(ok_msg, "ok")
        return True, valor

    # ---------------------------------------------------------------- servicios
    def servicio(self, clave: str, fabrica: Callable[[], Any]) -> Any:
        """Servicio de Flet reutilizable (FilePicker, Clipboard). Sin Page (pruebas) devuelve None."""
        if self.page is None:
            return None
        s = self._servicios.get(clave)
        if s is None:
            s = self._servicios[clave] = fabrica()
            self.page.services.append(s)
        return s

    def auto_refrescar(self, pagina: str, segundos: float, accion: Callable[[], None]) -> None:
        """Repite `accion` cada `segundos` mientras la pantalla `pagina` siga abierta (una sola tarea vigente por contexto).
        `accion` debe refrescar solo listas (no reconstruir formularios) para no pisar lo que la persona escribe."""
        if self.page is None:
            return
        marca = object()
        self._auto = marca

        async def ciclo():
            while self._auto is marca and self.pagina_actual == pagina:
                await asyncio.sleep(segundos)
                if self._auto is not marca or self.pagina_actual != pagina:
                    break
                try:
                    accion()
                except Exception:
                    traceback.print_exc()
        self.page.run_task(ciclo)

    # ------------------------------------------------------------------ diálogos
    def dialogo(self, titulo: str, contenido: ft.Control, acciones=None) -> None:
        if not self.page:
            return
        acciones = acciones or [ft.TextButton("Cerrar", on_click=lambda e: self.page.pop_dialog())]
        self.page.show_dialog(ft.AlertDialog(title=ft.Text(titulo, color=T.NAVY, weight=ft.FontWeight.W_700),
                                             content=contenido, actions=acciones, modal=False))

    def confirmar(self, titulo: str, mensaje: str, on_si: Callable[[], None], peligro: bool = False) -> None:
        """Diálogo de confirmación para acciones sensibles o destructivas."""
        if not self.page:
            on_si()
            return

        def si(e):
            self.page.pop_dialog()
            on_si()

        self.dialogo(titulo, ft.Text(mensaje, size=14, color=T.INK), [
            ft.TextButton("Cancelar", on_click=lambda e: self.page.pop_dialog()),
            ft.Button("Confirmar", on_click=si, style=ft.ButtonStyle(bgcolor=T.RED if peligro else T.BLUE, color=T.WHITE))])

    def pedir_motivo(self, titulo: str, etiqueta: str, on_ok: Callable[[str], bool], accion: str = "Confirmar") -> None:
        """Diálogo con un motivo obligatorio (rechazos, cancelaciones). Valida en el cliente que no esté vacío; `on_ok(motivo)`
        ejecuta la operación y devuelve True si salió bien (entonces se cierra el diálogo). Si el backend rechaza, el diálogo sigue abierto."""
        motivo = ft.TextField(label=etiqueta, autofocus=True, multiline=True, min_lines=2, max_lines=4, border_radius=10,
                              border_color="#cfdcec", focused_border_color=T.BLUE)

        def hecho(e):
            if not (motivo.value or "").strip():
                motivo.error_text = "Escriba el motivo: queda registrado en la auditoría."
                self.actualizar()
                return
            motivo.error_text = None
            if on_ok(motivo.value.strip()) and self.page:
                self.page.pop_dialog()

        self.dialogo(titulo, ft.Container(motivo, width=420), [
            ft.TextButton("Cancelar", on_click=lambda e: self.page.pop_dialog() if self.page else None),
            ft.Button(accion, on_click=hecho, style=ft.ButtonStyle(bgcolor=T.RED, color=T.WHITE))])

    @property
    def proveedor_ia(self) -> str:
        return config.ai_provider()
