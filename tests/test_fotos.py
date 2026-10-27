"""Fotos y coordenadas de reportes: validación por firma binaria, límites, acceso solo de administración."""

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

import config
from api.main import app
from data import database as db
from services import audit, auth, workflow
from services.auth import ErrorAutenticacion
from services.workflow import ErrorDeNegocio

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x11" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 40
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 " + b"\x00" * 20
ADMIN = {"X-API-Token": "op-test"}


@pytest.fixture(autouse=True)
def media_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setenv("AGUA_CLARA_API_TOKEN", "op-test")


def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode()


def _foto(datos=JPEG, nombre="foto.jpg"):
    return {"nombre": nombre, "base64": _b64(datos)}


def _archivos():
    d = config.MEDIA_DIR
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_reporte_con_foto_y_coordenadas_de_extremo_a_extremo():
    c = TestClient(app)
    r = c.post("/api/v1/publico/reportes", json={"texto": "Sin agua hace 2 dias en Chame, 40 personas",
                                                  "lat": 8.60, "lng": -79.88, "fotos": [_foto(JPEG), _foto(PNG, "a.png")]})
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"codigo_seguimiento", "mensaje"}
    codigo = r.json()["codigo_seguimiento"]

    # el ciudadano consulta: solo ve si hay ubicación y cuántas fotos, nunca coordenadas ni fotos
    g = c.get(f"/api/v1/publico/reportes/{codigo}")
    assert g.status_code == 200
    assert g.json()["tiene_ubicacion"] is True and g.json()["n_fotos"] == 2
    assert "8.6" not in g.text and "79.88" not in g.text and "foto_ids" not in g.text

    # el administrador ve coordenadas y fotos
    rep = next(x for x in c.get("/api/v1/reportes", headers=ADMIN).json() if x["codigo_seguimiento"] == codigo)
    assert rep["lat"] == 8.60 and rep["lng"] == -79.88 and rep["ubicacion_fuente"] == "mapa"
    assert len(rep["foto_ids"]) == 2
    f = c.get(f"/api/v1/reportes/{rep['id']}/fotos/{rep['foto_ids'][0]}", headers=ADMIN)
    assert f.status_code == 200 and f.content == JPEG
    assert f.headers["content-type"] == "image/jpeg"
    assert f.headers["x-content-type-options"] == "nosniff"
    assert f.headers["content-disposition"].startswith("inline;")
    f2 = c.get(f"/api/v1/reportes/{rep['id']}/fotos/{rep['foto_ids'][1]}", headers=ADMIN)
    assert f2.content == PNG and f2.headers["content-type"] == "image/png"

    # archivos: nombre aleatorio, sin nada del cliente, y metadatos con sha256
    assert len(_archivos()) == 2 and all(n.split(".")[0] == n.split(".")[0].lower() and len(n.split(".")[0]) == 32
                                         for n in _archivos())
    meta = db.obtener("fotos", rep["foto_ids"][0])
    assert meta["sha256"] == hashlib.sha256(JPEG).hexdigest() and meta["reporte_id"] == rep["id"]


def test_el_publico_no_puede_leer_fotos():
    c = TestClient(app)
    c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Capira", "fotos": [_foto()]})
    rep = workflow.listar_reportes()[-1]
    url = f"/api/v1/reportes/{rep.id}/fotos/{rep.foto_ids[0]}"
    assert c.get(url).status_code == 401
    assert c.get(url, headers={"X-API-Token": "otro"}).status_code == 401


def test_foto_con_extension_jpg_pero_contenido_falso_se_rechaza():
    c = TestClient(app)
    n = len(workflow.listar_reportes())
    for falso in (b"<?php system($_GET['x']); ?>", b"GIF89a" + b"\x00" * 30, b"MZ\x90\x00" + b"\x00" * 40):
        r = c.post("/api/v1/publico/reportes", json={"texto": f"Sin agua en Chame {falso[:3]!r}",
                                                      "fotos": [_foto(falso, "inocente.jpg")]})
        assert r.status_code == 422, r.text
    assert len(workflow.listar_reportes()) == n and _archivos() == []


def test_foto_sobredimensionada_o_base64_invalido_se_rechaza():
    c = TestClient(app)
    grande = JPEG + b"\x00" * config.MAX_BYTES_FOTO
    assert c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame uno", "fotos": [_foto(grande)]}).status_code == 422
    assert c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame dos",
                                                     "fotos": [{"nombre": "x", "base64": "***no-es-base64***"}]}).status_code == 422
    assert _archivos() == []


