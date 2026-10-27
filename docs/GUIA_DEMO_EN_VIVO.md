# Guía de demostración en vivo — HIDRA — Reto 01 (HIDRO360)

> **Objetivo:** mostrar HIDRO360 funcionando de extremo a extremo en **5–7 minutos**, con énfasis en lo que evalúa la rúbrica: coordinación, supervisión humana, explicabilidad, auditoría y honestidad sobre lo no probado.
> **Regla de oro:** todo lo que se vede debe estar **ejecutado**, no descrito. Si algo no se probó, se dice.

## Antes de empezar (checklist de 60 s)

1. `python app.py` abre la ventana de escritorio (o `python app.py --web`). **La primera ejecución necesita internet** para bajar el cliente de Flet.
2. Confirmar que hay un administrador: si no, `python -m scripts.crear_admin`.
3. Tener a mano una frase de ejemplo para el portal: «En Vista Alegre no tenemos agua desde ayer».
4. **No** usar datos reales de personas. Los sectores, cisternas y reportes de la demo son **ficticios**.

## Guion (8 pasos)

| # | Paso | Qué se dice | Qué se ve | Tiempo |
|---|---|---|---|---|
| 1 | **Problema** | «Panamá Oeste recibe reportes de falta de agua por canales informales; se duplican y no hay trazabilidad.» | Portada y portal público | 30 s |
| 2 | **Reporte ciudadano** | «Cualquiera reporta sin cuenta.» | **Reportar** la frase del paso 3; aparece un código `HID-…` | 40 s |
| 3 | **Consulta con código** | «El ciudadano sigue su reporte con el código, sin registro.» | **Consultar mi reporte** muestra el estado | 20 s |
| 4 | **Carga de datos ficticios** | «Para la demo cargamos datos de ejemplo; pide confirmación.» | Acceso administración → login → *Diagnóstico y desarrollo → Cargar datos de ejemplo* | 30 s |
| 5 | **Ingesta + revisión humana** | «La IA propone una lectura; una persona **revisa, corrige y confirma**.» | **Revisión** → detalle con evidencia literal → editar → **Confirmar** | 60 s |
| 6 | **Duplicados y prioridad** | «Los duplicados solo se **sugieren**; la prioridad la calcula una fórmula **transparente** (40 % horas, 30 % población, 30 % vulnerabilidad).» | **Duplicados** (decidir sí/no) → **Priorización** (desglose y razones) | 70 s |
| 7 | **Plan de cisternas + aprobación** | «El planificador reparte cisternas respetando capacidad; **nada se ejecuta sin aprobación humana**.» | **Planificación** → generar → editar (pide motivo) → **Aprobar** con confirmación | 70 s |
| 8 | **Trazabilidad (el cierre)** | «Todo queda auditado con cadena de hashes: qué, quién, cuándo y por qué.» | **Auditoría** y **Trazas**; abrir una entrada y mostrar antes/después | 60 s |

**Cierre (30 s):** «La IA propone; las reglas deterministas calculan; **una persona decide**. Lo que no está probado —Gemini real, firmware en placa, Supabase/Vercel— lo decimos tal cual.»

## Plan B (offline y sin nubes)

La demo **no necesita internet ni claves** salvo el primer arranque de Flet. Si algo falla:

| Si falla… | Plan B |
|---|---|
| No hay red / no hay Gemini | Nada que hacer: el sistema usa **reglas locales** y lo declara como `reglas`/`reglas(fallback)`. La demo es idéntica. |
| No hay `DATABASE_URL` (Supabase) | Todo corre con **SQLite local** (`agua_clara.db`). Es el modo por defecto de la demo. |
| No hay Vercel | No se usa en la demo: la UI llama a `services/workflow` **en el mismo proceso**. |
| La ventana de Flet no arranca | `python app.py --web` y abrir `http://127.0.0.1:8501` (necesita internet en el navegador), o mostrar `uvicorn api.main:app` + `/docs`. |
| Se quiere empezar de cero | *Diagnóstico y desarrollo → Cargar datos de ejemplo* (pide confirmación; **no** borra la base real sin confirmar). |

## Qué NO decir (evitar afirmar lo no probado)

- ❌ «Gemini está probado con una clave real.» → Integrado; en el desarrollo se probó con cliente simulado. Verificar con `python -m scripts.verificar_gemini`.
- ❌ «El firmware funciona en la placa.» → **Compila**, pero **no se probó en un ESP32-S3 físico**.
- ❌ «Está desplegado en Supabase/Vercel.» → Configuración probada con `vercel build` local; **no desplegado**.
- ❌ «Producimos este impacto.» → El experimento de impacto es **sintético**.
- ✅ Sí se puede decir: «modo local funciona sin internet ni claves», «duplicados solo sugeridos», «aprobación humana obligatoria», «auditoría y trazas con cadena de hashes».

## Preguntas frecuentes del jurado

| Pregunta | Respuesta corta |
|---|---|
| ¿Quién decide? | Una persona. La IA no aprueba, no cierra, no asigna ni calcula prioridad. |
| ¿Cómo se evita que la IA invente? | Salida validada con Pydantic (`extra=forbid`), revalidada en código (sector del catálogo, evidencia literal) y con fallback local declarado. |
| ¿Se pueden fusionar reportes solos? | No: los duplicados se **sugieren**; la fusión la decide el operador. |
| ¿Qué pasa sin internet? | Todo el flujo funciona en modo local; la única dependencia de red es el primer arranque del cliente de Flet. |
| ¿Cómo sé que nada se alteró? | Auditoría y trazas con **cadena de hashes**; `verificar_integridad()` detecta cambios intermedios. |

Detalle técnico de cada paso: `docs/INSTALACION.md`, `docs/ARQUITECTURA.md` y `README.md`.
