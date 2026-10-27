# IA y Gemini en HIDRO360

> **Estado honesto.** Verificado contra el servicio real de Gemini el 2026-10-17 (modelo `gemini-3.5-flash-lite`, 5 reportes sintéticos; ver «Resultados reales»). Las demás pruebas automáticas usan un cliente simulado. Sin clave el sistema funciona completo en modo local (reglas).

## Qué hace la IA y qué no
| Tarea | Quién | Dónde |
|---|---|---|
| Interpretar el reporte (sector, referencia, horas, personas, severidad, tipo, vulnerabilidad, servicio esencial, inciertos, evidencia) | Gemini **propone**; el código valida | `services/ingest.py` → `InterpreteGemini` |
| Resumen explicable de un puntaje ya calculado | Gemini redacta; el código rechaza cifras inventadas | `services/ia_tareas.py` |
| Borrador de comunicación | Gemini redacta; el código rechaza promesas de hora; el aviso «BORRADOR» lo añade el código | `services/ia_tareas.py` |
| **Recomendación de respuesta** (acción sugerida de 1-3 frases para un reporte analizado) | Gemini redacta con esquema estricto; el código la descarta si inventa sector/cisterna/cifras, incluye datos personales o promete hora | `services/ia_tareas.py` → `recomendar_respuesta` |
| Prioridad, plan, capacidad, estados, auditoría, aprobación | **Código determinista / personas** (nunca la IA) | `services/*` |
Gemini **no** aprueba reportes, no cierra incidentes, no asigna cisternas ni calcula prioridad.

## Cómo se configura
1. Cree una clave en https://aistudio.google.com/apikey (requiere cuenta de Google). **No la pegue en el código, el chat, el README ni los tests.**
2. Copie `.env.example` a `.env` (está en `.gitignore`) y escriba `GEMINI_API_KEY=...`. Opcional: `GEMINI_MODEL`, `AI_TIMEOUT_SECONDS`, `AI_MAX_REINTENTOS`.
3. `AI_PROVIDER`: `auto` (defecto: Gemini solo si hay clave) · `local` (nunca sale texto del equipo) · `gemini` (fuerza Gemini; si falla, modo local).
4. Verifique: `python -m scripts.verificar_gemini` (muestra proveedor, modelo y si hay clave —sin mostrarla—, lista los modelos disponibles y hace una llamada mínima; códigos de salida 0/1/2). `--sin-llamada` solo comprueba la configuración. En la app: **Configuración → Verificar conexión con Gemini**.
5. Cambiar de modelo: edite `GEMINI_MODEL` en `.env`; no hay que tocar código. **El modelo por defecto (`gemini-3.5-flash-lite`) se tomó de la página de modelos de Google consultada el 2026-10-16; los nombres cambian: confírmelo con el comando anterior.**

## Límites, gratuidad y condiciones
Según la documentación de Google consultada el 2026-10-16: existe un nivel gratuito ligado a un proyecto; los límites (solicitudes/min, tokens/min, solicitudes/día) dependen del modelo y del proyecto, **se ven en AI Studio**, pueden cambiar y no están garantizados; las cuotas diarias se reinician a medianoche (Pacífico). **No se promete que ningún modelo ni cuota sea gratuito de forma permanente.** Revise las condiciones de uso y de tratamiento de datos vigentes de Google antes de enviar datos reales: esta guía no las ha verificado.

## Fallback local
Se activa si falta clave/paquete, hay timeout/red caída, 429 (cuota), 401/403, 5xx persistente, respuesta vacía/bloqueada, JSON inválido, campo extra o rango inválido. El resultado queda como `metodo = "reglas(fallback)"`, con una nota visible en la revisión y un evento `fallback` en las trazas. **Nunca** se presenta como generado por Gemini.
- Reintento prudente: máximo `AI_MAX_REINTENTOS` (1), solo para errores transitorios (5xx, timeout, red).
- Corte de circuito: tras 429 se suspenden las llamadas 60 s; tras 401/403, 15 min; tras 3 fallos seguidos, 60 s (estado visible en Configuración; botón «Reiniciar estado»).

