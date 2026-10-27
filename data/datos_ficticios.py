"""
DATOS FICTICIOS para la demostración. Ningún dato aquí es real.
Los reportes se cargan como texto crudo y pasan por el mismo flujo que uno escrito a mano.
"""

SECTORES_FICTICIOS = [
    {"nombre": "La Chorrera", "poblacion_estimada": 30000, "vulnerabilidad": "media", "lat": 8.880, "lng": -79.783, "alias": ["chorrera"]},
    {"nombre": "Capira", "poblacion_estimada": 15000, "vulnerabilidad": "alta", "lat": 8.755, "lng": -79.880, "alias": []},
    {"nombre": "Arraiján", "poblacion_estimada": 25000, "vulnerabilidad": "media", "lat": 8.951, "lng": -79.656, "alias": ["arraijan"]},
    {"nombre": "Nuevo Arraiján", "poblacion_estimada": 20000, "vulnerabilidad": "baja", "lat": 8.940, "lng": -79.640, "alias": ["nuevo arraijan"]},
    {"nombre": "Chame", "poblacion_estimada": 8000, "vulnerabilidad": "media", "lat": 8.595, "lng": -79.880, "alias": []},
    {"nombre": "San Carlos", "poblacion_estimada": 12000, "vulnerabilidad": "alta", "lat": 8.472, "lng": -79.950, "alias": []},
    {"nombre": "Bejuco", "poblacion_estimada": 5000, "vulnerabilidad": "alta", "lat": 8.530, "lng": -79.940, "alias": []},
    {"nombre": "El Coco", "poblacion_estimada": 18000, "vulnerabilidad": "baja", "lat": 8.640, "lng": -79.830, "alias": ["coco"]},
    {"nombre": "Vacamonte", "poblacion_estimada": 7000, "vulnerabilidad": "media", "lat": 8.890, "lng": -79.700, "alias": []},
    {"nombre": "Veracruz", "poblacion_estimada": 6000, "vulnerabilidad": "alta", "lat": 8.900, "lng": -79.600, "alias": ["vera cruz"]},
    {"nombre": "Caimito", "poblacion_estimada": 14000, "vulnerabilidad": "media", "lat": 8.810, "lng": -79.760, "alias": []},
    {"nombre": "Burunga", "poblacion_estimada": 10000, "vulnerabilidad": "media", "lat": 8.920, "lng": -79.670, "alias": []},
    {"nombre": "Santa Clara", "poblacion_estimada": 9000, "vulnerabilidad": "alta", "lat": 8.450, "lng": -80.000, "alias": []},
    {"nombre": "Playa Leona", "poblacion_estimada": 4000, "vulnerabilidad": "crítica", "lat": 8.870, "lng": -79.620, "alias": ["leona"]},
    {"nombre": "Vista Alegre", "poblacion_estimada": 11000, "vulnerabilidad": "media", "lat": 8.960, "lng": -79.680, "alias": [],
     "referencias": ["el chino de vista alegre"]},
]

# Referencias coloquiales y antecedentes FICTICIOS (ninguno es un dato real)
_EXTRA = {
    "Chame": {"referencias": ["puente de chame", "playa de chame"], "antecedentes_interrupciones": 2},
    "Capira": {"antecedentes_interrupciones": 4},
    "Burunga": {"referencias": ["las cuatro calles"], "antecedentes_interrupciones": 0},
    "Veracruz": {"antecedentes_interrupciones": 3},
    "Bejuco": {"antecedentes_interrupciones": 5},
    "Santa Clara": {"antecedentes_interrupciones": 1},
}
for _s in SECTORES_FICTICIOS:
    _s.update({"fuente_datos": "ficticio (demo)", "fecha_datos": "2026-10-01"})
    _s.update(_EXTRA.get(_s["nombre"], {}))

# Disponible hoy: 11 500 L, menos que la demanda de la demo (~16 900 L): habrá sectores pendientes.
CISTERNAS_FICTICIAS = [
    {"id": "CIS-001", "capacidad_litros": 10000, "litros_disponibles": 4000, "estado": "disponible", "sector_base": "La Chorrera"},
    {"id": "CIS-002", "capacidad_litros": 8000, "litros_disponibles": 3000, "estado": "disponible", "sector_base": "Arraiján"},
    {"id": "CIS-003", "capacidad_litros": 12000, "litros_disponibles": 2500, "estado": "disponible", "sector_base": "Capira"},
    {"id": "CIS-004", "capacidad_litros": 6000, "litros_disponibles": 1500, "estado": "disponible", "sector_base": "San Carlos"},
    {"id": "CIS-005", "capacidad_litros": 5000, "litros_disponibles": 5000, "estado": "en_mantenimiento", "sector_base": "Bejuco"},
    {"id": "CIS-006", "capacidad_litros": 4000, "litros_disponibles": 500, "estado": "disponible", "sector_base": "Chame",
     "sectores_elegibles": ["Chame", "Playa Leona", "Veracruz", "Bejuco"]},  # camión pequeño: accesos estrechos
]

# Textos crudos. Incluyen un caso ambiguo, duplicados posibles (y uno dudoso),
# un reporte sin sector y volumen de quejas repetidas (La Chorrera) que NO debe subir la prioridad.
REPORTES_FICTICIOS = [
    "Llevamos 3 días sin agua en La Chorrera, calle principal. Hay muchos ancianos. No sabemos cuántas casas.",
    "3 días sin agua en La Chorrera, calle principal. Urgente, necesitamos agua.",
    "Otra vez: La Chorrera, calle principal, 3 días sin agua, unas 40 familias.",
    "Sin agua desde ayer por la tarde. Hay niños pequeños y adultos mayores. En Bejuco, 50 personas.",
    "CRÍTICO: 5 días sin agua en Capira, centro. Más de 200 personas afectadas y un centro de salud sin servicio.",
    "Llevamos 12 horas sin agua en El Coco, zona residencial. Unas 80 personas.",
    "Desde anoche sin agua. Muchas personas mayores en Veracruz. 15 familias. Urgente.",
    "Falta de agua hace 2 días en Arraiján, Colonia Las Flores. 30 familias.",
    "2 días sin agua en Arraiján cerca de la escuela, como 100 personas.",
    "6 horas sin agua en Nuevo Arraiján, 40 personas.",
    "4 días sin agua en San Carlos. Comunidad completa afectada, aproximadamente 150 personas. Crítico.",
    "30 horas sin agua en Caimito, zona alta. 70 personas.",
    "3 días sin agua en Santa Clara. Urgente, hay enfermos. 90 personas.",
    "8 horas sin agua en Chame, 30 personas.",
    "5 días sin agua en Playa Leona, zona aislada. 60 personas. Crítico.",
    "No tenemos agua desde hace dos días y no sabemos a quién avisar, somos como 25 familias.",
    "En Vista Alegre no tenemos agua desde ayer.",
    "Cerca del chino de Vista Alegre hay varias casas sin agua desde hace dos días.",
    "Se rompió una tubería cerca de la escuela en Vista Alegre y no llega agua.",
]
