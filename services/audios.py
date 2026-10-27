"""
Notas de voz y audios de reportes (evidencia sonora). Mismo diseño de seguridad que `services/fotos.py`:

- El tipo se decide por la FIRMA BINARIA del contenido (OGG/Opus, MP3, M4A/MP4, WAV, WebM, AMR), nunca por la
  extensión ni por el Content-Type que declare el cliente.
- El nombre en disco/objeto es aleatorio (`secrets.token_hex`) y la extensión sale del tipo detectado: el nombre que
  envía el cliente no participa en ninguna ruta y ni siquiera se guarda.
- Almacén según `config.STORAGE_BACKEND()`:
  * `local` (por defecto): escritura atómica en `config.MEDIA_DIR`, fuera del código y de Git.
  * `supabase`: Storage REST de Supabase por httpx (sin supabase-py), bucket PRIVADO `config.SUPABASE_AUDIO_BUCKET()`.
    La clave de servicio vive SOLO en el servidor; los errores se reducen a «no se pudo guardar/leer» con el código HTTP.
- La transcripción es responsabilidad de `services/transcripcion.py`; aquí solo se conserva el texto obtenido y se
  declara el método (`simulada` si no hubo transcripción real). Un audio sin transcripción NO es un reporte vacío:
  el ciudadano puede describirlo por texto.

Adaptador de persistencia reutilizable por fotos y audios: se mantiene separado de `fotos.py` (probado aparte) para no
alterar el contrato ya establecido de las fotografías; ambos comparten el mismo patrón y variables de configuración.
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
from models.schemas import Audio

EXTENSIONES = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
               "audio/wav": "wav", "audio/webm": "webm", "audio/amr": "amr"}
_RE_ID = re.compile(r"[0-9a-f]{32}")
_RE_DATA_URI = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)

TIMEOUT_STORAGE_S = 30.0
# Transporte HTTP inyectable (solo pruebas, httpx.MockTransport). None en producción.
_transport: Optional[Any] = None


class ErrorAudio(ValueError):
    """Audio inválido o almacén no disponible (la capa de negocio lo convierte en ErrorDeNegocio).
    Sus mensajes nunca incluyen claves, URLs internas ni cuerpos de respuesta del almacén."""


def _supabase() -> bool:
    return config.STORAGE_BACKEND() == "supabase"


def _cliente_storage() -> Tuple[httpx.Client, str, Dict[str, str]]:
    url, clave, bucket = config.SUPABASE_URL(), config.SUPABASE_SERVICE_ROLE_KEY(), config.SUPABASE_AUDIO_BUCKET()
    if not url or not clave or not bucket or not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", bucket):
        raise ErrorAudio("El almacenamiento de audios no está configurado.")
    if not (url.startswith("https://") or re.match(r"http://(localhost|127\.0\.0\.1)(:\d+)?($|/)", url)):
        raise ErrorAudio("El almacenamiento de audios no está configurado.")
    cli = httpx.Client(timeout=TIMEOUT_STORAGE_S, transport=_transport)
    return cli, f"{url}/storage/v1/object/{bucket}", {"Authorization": f"Bearer {clave}", "apikey": clave}


def _peticion_storage(metodo: str, nombre: str, **kw) -> httpx.Response:
    """Una petición al Storage. Cualquier fallo se reduce a un ErrorAudio genérico (sin detalles ni secretos)."""
    cli, base, cab = _cliente_storage()
    try:
        with cli:
            return cli.request(metodo, f"{base}/{nombre}", headers={**cab, **kw.pop("headers", {})}, **kw)
    except httpx.HTTPError:
        raise ErrorAudio("El almacenamiento de audios no respondió. Inténtelo de nuevo.")


def detectar_mime(datos: bytes) -> Optional[str]:
    """Tipo según la firma binaria; None si no es un audio reconocido."""
    if datos[:4] == b"OggS":
        return "audio/ogg"
    if datos[:3] == b"ID3" or (len(datos) > 1 and datos[0] == 0xFF and (datos[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if len(datos) > 12 and datos[4:8] == b"ftyp":
        return "audio/mp4"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WAVE":
        return "audio/wav"
    if datos[:4] == b"\x1aE\xdf\xa3":
        return "audio/webm"
    if datos[:5] == b"#!AMR":
        return "audio/amr"
    return None


def decodificar(entrada: Dict[str, Any]) -> bytes:
    """Obtiene los bytes de {"nombre":..,"base64":..} (o {"bytes":..}) validando el tamaño ANTES de decodificar."""
    if not isinstance(entrada, dict):
        raise ErrorAudio("Formato de audio inválido.")
    if isinstance(entrada.get("bytes"), (bytes, bytearray)):
        datos = bytes(entrada["bytes"])
    else:
        b64 = entrada.get("base64")
        if not isinstance(b64, str) or not b64:
            raise ErrorAudio("El audio no trae contenido.")
        b64 = _RE_DATA_URI.sub("", b64.strip())
        if len(b64) > (config.MAX_BYTES_AUDIO * 4) // 3 + 8:
            raise ErrorAudio(f"El audio excede el máximo de {config.MAX_BYTES_AUDIO // (1024 * 1024)} MB.")
        try:
            datos = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ErrorAudio("El audio no es válido (base64 inválido).")
    if not datos:
        raise ErrorAudio("El audio está vacío.")
    if len(datos) > config.MAX_BYTES_AUDIO:
        raise ErrorAudio(f"El audio excede el máximo de {config.MAX_BYTES_AUDIO // (1024 * 1024)} MB.")
    return datos


def validar(entradas: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Valida todos los audios de un reporte. Devuelve una lista de dicts con `datos`, `mime` y los metadatos
    opcionales (`duracion_s`, `transcripcion`, `metodo_transcripcion`). Nada se escribe si alguno falla."""
    entradas = entradas or []
    if len(entradas) > config.MAX_AUDIOS_POR_REPORTE:
        raise ErrorAudio(f"Máximo {config.MAX_AUDIOS_POR_REPORTE} audios por reporte.")
    salida = []
    for e in entradas:
        if not isinstance(e, dict):
            raise ErrorAudio("Formato de audio inválido.")
        datos = decodificar(e)
        mime = detectar_mime(datos)
        if mime is None:
            raise ErrorAudio("El archivo no es una nota de voz o audio reconocido (OGG, MP3, M4A, WAV, WebM o AMR).")
        transcripcion = e.get("transcripcion")
        if transcripcion is not None and not isinstance(transcripcion, str):
            raise ErrorAudio("La transcripción del audio no es texto.")
        duracion = e.get("duracion_s")
        if duracion is not None:
            try:
                duracion = float(duracion)
            except (TypeError, ValueError):
                duracion = None
        salida.append({"datos": datos, "mime": mime, "duracion_s": duracion,
                       "transcripcion": (transcripcion or "").strip() or None,
                       "metodo_transcripcion": str(e.get("metodo_transcripcion") or "simulada")[:40]})
    return salida


