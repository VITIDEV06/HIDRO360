"""
Rutas del flujo asistido por IA. NO importa api.main (evita ciclos): el líder las incluye con

    app.include_router(rutas_ia.crear_router(admin), prefix="/api/v1")

`admin_dep` es la dependencia de administrador de api.main (devuelve el Actor autenticado). Los errores
(ErrorDeNegocio, autenticación, autorización) los traducen los manejadores globales de la aplicación.
"""

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from models.schemas import Parada, Recomendacion
from services import auth, workflow_ia


class _Cuerpo(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AnalisisNuevo(_Cuerpo):
    responsable: Optional[str] = Field(None, min_length=1)


class DecisionRecomendacion(_Cuerpo):
    accion: str = Field(pattern="^(aprobar|modificar|rechazar)$")
    responsable: Optional[str] = Field(None, min_length=1)
    motivo: str = ""
    cisterna_id: Optional[str] = None
    paradas: Optional[List[Parada]] = None


def crear_router(admin_dep: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["ia"])

    def _ejecutar(actor, fn, *a, **k):
        with auth.como(actor):
            return fn(*a, **k)

    @router.post("/reportes/{reporte_id}/analisis", response_model=Recomendacion, status_code=201)
    def analizar(reporte_id: int, body: Optional[AnalisisNuevo] = None, actor=Depends(admin_dep)):
        resp = (body.responsable if body else None) or actor.usuario
        return _ejecutar(actor, workflow_ia.analizar_reporte, reporte_id, resp)

    @router.get("/recomendaciones", response_model=List[Recomendacion])
    def listar(estado: Optional[str] = None, reporte_id: Optional[int] = None, actor=Depends(admin_dep)):
        return _ejecutar(actor, workflow_ia.listar_recomendaciones, estado, reporte_id)

    @router.get("/recomendaciones/{recomendacion_id}", response_model=Recomendacion)
    def obtener(recomendacion_id: int, actor=Depends(admin_dep)):
        return _ejecutar(actor, workflow_ia.obtener_recomendacion, recomendacion_id)

    @router.post("/recomendaciones/{recomendacion_id}/decision", response_model=Recomendacion)
    def decidir(recomendacion_id: int, body: DecisionRecomendacion, actor=Depends(admin_dep)):
        return _ejecutar(actor, workflow_ia.decidir_recomendacion, recomendacion_id, body.accion,
                         body.responsable or actor.usuario, body.motivo, body.cisterna_id, body.paradas)

    @router.get("/reportes/{reporte_id}/contexto")
    def contexto(reporte_id: int, usar_ia: bool = True, actor=Depends(admin_dep)) -> Dict[str, Any]:
        """Resumen de contexto para el operador (reporte + duplicados sugeridos + historial del sector)."""
        return _ejecutar(actor, workflow_ia.resumen_contexto_reporte, reporte_id, usar_ia)

    @router.get("/patrones")
    def patrones(ventana_horas: int = Query(72, ge=1, le=720), minimo_reportes: int = Query(3, ge=2, le=50),
                 usar_ia: bool = True, actor=Depends(admin_dep)) -> Dict[str, Any]:
        """Patrones deterministas (sector recurrente, pico horario) con explicación en lenguaje claro."""
        return _ejecutar(actor, workflow_ia.detectar_patrones, ventana_horas, minimo_reportes, usar_ia)

    return router
