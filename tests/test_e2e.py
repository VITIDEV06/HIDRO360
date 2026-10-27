"""Extremo a extremo contra un servidor uvicorn REAL (proceso aparte): ciudadano -> administrador -> IA ->
carro cisterna -> reinicio del servidor y persistencia. Base y fotos temporales. IA en modo local (sin red)."""

import base64
import os
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import httpx
import pytest

import config
from services import auth, workflow

ROOT = Path(__file__).resolve().parent.parent
CLAVE = "Clave-E2E-de-prueba-1"


def _png() -> bytes:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _puerto():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servidor:
    def __init__(self, puerto):
        self.puerto, self.p = puerto, None
        self.url = f"http://127.0.0.1:{puerto}/api/v1"

    def iniciar(self):
        env = {**os.environ, "HIDRO360_DB": str(config.DB_PATH), "HIDRO360_MEDIA_DIR": str(config.MEDIA_DIR),
               "HIDRO360_TRACE": str(config.TRACE_PATH), "AI_PROVIDER": "local"}
        for v in ("GEMINI_API_KEY", "HIDRO360_API_TOKEN", "HIDRO360_DEVICE_TOKEN",
                  "AGUA_CLARA_API_TOKEN", "AGUA_CLARA_DEVICE_TOKEN", "AGUA_CLARA_DB"):
            env.pop(v, None)
        self.p = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(self.puerto),
                                   "--log-level", "warning"], cwd=ROOT, env=env)
        for _ in range(80):
            try:
                if httpx.get(f"{self.url}/salud", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.25)
        raise RuntimeError("el servidor no arrancó")

    def detener(self):
        if self.p:
            self.p.terminate()
            self.p.wait(10)
            self.p = None


