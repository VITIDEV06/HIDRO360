"""
Módulo 5 - Comunicación.
Solo redacta BORRADORES informativos. No envía nada y no promete hora de llegada.
"""

from typing import Dict, List

from models.schemas import Asignacion, SectorPendiente

AVISO = "BORRADOR - no enviado. Requiere revisión humana antes de cualquier uso."


def borrador_asignacion(sector: str, litros: int, cisternas: List[str]) -> str:
    """Plantilla local (sin IA) del aviso de asignación propuesta."""
    total_txt = f"{litros:,}".replace(",", " ")
    return (f"Aviso para {sector}: hemos recibido los reportes de falta de agua y se está "
            f"programando abastecimiento con cisternas ({', '.join(cisternas)}), hasta {total_txt} litros previstos. "
            "La hora de llegada aún no está confirmada; informaremos cuando lo esté. "
            "Gracias por su paciencia.\n\n" + AVISO)


def redactar_borradores(asignaciones: List[Asignacion], pendientes: List[SectorPendiente]) -> List[Dict[str, str]]:
    borradores: List[Dict[str, str]] = []
    por_sector: Dict[str, List[Asignacion]] = {}
    for a in asignaciones:
        por_sector.setdefault(a.sector, []).append(a)

    for sector, items in por_sector.items():
        borradores.append({
            "sector": sector,
            "tipo": "asignación propuesta",
            "texto": borrador_asignacion(sector, sum(i.litros for i in items), sorted({i.cisterna_id for i in items})),
        })
    for p in pendientes:
        borradores.append({
            "sector": p.sector,
            "tipo": "pendiente",
            "texto": (
                f"Aviso para {p.sector}: su reporte fue recibido y está en evaluación. "
                "Por ahora no podemos confirmar abastecimiento ni horario. "
                "Le mantendremos informado.\n\n" + AVISO),
        })
    return borradores
