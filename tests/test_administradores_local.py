"""Administradores en la demostración local: creación, persistencia real y seguridad básica (SQLite)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from api.main import app
from services import auth, workflow

RAIZ = Path(__file__).resolve().parent.parent
CLAVE = "Clave-Demo-Segura-91"


def _proceso(codigo: str, bd: Path, **extra) -> subprocess.CompletedProcess:
    """Ejecuta Python en un proceso NUEVO contra el mismo archivo SQLite. La contraseña va por entorno, no por argumentos."""
    entorno = {**os.environ, "HIDRO360_ENV": "test", "HIDRO360_BD": "sqlite", "HIDRO360_DB": str(bd),
               "DATABASE_URL": "", "HIDRO360_TRACE": str(bd.with_suffix(".trazas")), **extra}
    return subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=120)


def test_crear_administrador_aparece_en_el_listado():
    auth.crear_usuario("admin_demo", CLAVE, auth.Rol.ADMIN)
    cuentas = {u["usuario"]: u for u in auth.listar_usuarios()}
    assert cuentas["admin_demo"]["rol"] == "administrador"


def test_cuenta_duplicada_por_la_via_administrativa_se_rechaza():
    workflow.crear_usuario_admin("admin_demo", "administrador", CLAVE)  # conftest da un actor administrador
    with pytest.raises(workflow.ErrorDeNegocio, match="Ya existe"):
        workflow.crear_usuario_admin("admin_demo", "administrador", "Otra-Clave-Distinta-77")
    assert auth.autenticar("admin_demo", CLAVE).usuario == "admin_demo"  # sigue valiendo la clave original


def _script(monkeypatch, argumentos, clave):
    import builtins
    from scripts import crear_admin
    monkeypatch.setattr(sys, "argv", ["crear_admin", *argumentos])
    monkeypatch.delenv("HIDRO360_ADMIN_CLAVE", raising=False)
    monkeypatch.delenv("HIDRO360_ADMIN_USUARIO", raising=False)
    monkeypatch.setattr(crear_admin, "pedir_clave", lambda *a, **k: clave)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "admin_demo")
    return crear_admin.main()


def test_sin_consola_real_la_clave_se_lee_con_input_y_no_se_cuelga(monkeypatch, capsys):
    """En Git Bash o paneles integrados getpass se queda esperando para siempre: debe avisar y usar input()."""
    import builtins
    from scripts import crear_admin
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: CLAVE)
    assert crear_admin.pedir_clave("Contraseña: ") == CLAVE
    assert "no permite ocultar la contraseña" in capsys.readouterr().out


def test_script_acepta_usuario_y_clave_por_entorno(monkeypatch):
    from scripts import crear_admin
    monkeypatch.setattr(sys, "argv", ["crear_admin"])
    monkeypatch.setenv("HIDRO360_ADMIN_USUARIO", "admin_entorno")
    monkeypatch.setenv("HIDRO360_ADMIN_CLAVE", CLAVE)
    assert crear_admin.main() == 0
    assert auth.autenticar("admin_entorno", CLAVE).rol == auth.Rol.ADMIN


def test_script_no_reemplaza_una_cuenta_existente_sin_restablecer(monkeypatch):
    auth.crear_usuario("admin_demo", CLAVE, auth.Rol.ADMIN)
    assert _script(monkeypatch, ["--usuario", "admin_demo"], "Clave-Nueva-Segura-55") == 1
    assert auth.autenticar("admin_demo", CLAVE).usuario == "admin_demo"        # la clave original sigue valiendo
    with pytest.raises(auth.ErrorAutenticacion):
        auth.autenticar("admin_demo", "Clave-Nueva-Segura-55")


def test_script_con_restablecer_cambia_la_clave(monkeypatch):
    auth.crear_usuario("admin_demo", CLAVE, auth.Rol.ADMIN)
    assert _script(monkeypatch, ["--usuario", "admin_demo", "--restablecer"], "Clave-Nueva-Segura-55") == 0
    assert auth.autenticar("admin_demo", "Clave-Nueva-Segura-55").usuario == "admin_demo"


def test_script_crea_el_primer_administrador(monkeypatch):
    assert _script(monkeypatch, ["--usuario", "primer_admin"], CLAVE) == 0
    assert auth.autenticar("primer_admin", CLAVE).rol == auth.Rol.ADMIN


@pytest.mark.parametrize("usuario,clave", [
    ("", CLAVE), ("   ", CLAVE),                # usuario vacío
    ("ana", "corta1A"),                          # clave corta
    ("ana", "sinnumeroNiMayus"),                 # sin número
    ("ana", "SOLOMAYUSCULAS123"),                # sin minúsculas
])
def test_datos_invalidos_se_rechazan(usuario, clave):
    with pytest.raises(ValueError):
        auth.crear_usuario(usuario, clave, auth.Rol.ADMIN)


def test_la_clave_no_aparece_en_el_archivo_sqlite():
    auth.crear_usuario("admin_demo", CLAVE, auth.Rol.ADMIN)
    contenido = Path(config.DB_PATH).read_bytes()
    assert CLAVE.encode() not in contenido
    assert b"scrypt" in contenido  # se guarda el hash, con el algoritmo declarado


def test_administrador_persiste_tras_reiniciar_el_proceso(tmp_path):
    bd = tmp_path / "persistencia.db"
    crea = _proceso("import os; from services import auth; "
                    "auth.crear_usuario('admin_persistente', os.environ['CLAVE_PRUEBA'], auth.Rol.ADMIN); print('creado')",
                    bd, CLAVE_PRUEBA=CLAVE)
    assert crea.returncode == 0 and "creado" in crea.stdout, crea.stderr[-400:]
    # Proceso NUEVO: no comparte memoria con el anterior; solo el archivo SQLite.
    entra = _proceso("import os; from services import auth; "
                     "print(auth.autenticar('admin_persistente', os.environ['CLAVE_PRUEBA']).rol.value)",
                     bd, CLAVE_PRUEBA=CLAVE)
    assert entra.returncode == 0 and "administrador" in entra.stdout, entra.stderr[-400:]
    mala = _proceso("import os; from services import auth; "
                    "auth.autenticar('admin_persistente', 'Clave-Equivocada-00')", bd)
    assert mala.returncode != 0  # contraseña incorrecta: no entra


def test_crear_cuentas_administrativas_exige_administrador():
    with auth.como(None):
        with pytest.raises(auth.ErrorAutenticacion):
            workflow.crear_usuario_admin("intruso", "administrador", CLAVE)
    with auth.como(auth.Actor("ciudadano", auth.Rol.USUARIO, "local")):
        with pytest.raises(auth.ErrorAutorizacion):
            workflow.crear_usuario_admin("intruso", "administrador", CLAVE)
    assert "intruso" not in {u["usuario"] for u in auth.listar_usuarios()}


def test_endpoint_publico_no_puede_crear_administradores():
    c = TestClient(app)
    r = c.post("/api/v1/usuarios", json={"usuario": "intruso", "rol": "administrador", "clave": CLAVE})
    assert r.status_code in (401, 403)
    assert "intruso" not in {u["usuario"] for u in auth.listar_usuarios()}


def test_reporte_creado_aparece_en_el_listado_administrativo():
    r = workflow.crear_reporte("Sin agua desde hace dos días en la calle 4", canal="portal", origen="prueba")
    en_lista = [x for x in workflow.listar_reportes() if x.codigo_seguimiento == r.codigo_seguimiento]
    assert len(en_lista) == 1
    assert workflow.consultar_reporte_publico(r.codigo_seguimiento)["codigo"] == r.codigo_seguimiento


def test_lanzador_local_fuerza_sqlite_y_ofrece_crear_admin():
    ps = (RAIZ / "scripts" / "demo_local.ps1").read_text(encoding="utf-8")
    assert '$env:HIDRO360_BD = "sqlite"' in ps
    assert '$env:DATABASE_URL = ""' in ps
    assert "-CrearAdmin" in ps and "scripts.crear_admin" in ps


def test_configuracion_de_gemini_y_telegram_no_se_filtra(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-gemini-ficticia-no-real-12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "000000:token-ficticio-no-real")
    assert config.GEMINI_API_KEY() and config.TELEGRAM_BOT_TOKEN()
    cuerpo = TestClient(app).get("/api/v1/salud").text
    assert "clave-gemini-ficticia" not in cuerpo and "token-ficticio" not in cuerpo
    assert ".env" in (RAIZ / ".gitignore").read_text(encoding="utf-8")