def test_flujo_completo_con_reinicio():
    # Preparación (fuera del servidor): cuenta de administración y datos de ejemplo claramente ficticios.
    auth.crear_usuario("admin_e2e", CLAVE, auth.Rol.ADMIN)
    with auth.como(auth.Actor("preparacion", auth.Rol.ADMIN, "servicio")):
        workflow.cargar_datos_demo(responsable="preparacion")
    srv = Servidor(_puerto())
    srv.iniciar()
    try:
        # 1. Ciudadano: reporte con foto y coordenadas reales, sin cuenta.
        png = _png()
        r = httpx.post(f"{srv.url}/publico/reportes", json={
            "texto": "En La Chorrera llevamos 2 días sin agua, somos unas 40 personas, hay niños pequeños.",
            "lat": 8.88, "lng": -79.78,
            "fotos": [{"nombre": "../../evil.png", "base64": base64.b64encode(png).decode()}]})
        assert r.status_code == 201, r.text
        codigo = r.json()["codigo_seguimiento"]
        pub = httpx.get(f"{srv.url}/publico/reportes/{codigo}").json()
        assert pub["tiene_ubicacion"] is True and pub["n_fotos"] == 1
        assert "lat" not in pub and "foto_ids" not in pub
        # La foto falsa (extensión .png, contenido de texto) se rechaza.
        mala = httpx.post(f"{srv.url}/publico/reportes", json={
            "texto": "sin agua en Chame", "fotos": [{"nombre": "x.png", "base64": base64.b64encode(b"no soy imagen").decode()}]})
        assert mala.status_code in (400, 422)
        # Sin credenciales, las rutas de administración responden 401.
        assert httpx.get(f"{srv.url}/reportes").status_code == 401

        # 2. Administración: login, detalle, foto, mapa, confirmar.
        tok = httpx.post(f"{srv.url}/auth/login", json={"usuario": "admin_e2e", "clave": CLAVE}).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        reportes = httpx.get(f"{srv.url}/reportes", headers=H).json()
        rep = next(x for x in reportes if x.get("codigo_seguimiento") == codigo)
        assert (rep["lat"], rep["lng"]) == (8.88, -79.78) and len(rep["foto_ids"]) == 1
        f = httpx.get(f"{srv.url}/reportes/{rep['id']}/fotos/{rep['foto_ids'][0]}", headers=H)
        assert f.status_code == 200 and f.content == png and f.headers["content-type"] == "image/png"
        mapa = httpx.get(f"{srv.url}/mapa", headers=H).json()
        assert any(abs(m["lat"] - 8.88) < 1e-9 for m in mapa["reportes"])
        c = httpx.post(f"{srv.url}/reportes/{rep['id']}/confirmar", headers=H,
                       json={"sector": "La Chorrera", "horas_sin_agua": 48, "personas_afectadas": 40})
        assert c.status_code == 200, c.text
        assert c.json()["estado"] == "confirmado"

        # 3. IA (modo local): etapas, procedencia honesta, aprobación humana -> misión.
        a = httpx.post(f"{srv.url}/reportes/{rep['id']}/analisis", headers=H, json={})
        assert a.status_code == 201, a.text
        rec = a.json()
        assert [e["nombre"] for e in rec["etapas"]] == ["validacion", "duplicados", "clasificacion",
                                                       "prioridad", "recomendacion", "asignacion"]
        assert rec["proveedor"] == "reglas" and rec["estado"] == "propuesta" and rec["mision_id"] is None
        assert rec["cisterna_sugerida"], rec
        d = httpx.post(f"{srv.url}/recomendaciones/{rec['id']}/decision", headers=H,
                       json={"accion": "aprobar", "motivo": "Revisado en la prueba E2E"})
        assert d.status_code == 200, d.text
        mision_id = d.json()["mision_id"]
        assert mision_id
        cis = rec["cisterna_sugerida"]

        # 4. Carro cisterna: identidad propia, misión propia, evento idempotente visible en el panel.
        reg = httpx.post(f"{srv.url}/dispositivos", headers=H, json={"cisterna_id": cis, "nombre": "Camión E2E"})
        assert reg.status_code == 201, reg.text
        T = {"X-Device-Token": reg.json()["token"]}
        m = httpx.get(f"{srv.url}/dispositivo/mision", headers=T).json()
        assert m["cisterna_id"] == cis and m["mision"]["id"] == mision_id
        assert httpx.get(f"{srv.url}/dispositivo/mision", headers={"X-Device-Token": "x" * 40}).status_code == 401
        p1 = m["mision"]["paradas"][0]
        lote = {"eventos": [
            {"uid": "e2e-llegada-0001", "tipo": "llegada", "mision_id": mision_id, "parada_orden": p1["orden"]},
            {"uid": "e2e-entrega-0001", "tipo": "entrega", "mision_id": mision_id, "parada_orden": p1["orden"],
             "litros_entregados": p1["litros"]}]}
        r1 = httpx.post(f"{srv.url}/dispositivo/eventos", headers=T, json=lote).json()["resultados"]
        assert [x["resultado"] for x in r1] == ["aplicado", "aplicado"]
        r2 = httpx.post(f"{srv.url}/dispositivo/eventos", headers=T, json=lote).json()["resultados"]
        assert [x["resultado"] for x in r2] == ["duplicado", "duplicado"]
        ev = httpx.get(f"{srv.url}/eventos", headers=H).json()
        assert {e["uid"] for e in ev} >= {"e2e-llegada-0001", "e2e-entrega-0001"}
        assert sum(1 for e in ev if e["uid"] == "e2e-entrega-0001" and e["resultado"] == "aplicado") == 1

        # 5. Persistencia: reiniciar el servidor y comprobar todo.
        srv.detener()
        srv.iniciar()
        tok2 = httpx.post(f"{srv.url}/auth/login", json={"usuario": "admin_e2e", "clave": CLAVE}).json()["token"]
        H2 = {"Authorization": f"Bearer {tok2}"}
        assert httpx.get(f"{srv.url}/publico/reportes/{codigo}").json()["n_fotos"] == 1
        assert httpx.get(f"{srv.url}/reportes/{rep['id']}/fotos/{rep['foto_ids'][0]}", headers=H2).content == png
        assert httpx.get(f"{srv.url}/misiones/{mision_id}", headers=H2).status_code == 200
        ev2 = httpx.get(f"{srv.url}/eventos", headers=H2).json()
        assert any(e["uid"] == "e2e-entrega-0001" for e in ev2)
        mis = httpx.get(f"{srv.url}/misiones/{mision_id}", headers=H2).json()
        assert mis["paradas"][0]["estado"] == "entregada"
        assert mis["estado"] == ("completada" if len(mis["paradas"]) == 1 else "en_curso")
        # Misión terminada: la terminal ya no recibe misión activa (el firmware lo muestra como completada).
        if mis["estado"] == "completada":
            assert httpx.get(f"{srv.url}/dispositivo/mision", headers=T).json()["mision"] is None
        assert len(httpx.get(f"{srv.url}/recomendaciones", headers=H2).json()) == 1
    finally:
        srv.detener()
