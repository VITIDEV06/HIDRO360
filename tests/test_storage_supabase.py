"""
Almacenamiento de fotos en Supabase Storage (HIDRO360_STORAGE=supabase).

ETIQUETA: SIMULADO. El Storage lo imita un httpx.MockTransport; NO se ha probado contra un proyecto Supabase real.
Verifica el protocolo (rutas, cabeceras, bucket), el manejo de errores 4xx/5xx/red, la limpieza ante fallos y que la
clave de servicio no aparezca nunca en mensajes, respuestas, auditoría ni trazas.
"""

import base64
import hashlib
import re

import httpx
import pytest
from fastapi.testclient import TestClient

import config
from api.main import app
from data import database as db
from services import audit, auth, fotos, tracing, workflow
from services.auth import ErrorAutenticacion
from services.workflow import ErrorDeNegocio

URL = "https://proyecto-prueba.supabase.co"
BUCKET = "reportes-fotos"
CLAVE = "sb_secret_SOLO_PRUEBA_no_es_una_clave_real_0123456789abcdef"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x11" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 40
ADMIN = {"X-API-Token": "op-test"}
RE_OBJ = re.compile(rf"^/storage/v1/object/{BUCKET}/([0-9a-f]{{32}})\.(jpg|png|webp)$")


class Storage:
    """Storage simulado: objetos en memoria; permite forzar fallos por método y contar llamadas."""

    def __init__(self):
        self.objetos = {}
        self.llamadas = []
        self.fallos = {}            # (metodo, n-ésima llamada de ese método) -> código HTTP, o Exception
        self.contadores = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        m = request.method
        self.contadores[m] = self.contadores.get(m, 0) + 1
        self.llamadas.append((m, request.url.path, dict(request.headers)))
        assert request.url.host == "proyecto-prueba.supabase.co"
        assert request.headers["authorization"] == f"Bearer {CLAVE}" and request.headers["apikey"] == CLAVE
        f = self.fallos.get((m, self.contadores[m])) or self.fallos.get((m, "*"))
        if isinstance(f, Exception):
            raise f
        if f:
            return httpx.Response(f, json={"message": f"fallo simulado con {CLAVE}"})   # el cuerpo NO debe propagarse
        ruta = request.url.path
        assert RE_OBJ.match(ruta), ruta
        if m == "POST":
            assert request.headers["x-upsert"] == "false" and request.headers["content-type"].startswith("image/")
            if ruta in self.objetos:
                return httpx.Response(409, json={"message": "duplicado"})
            self.objetos[ruta] = request.content
            return httpx.Response(200, json={"Key": ruta})
        if m == "GET":
            return httpx.Response(200, content=self.objetos[ruta]) if ruta in self.objetos else httpx.Response(404)
        if m == "DELETE":
            self.objetos.pop(ruta, None)
            return httpx.Response(200, json={"message": "Successfully deleted"})
        return httpx.Response(405)


@pytest.fixture
def st(monkeypatch, tmp_path):
    monkeypatch.setenv("HIDRO360_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", CLAVE)
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", BUCKET)
    monkeypatch.setenv("HIDRO360_API_TOKEN", "op-test")
    srv = Storage()
    monkeypatch.setattr(fotos, "_transport", httpx.MockTransport(srv))
    return srv


def _foto(datos=JPEG):
    return {"nombre": "../../x.jpg", "base64": base64.b64encode(datos).decode()}


def _todo_el_texto_generado(extra=""):
    """Auditoría + trazas + metadatos de fotos: ninguna debe contener la clave de servicio."""
    partes = [extra, str([e.model_dump() for e in audit.listar()]), str(db.listar("fotos"))]
    if config.TRACE_PATH.exists():
        partes.append(config.TRACE_PATH.read_text(encoding="utf-8"))
    return "\n".join(partes)


# ----------------------------------------------------------------------------- subida y lectura
def test_subida_lectura_y_borrado_contra_storage_simulado(st):
    r = workflow.crear_reporte("Sin agua en Chame, con fotos", fotos=[_foto(JPEG), _foto(PNG)])
    assert len(r.foto_ids) == 2
    assert not config.MEDIA_DIR.exists() or not list(config.MEDIA_DIR.iterdir())     # nada en disco local
    rutas = sorted(st.objetos)
    assert len(rutas) == 2 and all(RE_OBJ.match(p) for p in rutas)
    assert {p.split("/")[-1].split(".")[0] for p in rutas} == set(r.foto_ids)         # nombre = id aleatorio de 128 bits
    assert st.objetos[f"/storage/v1/object/{BUCKET}/{r.foto_ids[0]}.jpg"] == JPEG
    assert [c[0] for c in st.llamadas] == ["POST", "POST"]
    # metadatos coherentes con la tabla `fotos`
    meta = fotos.metadatos(r.foto_ids[0])
    assert meta.tipo_mime == "image/jpeg" and meta.bytes == len(JPEG) and meta.reporte_id == r.id
    assert meta.sha256 == hashlib.sha256(JPEG).hexdigest()
    # lectura solo por el backend con rol admin
    assert workflow.obtener_foto(r.id, r.foto_ids[0]) == (JPEG, "image/jpeg")
    with auth.como(None):
        with pytest.raises(ErrorAutenticacion):
            workflow.obtener_foto(r.id, r.foto_ids[0])
    assert CLAVE not in _todo_el_texto_generado()


