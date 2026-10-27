"""
Módulo 6 (parte) - Auditoría append-only con encadenamiento de hashes.
Cada entrada guarda responsable, fecha, acción, motivo y detalle (cambios).
El hash de cada entrada incluye el de la anterior: alterar el historial es detectable.
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from data import database as db
from models.schemas import EntradaAuditoria

GENESIS = "0" * 64


def _hash(previo: str, cuerpo: Dict[str, Any]) -> str:
    return hashlib.sha256((previo + json.dumps(cuerpo, sort_keys=True, ensure_ascii=False, default=str)).encode()).hexdigest()


def registrar(responsable: str, accion: str, entidad: str, entidad_id: Any,
              motivo: str = "", detalle: Optional[Dict[str, Any]] = None) -> EntradaAuditoria:
    ultima = db.ultimo("auditoria")
    previo = ultima["hash"] if ultima else GENESIS
    cuerpo = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "responsable": responsable, "accion": accion, "entidad": entidad,
        "entidad_id": str(entidad_id), "motivo": motivo,
        "detalle": {"resultado": "ok", "version_reglas": config.VERSION_REGLAS, **(detalle or {})},
    }
    h = _hash(previo, cuerpo)
    nuevo_id = db.insertar("auditoria", {**cuerpo, "hash_previo": previo, "hash": h})
    return EntradaAuditoria(id=nuevo_id, **cuerpo, hash_previo=previo, hash=h)


def listar(entidad: Optional[str] = None, entidad_id: Optional[Any] = None) -> List[EntradaAuditoria]:
    todas = [EntradaAuditoria(**d) for d in db.listar("auditoria")]
    if entidad:
        todas = [e for e in todas if e.entidad == entidad]
    if entidad_id is not None:
        todas = [e for e in todas if e.entidad_id == str(entidad_id)]
    return todas


def verificar_integridad() -> bool:
    previo = GENESIS
    for d in db.listar("auditoria"):
        cuerpo = {k: d[k] for k in ("fecha", "responsable", "accion", "entidad", "entidad_id", "motivo", "detalle")}
        if d["hash_previo"] != previo or d["hash"] != _hash(previo, cuerpo):
            return False
        previo = d["hash"]
    return True
