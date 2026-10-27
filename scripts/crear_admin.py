"""
Crea (o restablece) una cuenta de HIDRO360 pidiendo la contraseña sin mostrarla. No existen cuentas predeterminadas.

    python -m scripts.crear_admin                 # administrador (cuenta local con contraseña)
    python -m scripts.crear_admin --rol usuario   # cuenta sin permisos administrativos
    python -m scripts.crear_admin --supabase-email jefa@ejemplo.com
                                                  # perfil de administrador vinculado a una cuenta de Supabase Auth

La contraseña nunca se guarda en texto plano (scrypt con sal) ni se acepta por argumento de línea de comandos
(quedaría en el historial del shell).

Con --supabase-email NO se pide contraseña: la identidad vive en Supabase (cree la cuenta en el panel de Supabase con
el registro abierto DESACTIVADO) y aquí solo se crea el perfil local que concede el rol. El perfil se vincula al `sub`
de Supabase en el primer acceso. Use HIDRO360_AUTH=supabase y DATABASE_URL del entorno de destino. Si el correo ya
tiene perfil, no se reemplaza.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import auth  # noqa: E402


def pedir_clave(texto: str) -> str:
    """Pide la contraseña. En una consola real queda OCULTA (getpass). Si el terminal no es una consola (Git Bash,
    paneles integrados, tuberías), getpass se queda esperando para siempre: ahí se avisa y se lee con input()."""
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(texto)
    print("Aviso: este terminal no permite ocultar la contraseña; se verá al escribirla. "
          "Use PowerShell normal si prefiere que quede oculta.")
    return input(texto)


def cuenta_existe(usuario: str) -> bool:
    return usuario.strip().lower() in {u["usuario"] for u in auth.listar_usuarios()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--usuario", help="nombre de usuario (si se omite, se pregunta)")
    ap.add_argument("--rol", choices=[r.value for r in auth.Rol], default=auth.Rol.ADMIN.value)
    ap.add_argument("--restablecer", action="store_true",
                    help="permite cambiar la contraseña de una cuenta que YA existe (sin esta opción no se reemplaza nada)")
    ap.add_argument("--supabase-email", help="correo de la cuenta de Supabase a la que se concede el rol")
    a = ap.parse_args()
    if a.supabase_email:
        from services import auth_supabase
        try:
            doc = auth_supabase.crear_perfil(a.supabase_email, auth.Rol(a.rol))
        except ValueError as e:
            print(f"No se creó el perfil: {e}")
            return 1
        print(f"Perfil «{doc['usuario']}» creado con rol {a.rol}. Se vinculará a su cuenta de Supabase en el primer acceso.")
        return 0
    usuario = (a.usuario or os.environ.get("HIDRO360_ADMIN_USUARIO") or input("Usuario: ")).strip()
    if cuenta_existe(usuario) and not a.restablecer:
        print(f"La cuenta «{usuario.lower()}» ya existe y no se reemplaza. Para cambiar su contraseña use --restablecer.")
        return 1
    clave = os.environ.get("HIDRO360_ADMIN_CLAVE")  # opción sin preguntas: solo para esta sesión de terminal
    if clave is None:
        clave = pedir_clave("Contraseña: ")
        if clave != pedir_clave("Repita la contraseña: "):
            print("Las contraseñas no coinciden.")
            return 1
    try:
        auth.crear_usuario(usuario, clave, auth.Rol(a.rol))
    except ValueError as e:
        print(f"No se creó la cuenta: {e}")
        return 1
    print(f"Cuenta «{usuario.lower()}» {'restablecida' if a.restablecer else 'creada'} con rol {a.rol}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