def test_nombres_de_objeto_no_predecibles(st):
    a = workflow.crear_reporte("Sin agua en Chame uno", fotos=[_foto()])
    b = workflow.crear_reporte("Sin agua en Capira dos distinto", fotos=[_foto()])
    assert a.foto_ids[0] != b.foto_ids[0] and re.fullmatch(r"[0-9a-f]{32}", a.foto_ids[0])


def test_la_api_sirve_la_foto_solo_a_administracion_sin_url_publica(st):
    c = TestClient(app)
    r = c.post("/api/v1/publico/reportes", json={"texto": "Sin agua en Chame con foto", "fotos": [_foto()]})
    assert r.status_code == 201
    rep = next(x for x in c.get("/api/v1/reportes", headers=ADMIN).json()
               if x["codigo_seguimiento"] == r.json()["codigo_seguimiento"])
    ruta = f"/api/v1/reportes/{rep['id']}/fotos/{rep['foto_ids'][0]}"
    assert c.get(ruta).status_code == 401
    g = c.get(ruta, headers=ADMIN)
    assert g.status_code == 200 and g.content == JPEG
    assert g.headers["cache-control"] == "private, no-store" and g.headers["x-content-type-options"] == "nosniff"
    for texto in (r.text, c.get("/api/v1/reportes", headers=ADMIN).text, str(g.headers)):
        assert CLAVE not in texto and "supabase.co" not in texto and "storage/v1" not in texto


