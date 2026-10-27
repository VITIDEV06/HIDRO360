"""
Fotografías de reportes. Decisiones de seguridad:

- El tipo se decide por la FIRMA BINARIA del contenido (JPEG/PNG/WebP), nunca por la extensión ni por el
  Content-Type que declare el cliente.
- El nombre en disco es aleatorio (`secrets.token_hex`) y la extensión sale del tipo detectado: el nombre
  que envía el cliente no participa en ninguna ruta (sin path traversal) y ni siquiera se guarda.
- Almacén según config.STORAGE_BACKEND():
  * `local` (por defecto): escritura atómica (archivo temporal + os.replace) en config.MEDIA_DIR, fuera del código y de Git.
  * `supabase`: Storage REST de Supabase por httpx (sin supabase-py), bucket PRIVADO (config.SUPABASE_STORAGE_BUCKET()).
    La clave de servicio vive SOLO en el servidor: va en cabeceras de salida y jamás en respuestas, trazas ni
    mensajes de error (los errores se reducen a «no se pudo guardar/leer» con el código HTTP). Los objetos se llaman
    `<128 bits aleatorios>.<ext>`; no hay URLs públicas ni firmadas: la lectura pasa por el backend con
    autorización de administrador (workflow.obtener_foto).
- Solo la administración puede leerlas (workflow.obtener_foto); el portal público no las expone.
- Cambiar de backend NO migra fotos ya guardadas (los objetos quedan en el almacén anterior).
- Limitación: no se re-codifican ni se limpian los metadatos EXIF (pueden incluir GPS del teléfono).
"""

import base64
import binascii
import hashlib
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

import config
from data import database as db
from models.schemas import Foto

EXTENSIONES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_RE_ID = re.compile(r"[0-9a-f]{32}")
_RE_DATA_URI = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)


TIMEOUT_STORAGE_S = 20.0
# Transporte HTTP inyectable (solo pruebas, httpx.MockTransport). None en producción.
_transport: Optional[Any] = None


class ErrorFoto(ValueError):
    """Foto inválida o almacén no disponible (la capa de negocio la convierte en ErrorDeNegocio).
    Sus mensajes nunca incluyen claves, URLs internas ni cuerpos de respuesta del almacén."""


def _supabase() -> bool:
    return config.STORAGE_BACKEND() == "supabase"


def _cliente_storage() -> Tuple[httpx.Client, str, Dict[str, str]]:
    url, clave, bucket = config.SUPABASE_URL(), config.SUPABASE_SERVICE_ROLE_KEY(), config.SUPABASE_STORAGE_BUCKET()
    if not url or not clave or not bucket or not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", bucket):
        raise ErrorFoto("El almacenamiento de fotos no está configurado.")
    if not (url.startswith("https://") or re.match(r"http://(localhost|127\.0\.0\.1)(:\d+)?($|/)", url)):
        raise ErrorFoto("El almacenamiento de fotos no está configurado.")
    cli = httpx.Client(timeout=TIMEOUT_STORAGE_S, transport=_transport)
    return cli, f"{url}/storage/v1/object/{bucket}", {"Authorization": f"Bearer {clave}", "apikey": clave}


def _peticion_storage(metodo: str, nombre: str, **kw) -> httpx.Response:
    """Una petición al Storage. Cualquier fallo se reduce a un ErrorFoto genérico (sin detalles ni secretos)."""
    cli, base, cab = _cliente_storage()
    try:
        with cli:
            return cli.request(metodo, f"{base}/{nombre}", headers={**cab, **kw.pop("headers", {})}, **kw)
    except httpx.HTTPError:
        raise ErrorFoto("El almacenamiento de fotos no respondió. Inténtelo de nuevo.")