## Validación y seguridad
- **Entrada no confiable**: el texto del ciudadano es dato. Las frases que parecen instrucciones («olvida las reglas…») se excluyen de la extracción y se avisa; el prompt separa instrucciones del sistema y datos (`<reporte>…</reporte>`); el modelo no tiene herramientas.
- **Privacidad**: antes de enviar se redactan teléfonos y correos (`[tel]`, `[correo]`). Se envía solo el texto del reporte (sin id, sin otros reportes); las tareas de resumen/borrador envían solo sector, puntaje, factores o litros. No se envía nada si `AI_PROVIDER=local`.
- **Salida**: esquema estricto `SalidaIA` (`extra="forbid"`, rangos) + revalidación en código: el sector lo decide el catálogo; cifras y severidad exigen una evidencia que aparezca **literalmente** en el texto enviado (y fuera de las frases sospechosas); enums y formatos; lo no respaldado se descarta y se marca incierto.
- **Confianza**: la que declara el modelo es una autoevaluación subjetiva; **no es una probabilidad calibrada** y la interfaz lo indica.
- **Secretos**: la clave solo sale de variables de entorno/`.env`; no se registra en trazas, errores ni estado (pruebas: `tests/test_gemini.py`).
- **Trazas**: cada llamada deja `ia/gemini.<tarea>` con modelo, estado y duración, sin contenido.

## Orquestador: del reporte a la misión (con aprobación humana)
```
Reporte -> validación -> duplicados -> clasificación -> prioridad -> recomendación -> asignación -> [APROBACIÓN HUMANA] -> misión
```
Código: `services/orquestador.py` (lógica pura, sin base de datos), `services/workflow_ia.py` (persistencia, permisos, auditoría) y `api/rutas_ia.py` (router FastAPI; el líder lo monta con `app.include_router(rutas_ia.crear_router(admin), prefix="/api/v1")`).

| Etapa | Quién | Reutiliza |
|---|---|---|
| validación | código | estado del reporte, sector del catálogo (si el reporte está rechazado/duplicado/en plan el flujo **se detiene** y el resto queda `omitida`) |
| duplicados | código (solo **sugiere**; nada se fusiona) | `geoloc.sugerir_duplicados` |
| clasificación | **Gemini** propone / reglas | `ingest.interpretar` (informativa: no modifica el reporte) |
| prioridad | código | `prioritization` (el reporte se evalúa «como si» estuviera confirmado, sin cambiarlo) |
| recomendación | **Gemini** redacta / texto local | `ia_tareas.recomendar_respuesta` |
| asignación | código | `planner` (capacidad, `litros_disponibles`, elegibilidad; excluye cisternas con misión activa). Si no hay cisterna posible: `cisterna_sugerida=None` con el motivo en el detalle de la etapa |

- **Procedencia por etapa** (`EtapaIA.proveedor`): `gemini` solo si Gemini respondió y la salida pasó la validación; `reglas` si el modo local es la configuración; `reglas(fallback)` si se intentó Gemini y falló (429, 401/403, 5xx, timeout, sin red/clave, JSON inválido, campo extra, sector/cisterna/cifras inventados, datos personales, promesa de hora). **Global** (`Recomendacion.proveedor`): `gemini` solo si **todas** las etapas con IA (clasificación y recomendación) respondieron Gemini; `reglas(fallback)` si alguna lo intentó y falló; si no, `reglas`. El fallback nunca se rotula como Gemini y deja traza `fallback` (`orquestador/etapa.<nombre>` y `ia/...`).
- **Aislamiento de errores**: cada etapa corre aparte; si una falla queda `error`/`fallback` y las demás continúan con datos deterministas.
- **Humano en el bucle**: `analizar_reporte` solo guarda una `Recomendacion` «propuesta» (re-analizar crea otra, no sobrescribe). `decidir_recomendacion(id, "aprobar"|"modificar"|"rechazar", ...)` es la **única** vía a una misión (`workflow.crear_mision`), exige rol administrador, rechazar y modificar exigen motivo, la propuesta se revalida contra el estado actual de las cisternas y una recomendación decidida no se puede decidir de nuevo. Todo queda en la auditoría (misma transacción).
- **API** (administrador): `POST /api/v1/reportes/{id}/analisis` · `GET /api/v1/recomendaciones?estado=&reporte_id=` · `GET /api/v1/recomendaciones/{id}` · `POST /api/v1/recomendaciones/{id}/decision` con `{"accion","motivo","cisterna_id","paradas"}`.