def test_borrado_controlado_quita_objeto_metadatos_y_referencia(st):
    r = workflow.crear_reporte("Sin agua en Chame borrar", fotos=[_foto(JPEG), _foto(PNG)])
    fid = r.foto_ids[0]
    with pytest.raises(ErrorDeNegocio):
        workflow.eliminar_foto(r.id, fid, " ")                                       # motivo obligatorio
    r2 = workflow.eliminar_foto(r.id, fid, "contiene una cara")
    assert fid not in r2.foto_ids and len(st.objetos) == 1 and fotos.metadatos(fid) is None
    e = audit.listar("reporte", r.id)[-1]
    assert e.accion == "eliminar_foto" and e.motivo == "contiene una cara"
    with pytest.raises(ErrorDeNegocio):
        workflow.eliminar_foto(r.id, fid, "otra vez")
    # por API
    c = TestClient(app)
    ok = c.post(f"/api/v1/reportes/{r.id}/fotos/{r.foto_ids[1]}/eliminar", json={"motivo": "datos personales"}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["foto_ids"] == [] and st.objetos == {}
    assert c.post(f"/api/v1/reportes/{r.id}/fotos/{fid}/eliminar", json={"motivo": "x"}).status_code == 401


def test_si_el_storage_no_confirma_el_borrado_no_cambia_nada(st):
    r = workflow.crear_reporte("Sin agua en Chame borrado fallido", fotos=[_foto()])
    st.fallos[("DELETE", "*")] = 500
    with pytest.raises(ErrorDeNegocio, match="No se pudo borrar"):
        workflow.eliminar_foto(r.id, r.foto_ids[0], "prueba")
    assert workflow.obtener_reporte(r.id).foto_ids == r.foto_ids and fotos.metadatos(r.foto_ids[0]) is not None
    assert CLAVE not in _todo_el_texto_generado()


# ----------------------------------------------------------------------------- errores
@pytest.mark.parametrize("codigo", [400, 401, 403, 409, 413, 429, 500, 502, 503])
def test_fallo_de_subida_no_deja_el_reporte_a_medias_ni_filtra_la_clave(st, codigo):
    antes = len(workflow.listar_reportes())
    st.fallos[("POST", "*")] = codigo
    with pytest.raises(ErrorDeNegocio) as e:
        workflow.crear_reporte("Sin agua en Chame fallo de subida", fotos=[_foto()])
    assert CLAVE not in str(e.value) and "supabase" not in str(e.value).lower() and str(codigo) in str(e.value)
    assert len(workflow.listar_reportes()) == antes and db.listar("fotos") == []
    assert CLAVE not in _todo_el_texto_generado(str(e.value))


def test_error_de_red_se_traduce_a_mensaje_generico(st):
    st.fallos[("POST", "*")] = httpx.ConnectError("no hay ruta a proyecto-prueba.supabase.co con " + CLAVE)
    with pytest.raises(ErrorDeNegocio) as e:
        workflow.crear_reporte("Sin agua en Chame sin red", fotos=[_foto()])
    assert CLAVE not in str(e.value) and "supabase" not in str(e.value).lower()
    st.fallos.clear()
    st.fallos[("POST", "*")] = httpx.ReadTimeout("tiempo agotado")
    with pytest.raises(ErrorDeNegocio, match="no respondió"):
        workflow.crear_reporte("Sin agua en Chame timeout", fotos=[_foto()])


def test_si_falla_la_segunda_foto_se_limpia_la_primera(st):
    st.fallos[("POST", 2)] = 500
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_reporte("Sin agua en Chame dos fotos", fotos=[_foto(JPEG), _foto(PNG)])
    assert st.objetos == {}                                   # la primera subida se borró del bucket
    assert [c[0] for c in st.llamadas] == ["POST", "POST", "DELETE"]
    assert db.listar("fotos") == []


def test_si_la_transaccion_falla_despues_de_subir_se_borran_los_objetos(st, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("fallo de auditoría")
    monkeypatch.setattr(audit, "registrar", boom)
    with pytest.raises(RuntimeError):
        workflow.crear_reporte("Sin agua en Chame auditoría rota", fotos=[_foto(), _foto(PNG)])
    assert st.objetos == {}


def test_lectura_con_errores_o_contenido_alterado(st):
    r = workflow.crear_reporte("Sin agua en Chame lectura", fotos=[_foto()])
    fid = r.foto_ids[0]
    st.fallos[("GET", "*")] = 500
    with pytest.raises(ErrorDeNegocio, match="no existe o su archivo"):
        workflow.obtener_foto(r.id, fid)
    st.fallos.clear()
    st.fallos[("GET", "*")] = 404
    assert fotos.leer(fid) is None
    st.fallos.clear()
    ruta = f"/storage/v1/object/{BUCKET}/{fid}.jpg"
    st.objetos[ruta] = JPEG + b"manipulado"                 # el objeto cambió: el SHA-256 guardado lo detecta
    assert fotos.leer(fid) is None
    st.fallos[("GET", "*")] = httpx.ConnectError("sin red")
    assert fotos.leer(fid) is None


def test_configuracion_incompleta_o_insegura_no_sube_nada(st, monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    with pytest.raises(ErrorDeNegocio, match="no está configurado"):
        workflow.crear_reporte("Sin agua en Chame sin clave", fotos=[_foto()])
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", CLAVE)
    monkeypatch.setenv("SUPABASE_URL", "http://servidor-externo.example")      # HTTP en claro hacia fuera: no
    with pytest.raises(ErrorDeNegocio, match="no está configurado"):
        workflow.crear_reporte("Sin agua en Chame http", fotos=[_foto()])
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "../otro")
    with pytest.raises(ErrorDeNegocio, match="no está configurado"):
        workflow.crear_reporte("Sin agua en Chame bucket raro", fotos=[_foto()])
    assert st.llamadas == []


def test_la_validacion_ocurre_antes_de_subir(st):
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_reporte("Sin agua en Chame no es imagen", fotos=[{"nombre": "a.jpg", "base64":
                                                                         base64.b64encode(b"%PDF-1.4 hola").decode()}])
    with pytest.raises(ErrorDeNegocio):
        workflow.crear_reporte("Sin agua en Chame enorme", fotos=[{"nombre": "a.jpg", "base64": "A" * 5_000_000}])
    assert st.llamadas == []


def test_el_texto_depurado_de_trazas_enmascara_cadenas_de_conexion_y_claves(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "secreto-literal-0123456789")   # (DATABASE_URL real activaría Postgres)
    tracing.emitir("prueba", "error", "error", {"url": "postgresql://postgres.abc:SuperClave123@aws-0.pooler.supabase.com:6543/postgres"},
                   {"error": "connection to postgresql://u:SuperClave123@host failed; Bearer abcdefghijklmnop; secreto-literal-0123456789 "
                             + CLAVE + " eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiIxIn0.firmaFirmaFirma"})
    texto = config.TRACE_PATH.read_text(encoding="utf-8")
    for secreto in ("SuperClave123", CLAVE, "abcdefghijklmnop", "eyJhbGci", "secreto-literal-0123456789"):
        assert secreto not in texto