def _ruta(audio_id: str, mime: str) -> Path:
    if not _RE_ID.fullmatch(audio_id) or mime not in EXTENSIONES:
        raise ErrorAudio("Identificador de audio inválido.")
    base = Path(config.MEDIA_DIR)
    ruta = base / f"{audio_id}.{EXTENSIONES[mime]}"
    if ruta.resolve().parent != base.resolve():          # defensa en profundidad
        raise ErrorAudio("Ruta de audio inválida.")
    return ruta


def _nombre_objeto(audio_id: str, mime: str) -> str:
    if not _RE_ID.fullmatch(audio_id) or mime not in EXTENSIONES:
        raise ErrorAudio("Identificador de audio inválido.")
    return f"{audio_id}.{EXTENSIONES[mime]}"


def _subir(audio_id: str, datos: bytes, mime: str) -> None:
    if _supabase():
        r = _peticion_storage("POST", _nombre_objeto(audio_id, mime), content=datos,
                              headers={"Content-Type": mime, "x-upsert": "false"})
        if r.status_code not in (200, 201):
            raise ErrorAudio(f"No se pudo guardar el audio (almacenamiento respondió {r.status_code}).")
        return
    ruta = _ruta(audio_id, mime)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def guardar(datos: bytes, mime: str, reporte_id: Optional[int], duracion_s: Optional[float] = None,
            transcripcion: Optional[str] = None, metodo_transcripcion: str = "simulada") -> Audio:
    """Sube/escribe el archivo (ya validado por firma y tamaño) y registra los metadatos (participa en la transacción
    activa). Si la subida falla lanza ErrorAudio y NO queda metadato."""
    audio_id = secrets.token_hex(16)
    _subir(audio_id, datos, mime)
    audio = Audio(id=audio_id, reporte_id=reporte_id, tipo_mime=mime, bytes=len(datos),
                  sha256=hashlib.sha256(datos).hexdigest(), duracion_s=duracion_s, transcripcion=transcripcion,
                  metodo_transcripcion=metodo_transcripcion, fecha=datetime.now())
    db.insertar("audios", audio.model_dump(mode="json"), clave=audio_id)
    return audio


def borrar_archivo(audio_id: str, mime: str) -> bool:
    """Borra el archivo/objeto (limpieza de huérfanos si la transacción de BD se revierte). Nunca lanza."""
    try:
        if _supabase():
            r = _peticion_storage("DELETE", _nombre_objeto(audio_id, mime))
            return r.status_code in (200, 204, 404)
        _ruta(audio_id, mime).unlink(missing_ok=True)
        return True
    except (ErrorAudio, OSError):
        return False


def eliminar(audio_id: str) -> bool:
    """Borrado controlado: elimina el archivo y, solo si se borró, sus metadatos. False si no existía o no se pudo."""
    a = metadatos(audio_id)
    if a is None or not borrar_archivo(a.id, a.tipo_mime):
        return False
    db.borrar("audios", a.id)
    return True


def metadatos(audio_id: str) -> Optional[Audio]:
    if not isinstance(audio_id, str) or not _RE_ID.fullmatch(audio_id):
        return None
    d = db.obtener("audios", audio_id)
    return Audio(**d) if d else None


def leer(audio_id: str) -> Optional[Tuple[bytes, str]]:
    """(bytes, mime) verificando el SHA-256 guardado; None si no existe."""
    a = metadatos(audio_id)
    if a is None:
        return None
    try:
        if _supabase():
            r = _peticion_storage("GET", _nombre_objeto(a.id, a.tipo_mime))
            if r.status_code != 200:
                return None
            datos = r.content
        else:
            datos = _ruta(a.id, a.tipo_mime).read_bytes()
    except (OSError, ErrorAudio):
        return None
    if hashlib.sha256(datos).hexdigest() != a.sha256:
        return None
    return datos, a.tipo_mime