def detectar_mime(datos: bytes) -> Optional[str]:
    """Tipo según la firma binaria; None si no es JPEG, PNG ni WebP."""
    if datos[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if datos[:8] == b"\x89PNG\r\n\x1a\n" and datos[12:16] == b"IHDR":
        return "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def decodificar(entrada: Dict[str, Any]) -> bytes:
    """Obtiene los bytes de {"nombre":..,"base64":..} validando tamaño ANTES de decodificar."""
    if not isinstance(entrada, dict):
        raise ErrorFoto("Formato de foto inválido.")
    if isinstance(entrada.get("bytes"), (bytes, bytearray)):
        datos = bytes(entrada["bytes"])
    else:
        b64 = entrada.get("base64")
        if not isinstance(b64, str) or not b64:
            raise ErrorFoto("La foto no trae contenido.")
        b64 = _RE_DATA_URI.sub("", b64.strip())
        if len(b64) > (config.MAX_BYTES_FOTO * 4) // 3 + 8:
            raise ErrorFoto(f"La foto excede el máximo de {config.MAX_BYTES_FOTO // (1024 * 1024)} MB.")
        try:
            datos = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ErrorFoto("La foto no es una imagen válida (base64 inválido).")
    if not datos:
        raise ErrorFoto("La foto está vacía.")
    if len(datos) > config.MAX_BYTES_FOTO:
        raise ErrorFoto(f"La foto excede el máximo de {config.MAX_BYTES_FOTO // (1024 * 1024)} MB.")
    return datos


def validar(entradas: Optional[List[Dict[str, Any]]]) -> List[Tuple[bytes, str]]:
    """Valida todas las fotos de un reporte; devuelve [(bytes, mime)]. Nada se escribe si alguna falla."""
    entradas = entradas or []
    if len(entradas) > config.MAX_FOTOS_POR_REPORTE:
        raise ErrorFoto(f"Máximo {config.MAX_FOTOS_POR_REPORTE} fotos por reporte.")
    salida = []
    for e in entradas:
        datos = decodificar(e)
        mime = detectar_mime(datos)
        if mime is None:
            raise ErrorFoto("El archivo no es una imagen JPEG, PNG o WebP válida.")
        salida.append((datos, mime))
    return salida


def _ruta(foto_id: str, mime: str) -> Path:
    if not _RE_ID.fullmatch(foto_id) or mime not in EXTENSIONES:
        raise ErrorFoto("Identificador de foto inválido.")
    base = Path(config.MEDIA_DIR)
    ruta = base / f"{foto_id}.{EXTENSIONES[mime]}"
    if ruta.resolve().parent != base.resolve():          # defensa en profundidad
        raise ErrorFoto("Ruta de foto inválida.")
    return ruta


def _nombre_objeto(foto_id: str, mime: str) -> str:
    if not _RE_ID.fullmatch(foto_id) or mime not in EXTENSIONES:
        raise ErrorFoto("Identificador de foto inválido.")
    return f"{foto_id}.{EXTENSIONES[mime]}"


def _subir(foto_id: str, datos: bytes, mime: str) -> None:
    if _supabase():
        r = _peticion_storage("POST", _nombre_objeto(foto_id, mime), content=datos,
                              headers={"Content-Type": mime, "x-upsert": "false"})
        if r.status_code not in (200, 201):
            raise ErrorFoto(f"No se pudo guardar la foto (almacenamiento respondió {r.status_code}).")
        return
    ruta = _ruta(foto_id, mime)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def guardar(datos: bytes, mime: str, reporte_id: Optional[int]) -> Foto:
    """Sube/escribe el archivo (ya validado por firma y tamaño) y registra los metadatos (participa en la
    transacción activa). Si la subida falla lanza ErrorFoto y NO queda metadato."""
    foto_id = secrets.token_hex(16)
    _subir(foto_id, datos, mime)
    foto = Foto(id=foto_id, reporte_id=reporte_id, tipo_mime=mime, bytes=len(datos),
                sha256=hashlib.sha256(datos).hexdigest(), fecha=datetime.now())
    db.insertar("fotos", foto.model_dump(mode="json"), clave=foto_id)
    return foto


def borrar_archivo(foto_id: str, mime: str) -> bool:
    """Borra el archivo/objeto (limpieza de huérfanos si la transacción de BD se revierte, o borrado controlado).
    Nunca lanza; devuelve si quedó borrado (un objeto inexistente cuenta como borrado)."""
    try:
        if _supabase():
            r = _peticion_storage("DELETE", _nombre_objeto(foto_id, mime))
            return r.status_code in (200, 204, 404)
        _ruta(foto_id, mime).unlink(missing_ok=True)
        return True
    except (ErrorFoto, OSError):
        return False


def eliminar(foto_id: str) -> bool:
    """Borrado controlado: elimina el archivo y, solo si se borró, sus metadatos. False si no existía o no se pudo."""
    f = metadatos(foto_id)
    if f is None or not borrar_archivo(f.id, f.tipo_mime):
        return False
    db.borrar("fotos", f.id)
    return True


def metadatos(foto_id: str) -> Optional[Foto]:
    if not isinstance(foto_id, str) or not _RE_ID.fullmatch(foto_id):
        return None
    d = db.obtener("fotos", foto_id)
    return Foto(**d) if d else None


def leer(foto_id: str) -> Optional[Tuple[bytes, str]]:
    """(bytes, mime) verificando el SHA-256 guardado; None si no existe."""
    f = metadatos(foto_id)
    if f is None:
        return None
    try:
        if _supabase():
            r = _peticion_storage("GET", _nombre_objeto(f.id, f.tipo_mime))
            if r.status_code != 200:
                return None
            datos = r.content
        else:
            datos = _ruta(f.id, f.tipo_mime).read_bytes()
    except (OSError, ErrorFoto):
        return None
    if hashlib.sha256(datos).hexdigest() != f.sha256:
        return None
    return datos, f.tipo_mime