### Cómo probarlo
1. Modo local (sin clave): `.venv\Scripts\python -m pytest tests/test_orquestador.py -q`. Las pruebas con Gemini usan un **cliente simulado (mock)**: demuestran la validación, la procedencia y el fallback, **no** el servicio real.
2. Servicio real: configure `GEMINI_API_KEY` (ver arriba), ejecute `python -m scripts.verificar_gemini` y después `pytest tests/test_orquestador.py -k gemini_real -q` (se **omite** sin clave). Con clave, además, analice un reporte desde la API y compruebe que `etapas[clasificacion].proveedor` y `etapas[recomendacion].proveedor` sean `gemini`.
3. **Qué falta por probar**: volumen (solo decenas de llamadas), calidad semántica de la clasificación frente a un conjunto etiquetado y 5xx/timeouts reales (solo simulados).

## Limitaciones
Modelo y cuotas pueden cambiar; la extracción por reglas es regex; el modo Gemini aún no se evaluó con los 22 casos del runner (el runner usa el modo local); no se usa salida estructurada nativa del SDK (`response_schema`) porque no pudo verificarse sin clave: se pide JSON (`response_mime_type`) y se valida con Pydantic.

## Resultados reales (2026-10-17, `gemini-3.5-flash-lite`, nivel gratuito)
Prueba del flujo COMPLETO (`analizar_reporte`) sobre una base temporal con datos de ejemplo y 5 reportes sintéticos: jerga coloquial con sector, sin sector, con intento de inyección de prompt, con teléfono y correo, y uno largo (~900 caracteres).

| Medida | Antes de ajustar | Después |
|---|---|---|
| Clasificación aceptada (`gemini`) | 2/5 | **5/5** |
| Recomendación aceptada (`gemini`) | 4/4 con sector (la 5.ª, sin sector, la redacta el código por diseño) | **4/4** con sector |
| Procedencia global `gemini` | 1/5 | 4/5 (la 5.ª es local por diseño: sin sector) |
| Latencia por llamada | 1,0-1,5 s | igual |
| `analizar_reporte` completo | 1,4-3,0 s | 1,4-2,5 s |

Causas de descarte observadas en la clasificación (todas de TIPO, no de contenido): `servicio_esencial` devuelto como booleano (`false`/`true`) en vez de texto, y `evidencia` con valores `null`. Se corrigió pidiéndolo explícito en el prompt y normalizando SOLO esos dos deslices (`ingest._normalizar_tipos`) antes del esquema estricto; los campos desconocidos y todo el contenido siguen validándose igual (evidencia literal, sector del catálogo). La recomendación nunca fue descartada con el servicio real. Las ayudas del operador (contexto y patrones) se aceptaron 1/1 y 1/1 tras acotar el filtro de «promesa de hora» a verbos de llegada/envío futuro (`PROMESA_FUTURA`), porque el filtro general rechazaba fechas y horas pasadas legítimas («03:28», «en 72 horas»).

Hallazgos con el servicio real:
- **La API rechaza plazos menores de 10 s** (HTTP 400, «Minimum allowed deadline is 10s»). Por eso `MINIMO_UTIL_S = 10`: con menos tiempo restante en el presupuesto no se llama a Gemini.
- **Cuota gratuita**: cargar los datos de ejemplo con `AI_PROVIDER=gemini` interpreta ~20 reportes y agotó el límite por minuto (429) hacia la llamada 15.ª. Cargue los datos de ejemplo con `AI_PROVIDER=local` (`workflow.cargar_datos_demo` no lo fuerza; es un cambio para quien mantiene `workflow.py`).
- Un 429 suspende Gemini 60 s: el sistema cae a reglas y se recupera solo.
- La confianza declarada (0,75-0,95) cambió entre llamadas para el mismo texto (p. ej. severidad `media` vs `alta`): confirma que no es una probabilidad calibrada.

