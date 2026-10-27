"""Identidad visual de HIDRO360. Paleta y proporciones derivadas del prototipo conceptual (no está
en el repositorio; ver `docs/DISENO_UI.md`) y del logo (gota azul sobre fondo blanco: se usa SOLO sobre superficies blancas)."""

NAVY = "#09265d"
BLUE = "#087cf0"
CYAN = "#15c9e8"
INK = "#17243b"
MUTED = "#566478"  # contraste ≥ 5:1 sobre blanco (el #6d7b91 del prototipo daba ~4:1)
LINE = "#e5edf5"
BG = "#f5f9fd"
WHITE = "#ffffff"
RED = "#e85d67"
AMBER = "#f2a93b"
GREEN = "#169b79"

# Colores de estado → (fondo, texto)
PILL = {
    "ok": ("#d9f0e3", "#11643a"), "warn": ("#fff1cf", "#7a5300"), "bad": ("#f9d9d5", "#8a1f12"),
    "info": ("#dbeaf5", "#0f4466"), "ficticio": (AMBER, "#3d2b00"), "neutro": ("#eef2f7", "#44546a"),
}
ESTADO_PILL = {
    "por_revisar": ("por revisar", "warn"), "confirmado": ("confirmado", "ok"), "duplicado": ("duplicado", "info"),
    "en_plan": ("en plan", "info"), "rechazado": ("rechazado", "bad"), "propuesto": ("propuesto", "warn"),
    "aprobado": ("aprobado", "ok"), "ok": ("ok", "ok"), "error": ("error", "bad"), "fallback": ("fallback", "warn"),
    "CRÍTICA": ("crítica", "bad"), "ALTA": ("alta", "warn"), "MEDIA": ("media", "info"), "BAJA": ("baja", "ok"),
}
CATEGORIA_COLOR = {"CRÍTICA": RED, "ALTA": AMBER, "MEDIA": BLUE, "BAJA": GREEN}

ANCHO_MAX = 1240
