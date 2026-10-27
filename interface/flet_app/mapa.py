"""Mapas de la interfaz (flet-map sobre teselas de OpenStreetMap). Sin lógica de negocio ni coordenadas inventadas.

- `selector(...)`: mapa del portal ciudadano; un clic coloca el marcador y devuelve lat/lng reales.
- `mini(lat, lng)`: mapa pequeño de solo lectura con la ubicación de un reporte.
- `general(...)`: mapa de la consola con reportes, cisternas y paradas que vienen de `workflow.datos_mapa()`.
Las teselas se descargan de internet: sin conexión el recuadro queda gris y los formularios siguen funcionando.
Si la librería flet-map no está instalada se muestra un aviso en lugar del mapa (la interfaz no se cae)."""

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import flet as ft

from interface.flet_app import components as C
from interface.flet_app import theme as T

try:                      # flet-map es opcional en tiempo de ejecución: sin ella no hay mapa, pero sí aplicación
    import flet_map as fm
except Exception:         # pragma: no cover - depende del entorno
    fm = None

CENTRO_PANAMA_OESTE = (8.88, -79.78)     # La Chorrera; solo encuadre inicial, no un dato de ningún reporte
TESELAS = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATRIBUCION = "© colaboradores de OpenStreetMap"

COLOR_ESTADO = {"por_revisar": T.AMBER, "confirmado": T.BLUE, "en_plan": T.GREEN, "duplicado": T.MUTED, "rechazado": T.RED}
ETIQUETA_ESTADO = {"por_revisar": "por revisar", "confirmado": "confirmado", "en_plan": "en plan", "duplicado": "duplicado", "rechazado": "rechazado"}


def disponible() -> bool:
    return fm is not None


def coordenadas_validas(lat: Any, lng: Any) -> bool:
    try:
        return lat is not None and lng is not None and -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180
    except (TypeError, ValueError):
        return False


def _sin_mapa() -> ft.Control:
    return C.nota("El mapa no está disponible en este equipo (falta la librería flet-map). Puede seguir usando el resto de la pantalla.", "warn")


def _pin(color: str, icono=ft.Icons.LOCATION_ON, tam: float = 34, tooltip: Optional[str] = None) -> ft.Control:
    return ft.Icon(icono, color=color, size=tam, tooltip=tooltip)


def _marcador(lat: float, lng: float, color: str, icono=ft.Icons.LOCATION_ON, tam: float = 34, tooltip: Optional[str] = None):
    return fm.Marker(content=_pin(color, icono, tam, tooltip), coordinates=fm.MapLatitudeLongitude(float(lat), float(lng)),
                     width=tam + 6, height=tam + 6, alignment=ft.Alignment.TOP_CENTER if icono == ft.Icons.LOCATION_ON else ft.Alignment.CENTER)


def _teselas():
    return fm.TileLayer(url_template=TESELAS, user_agent_package_name="hidro360.teamvify", max_native_zoom=19)


def _atribucion():
    return fm.RichAttribution(attributions=[fm.TextSourceAttribution(ATRIBUCION)])


def _mapa(capas: list, centro: Tuple[float, float], zoom: float, alto: int, on_tap: Optional[Callable] = None, rueda: bool = False):
    """Sin rotación; la rueda del ratón solo hace zoom si `rueda` (en formularios con desplazamiento molesta: se movería la página)."""
    flags = (fm.InteractionFlag.DRAG | fm.InteractionFlag.FLING_ANIMATION | fm.InteractionFlag.PINCH_MOVE | fm.InteractionFlag.PINCH_ZOOM
             | fm.InteractionFlag.DOUBLE_TAP_ZOOM)
    if rueda:
        flags |= fm.InteractionFlag.SCROLL_WHEEL_ZOOM
    return fm.Map(layers=capas, initial_center=fm.MapLatitudeLongitude(*centro), initial_zoom=zoom, height=alto, on_tap=on_tap,
                  interaction_configuration=fm.InteractionConfiguration(flags=flags), bgcolor="#dfe9f3", min_zoom=3, max_zoom=19)


def _marco(mapa: ft.Control) -> ft.Control:
    return ft.Container(mapa, border_radius=14, border=ft.Border.all(1, T.LINE), clip_behavior=ft.ClipBehavior.HARD_EDGE)


# --------------------------------------------------------------------------------------------- portal
class Selector:
    """Mapa para elegir un punto. `punto` es (lat, lng) o None; `on_cambio(lat, lng)` se llama tras cada clic."""

    def __init__(self, on_cambio: Optional[Callable[[float, float], None]] = None, alto: int = 340):
        self.punto: Optional[Tuple[float, float]] = None
        self.on_cambio = on_cambio
        self.capa = fm.MarkerLayer(markers=[]) if fm else None
        self.mapa = _mapa([_teselas(), self.capa, _atribucion()], CENTRO_PANAMA_OESTE, 10, alto, self._tap) if fm else None
        self.control: ft.Control = _marco(self.mapa) if fm else _sin_mapa()

    def _tap(self, e) -> None:
        c = getattr(e, "coordinates", None)
        if c is not None:
            self.fijar(c.latitude, c.longitude)
            if self.on_cambio:
                self.on_cambio(self.punto[0], self.punto[1])

    def fijar(self, lat: float, lng: float) -> None:
        if not coordenadas_validas(lat, lng):
            raise ValueError("Coordenadas fuera de rango.")
        self.punto = (round(float(lat), 6), round(float(lng), 6))
        if self.capa is not None:
            self.capa.markers = [_marcador(self.punto[0], self.punto[1], T.RED, tooltip="Lugar del problema")]

    def limpiar(self) -> None:
        self.punto = None
        if self.capa is not None:
            self.capa.markers = []