def test_maximo_de_fotos_por_reporte():
    c = TestClient(app)
    r = c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame cuatro fotos",
                                                  "fotos": [_foto()] * (config.MAX_FOTOS_POR_REPORTE + 1)})
    assert r.status_code == 422
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_reporte("Sin agua en Chame via workflow", fotos=[_foto()] * 4)
    ok = c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame tres fotos",
                                                   "fotos": [_foto(JPEG), _foto(PNG), _foto(WEBP)]})
    assert ok.status_code == 201 and len(_archivos()) == 3


@pytest.mark.parametrize("lat,lng", [(0.0, 0.0), (40.4, -3.7), (8.9, -70.0), (9.9, -79.6)])
def test_coordenadas_fuera_de_panama_oeste_se_rechazan(lat, lng):
    r = TestClient(app).post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame", "lat": lat, "lng": lng})
    assert r.status_code == 422 and "Panamá Oeste" in r.json()["detail"]


def test_coordenadas_incompletas_o_invalidas():
    c = TestClient(app)
    assert c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame a", "lat": 8.6}).status_code == 422
    assert c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame b", "lat": 999, "lng": 0}).status_code == 422
    assert c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame c", "lat": "x", "lng": 1}).status_code == 422


def test_nombre_del_cliente_no_llega_al_sistema_de_archivos():
    c = TestClient(app)
    r = c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Capira traversal",
                                                  "fotos": [_foto(JPEG, "../../../../etc/passwd.jpg")]})
    assert r.status_code == 201
    assert all(".." not in n and "passwd" not in n for n in _archivos())
    assert all(p.parent == config.MEDIA_DIR for p in config.MEDIA_DIR.iterdir())
    rep = workflow.listar_reportes()[-1]
    for malo in ("..%2F..%2Fetc%2Fpasswd", "../x", "0" * 32, "no-existe"):
        assert c.get(f"/api/v1/reportes/{rep.id}/fotos/{malo}", headers=ADMIN).status_code in (404, 422)


def test_una_foto_no_se_lee_desde_otro_reporte():
    c = TestClient(app)
    c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Capira uno", "fotos": [_foto()]})
    c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame dos"})
    a, b = workflow.listar_reportes()[-2:]
    assert c.get(f"/api/v1/reportes/{b.id}/fotos/{a.foto_ids[0]}", headers=ADMIN).status_code == 404
    assert c.get(f"/api/v1/reportes/{a.id}/fotos/{a.foto_ids[0]}", headers=ADMIN).status_code == 200


def test_persistencia_de_fotos_y_coordenadas_tras_reabrir():
    workflow.crear_reporte("Sin agua en Chame persistente", lat=8.6, lng=-79.9, fotos=[_foto(WEBP, "x.webp")])
    rep = [r for r in db.listar("reportes") if r["mensaje_original"].endswith("persistente")][0]   # conexión nueva
    assert rep["lat"] == 8.6 and len(rep["foto_ids"]) == 1
    datos, mime = workflow.obtener_foto(rep["id"], rep["foto_ids"][0])
    assert datos == WEBP and mime == "image/webp"
    # un cliente y una conexión nuevos ven lo mismo
    assert TestClient(app).get(f"/api/v1/reportes/{rep['id']}/fotos/{rep['foto_ids'][0]}", headers=ADMIN).content == WEBP


def test_reportes_antiguos_sin_campos_nuevos_se_leen():
    rid = db.insertar("reportes", {"mensaje_original": "antiguo", "fecha_creacion": "2026-01-01T10:00:00",
                                   "estado": "por_revisar", "id": 0, "codigo_seguimiento": "HID-ABCDEFGH"})
    r = workflow.obtener_reporte(rid)
    assert r.lat is None and r.foto_ids == []
    pub = workflow.consultar_reporte_publico("HID-ABCDEFGH")
    assert pub["tiene_ubicacion"] is False and pub["n_fotos"] == 0


def test_obtener_foto_exige_administrador():
    with auth.como(None):
        with pytest.raises(ErrorAutenticacion):
            workflow.obtener_foto(1, "x")


def test_si_falla_la_transaccion_no_quedan_archivos_huerfanos(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("fallo")
    monkeypatch.setattr(audit, "registrar", boom)
    n = len(workflow.listar_reportes())
    with pytest.raises(RuntimeError):
        workflow.crear_reporte("Sin agua en Chame huerfanos", fotos=[_foto(), _foto(PNG)])
    assert _archivos() == [] and len(workflow.listar_reportes()) == n