## Presupuesto de tiempo y peor caso (serverless)
Cada llamada tiene plazo `AI_TIMEOUT_SECONDS` (15 s) y, para errores transitorios (5xx/timeout/red), 1 reintento tras 1,5 s. Peor caso de UNA llamada: 15 + 1,5 + 15 = **31,5 s**. `analizar_reporte` hace 2 llamadas (clasificación y recomendación): **63 s sin presupuesto**.

Con presupuesto (`ia_gemini.presupuesto`):

| Operación | Presupuesto | Peor caso |
|---|---|---|
| `workflow_ia.analizar_reporte` | `orquestador.PRESUPUESTO_IA_S = 20` s (o `HIDRO360_IA_PRESUPUESTO_S`) | **~20 s** + cálculo determinista (<0,5 s). Si la 1.ª llamada agota 15 s, quedan 5 s (< 10 s) y la 2.ª no se intenta |
| `ingest.interpretar` (alta de un reporte) | `ingest.PRESUPUESTO_INTERPRETACION_S = 12` s | **~12 s** (sin reintento: no caben 10 s + pausa) |
| contexto de incidencia / patrones | `ia_operador.PRESUPUESTO_S = 12` s | **~12 s** |

Reglas: el plazo de cada llamada se recorta al tiempo restante pero nunca baja de 10 s (mínimo de la API); si quedan menos de 10 s no se llama y la etapa queda `reglas(fallback)` con motivo «presupuesto de tiempo agotado»; los presupuestos anidados mandan por el más estricto. **Para Vercel**: la función que llame a `analizar_reporte` o `crear_reporte` con Gemini necesita `maxDuration` ≥ 30 s (plan gratuito: 10 s por defecto, configurable hasta 60 s). Con un límite de 10 s use `HIDRO360_IA_PRESUPUESTO_S=5` (equivale a no usar Gemini en el análisis) o `AI_PROVIDER=local`. Prueba: `test_peor_caso_documentado_con_presupuesto` (reloj simulado).

## Qué puede hacer la IA y qué no (matriz de permisos)
| Acción | IA (Gemini, `ia_*`, `orquestador`, `ingest`) | Quién lo hace |
|---|---|---|
| Leer los datos que le entrega el código (sector, puntaje, horas, personas, conteos) | Sí (texto del ciudadano solo en la clasificación, con teléfonos/correos redactados) | — |
| Proponer la interpretación del texto (sector candidato, cifras con evidencia) | Propone; el código valida | `ingest` |
| Redactar texto (acción sugerida, resumen de contexto/patrones, borrador) | Sí, validado y con fallback | `ia_tareas`, `ia_operador` |
| Modificar, confirmar, rechazar o marcar duplicado un reporte | **No** | persona admin (`workflow`) |
| Calcular prioridad, capacidad o plan | **No** | código determinista |
| Crear o modificar usuarios, sesiones, tokens | **No** | `auth` / administrador |
| Crear una misión | **No** | solo `workflow_ia.decidir_recomendacion` (aprobar/modificar) por un admin humano |
| Enviar mensajes | **No** (solo borradores) | persona |
| Ejecutar migraciones, escribir en la base o en archivos | **No** | — |

Lo comprueba `tests/test_ia_robustez.py`: análisis AST de `ia_gemini`, `ia_tareas`, `ia_operador`, `orquestador` e `ingest` (no importan `workflow`, `workflow_ia`, `database`, `audit`, `auth` y no llaman a funciones de escritura); la única llamada a `crear_mision` con `recomendacion_id` está en `decidir_recomendacion`; y el flujo completo de IA deja idénticas las tablas de reportes, planes, misiones, usuarios y flota. También se prueba que 7 fallos de Gemini (clave inválida, modelo inexistente, timeout, 429, JSON inválido, campos extra, invención de sector/cisterna/litros) terminan en `reglas(fallback)` sin cambiar esas tablas ni la prioridad.