def mini(lat: Any, lng: Any, alto: int = 200) -> ft.Control:
    """Mapa de lectura con una sola ubicación real. Sin coordenadas válidas devuelve un aviso, nunca un punto inventado."""
    if not coordenadas_validas(lat, lng):
        return C.vacio("Este reporte no trae ubicación en el mapa.", ft.Icons.LOCATION_OFF)
    if fm is None:
        return _sin_mapa()
    capa = fm.MarkerLayer(markers=[_marcador(lat, lng, T.RED, tooltip=f"{float(lat):.5f}, {float(lng):.5f}")])
    return _marco(_mapa([_teselas(), capa, _atribucion()], (float(lat), float(lng)), 15, alto))


# ------------------------------------------------------------------------------------------- consola
def _lista(d: Any, clave: str) -> List[Dict[str, Any]]:
    v = d.get(clave) if isinstance(d, dict) else None
    return [x for x in v if isinstance(x, dict)] if isinstance(v, (list, tuple)) else []


def puntos_de_mapa(datos: Any) -> Dict[str, list]:
    """Normaliza `datos_mapa()` descartando todo lo que no tenga coordenadas válidas (nada se inventa ni se coloca en un centro)."""
    reportes = [r for r in _lista(datos, "reportes") if coordenadas_validas(r.get("lat"), r.get("lng"))]
    cisternas = [c for c in _lista(datos, "cisternas") if coordenadas_validas(c.get("lat"), c.get("lng"))]
    paradas = [p for p in _lista(datos, "paradas") if coordenadas_validas(p.get("lat"), p.get("lng"))]      # forma de datos_mapa()
    for m in _lista(datos, "misiones"):                                                                     # forma alternativa: paradas anidadas
        if str(m.get("estado", "")) in ("completada", "cancelada"):
            continue
        for p in m.get("paradas") or []:
            if isinstance(p, dict) and coordenadas_validas(p.get("lat"), p.get("lng")):
                paradas.append({**p, "mision_id": m.get("id"), "cisterna_id": m.get("cisterna_id")})
    return {"reportes": reportes, "cisternas": cisternas, "paradas": paradas}


def general(datos: Any, mostrar: Iterable[str] = ("reportes", "cisternas", "paradas"), estados: Optional[Iterable[str]] = None, alto: int = 520) -> ft.Control:
    if fm is None:
        return _sin_mapa()
    pts = puntos_de_mapa(datos)
    mostrar = set(mostrar)
    estados = set(estados) if estados is not None else None
    marcas = []
    if "reportes" in mostrar:
        for r in pts["reportes"]:
            est = str(r.get("estado", ""))
            if estados is not None and est not in estados:
                continue
            tip = f"Reporte #{r.get('id')} · {ETIQUETA_ESTADO.get(est, est)}" + (f" · {r['sector']}" if r.get("sector") else "") + \
                  (f" · severidad {r['severidad']}" if r.get("severidad") else "")
            marcas.append(_marcador(r["lat"], r["lng"], COLOR_ESTADO.get(est, T.MUTED), tooltip=tip))
    if "cisternas" in mostrar:
        for c in pts["cisternas"]:
            marcas.append(_marcador(c["lat"], c["lng"], T.NAVY, ft.Icons.LOCAL_SHIPPING, 30, f"Cisterna {c.get('id')}" + (f" · {c['estado']}" if c.get("estado") else "")
                                    + {"base_sector": " · en su sector base (no es posición en vivo)", "ultima_parada": " · última parada alcanzada"}.get(c.get("fuente_ubicacion"), "")))
    if "paradas" in mostrar:
        for p in pts["paradas"]:
            marcas.append(_marcador(p["lat"], p["lng"], T.CYAN, ft.Icons.FLAG, 28,
                                    f"Misión #{p.get('mision_id')} · parada {p.get('orden')} · {p.get('sector', '')} · {p.get('litros', '?')} L"))
    todos = [(m.coordinates.latitude, m.coordinates.longitude) for m in marcas]
    centro = (sum(a for a, _ in todos) / len(todos), sum(b for _, b in todos) / len(todos)) if todos else CENTRO_PANAMA_OESTE
    zoom = 11 if todos else 10
    return _marco(_mapa([_teselas(), fm.MarkerLayer(markers=marcas), _atribucion()], centro, zoom, alto, rueda=True))


def leyenda() -> ft.Control:
    def item(color, icono, texto):
        return ft.Row([ft.Icon(icono, color=color, size=18), ft.Text(texto, size=12, color=T.INK)], spacing=4)
    return ft.Row([item(T.AMBER, ft.Icons.LOCATION_ON, "reporte por revisar"), item(T.BLUE, ft.Icons.LOCATION_ON, "confirmado"),
                   item(T.GREEN, ft.Icons.LOCATION_ON, "en plan"), item(T.MUTED, ft.Icons.LOCATION_ON, "duplicado"),
                   item(T.NAVY, ft.Icons.LOCAL_SHIPPING, "cisterna"), item(T.CYAN, ft.Icons.FLAG, "parada de misión")],
                  spacing=14, run_spacing=4, wrap=True)