## Ayudas al operador (solo lectura, `@requiere_admin`)
```python
workflow_ia.resumen_contexto_reporte(reporte_id: int, usar_ia: bool = True) -> dict
  # {"datos": {"reporte": {...}, "duplicados_sugeridos": [{reporte_id, similitud, motivos}], "historial_sector": {...}},
  #  "resumen": {"texto": str, "origen": "gemini"|"reglas"|"reglas(fallback)", "motivo_fallback": str|None}, "nota": str}
workflow_ia.detectar_patrones(ventana_horas: int = 72, minimo_reportes: int = 3, usar_ia: bool = True) -> dict
  # {"parametros", "generado", "reportes_en_ventana",
  #  "patrones": [{"tipo": "sector_recurrente", "sector", "reportes", "sin_atender", "reporte_ids", "ventana_horas", "explicacion"}
  #             | {"tipo": "pico_horario", "hora", "reportes", "total_ventana", "reporte_ids", "ventana_horas", "explicacion"}],
  #  "resumen": {igual que arriba}, "nota": str}
```
API: `GET /api/v1/reportes/{id}/contexto?usar_ia=true` y `GET /api/v1/patrones?ventana_horas=72&minimo_reportes=3&usar_ia=true` (administrador; 404 si el reporte no existe; 422 fuera de rango: ventana 1-720, mínimo 2-50). Los patrones son conteos deterministas (cada uno trae su `explicacion` generada por código); la IA solo redacta `resumen`. Preséntelos al operador como indicios, no conclusiones. El texto original del ciudadano no se envía a la IA en estas ayudas.

## Observabilidad
Cada llamada deja `ia/gemini.<tarea>` con modelo, versión del esquema de salida (`esquema_salida`: `SalidaIA/2`, `SalidaRecomendacion/1`, `texto/1`), estado, duración, intentos, tokens (`uso: {entrada, salida, total}`) si el SDK los informa y, en errores, solo el tipo de excepción y el código HTTP. **No** incluyen texto del ciudadano, teléfonos/correos, claves ni imágenes (prueba: `test_trazas_con_modelo_esquema_tokens_y_sin_fugas`; con el servicio real se comprobó además que la clave, un teléfono y un correo de prueba no aparecen en las trazas ni en la base). En Vercel las trazas salen por stdout (ver `services/tracing.py`).

## Asistente con herramientas y bot de Telegram en local (demostración)

- **Variables** (nombres, sin valores): `GEMINI_API_KEY` (clave de Google AI Studio), `GEMINI_MODEL`, `AI_PROVIDER`,
  `TELEGRAM_BOT_TOKEN` (token de BotFather), `HIDRO360_BD=sqlite` (forza SQLite en la demostración).
- **Herramientas que puede invocar Gemini** (`services/asistente_ia.py`, solo funciones de `services/workflow`):
  `consultar_reporte` (lectura por código HID-XXXXXXXX), `avisos_publicos` (lectura) y `proponer_reporte`
  (solo prepara un borrador; **no guarda**). Guardar exige confirmación explícita: `confirmar_reporte`.
- **Orquestación** (`services/ia_gemini.conversar`): máximo 3 rondas de herramientas; cada llamada se valida
  (nombre autorizado, argumentos obligatorios, formato del código) antes de ejecutarse. Si Gemini falla, la
  interfaz y el bot responden con las reglas locales y lo declaran.
- **Pruebas:** `tests/test_asistente_ia.py` usa Gemini y Telegram SIMULADOS (sin cuota). La verificación con Gemini
  real se hizo con `python -m scripts.verificar_gemini` y una pregunta que invocó `consultar_reporte`.
- **Bot de Telegram en local (sondeo):** `python -m scripts.bot_telegram`. No necesita webhook ni dominio.
  Requiere un token nuevo; el que estuvo en el chat debe revocarse en BotFather.
