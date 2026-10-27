# Decisiones técnicas (2026-10-16)

> **Nota de documentación (17-oct-2026):** las decisiones que citan `interface/streamlit_app.py`, `interface/estilos.py` o `tests/test_ui.py` son históricas: esa interfaz se sustituyó por Flet y esos archivos ya no existen.

> Nombre oficial del producto: **HIDRO360** (TEAM VIFY AI); ver D-02.

## D-01 Migrar de Streamlit a Flet — **adoptada**
- **Evidencia de viabilidad**: `flet 1.0.4` instala y se importa en Python 3.14.7 (el del proyecto); se probó una app de humo en navegador (render, eventos, diálogos, Dropdown, DataTable); la app de escritorio arrancó una ventana (entonces titulada «Agua Clara AI»; hoy HIDRO360) (`flet-desktop`; su cliente se descarga una vez a `~/.flet/client`, ver D-04); 13 pruebas de interacción y la revisión visual en navegador pasaron. Windows: sin dependencias nativas extra. Raspberry: sin probar.
- **Por qué**: interfaz personalizable con la identidad del prototipo (Streamlit limita el diseño), cliente de escritorio sin servidor, y la UI se ejecuta en el mismo proceso que `services/workflow.py`.
- **Coste/riesgos asumidos**: API de Flet 1.0 joven (se fija `~=1.0.4`); no hay un `AppTest` oficial (se prueban páginas y manejadores directamente); el modo web necesita internet en el navegador (D-04).
- **Retirada de Streamlit** (hecha después de verificar): se eliminaron `interface/streamlit_app.py`, `interface/estilos.py`, `.streamlit/` y `tests/test_ui.py`; se quitaron `streamlit` y `pandas` de `requirements.txt` y del venv. Cobertura verificada en el momento con `pytest -q` (las 13 pruebas de interacción de Flet cubrían las mismas acciones que la retirada de Streamlit). Cambio de comportamiento: el mapa de la página de duplicados (`st.map`) pasó a un **mapa esquemático** en el Panel general (sin cartografía).
- Se mantiene **FastAPI** como backend para dispositivos/integraciones; la UI **no** pasa por HTTP porque comparte proceso y lógica (un salto de red no aporta y añade fallos).

## D-02 Identidad visual: logo y prototipo
- Logo: JPEG, fondo blanco, **sin transparencia**, sin texto (el único activo vigente en el repositorio es `assets/logo.jpeg`). Prototipo: una página «Hidro360 | Prototipo» con el logo incrustado en base64 (contiene la paleta y el diseño de referencia; **no está versionada** en el repositorio, ver `docs/DISENO_UI.md`).
- **Nombre — decidido por el usuario: el nombre oficial es HIDRO360** (equipo TEAM VIFY AI). Ya no está pendiente. Antes los PDF de propuesta y el código decían «Agua Clara AI»; los PDF de `proyecto_info/` conservan el nombre histórico en su texto. Por compatibilidad se mantienen los **nombres de archivo del logo y del prototipo**, los prefijos `AGUA_CLARA_` de las variables de entorno (la renombración la decide el coordinador), el archivo local `agua_clara.db` y las rutas/funciones del código. El logo se usa sin alterarlo (no contiene texto). Ni `LOGO/` ni `PROTOTIPO/` están presentes en el repositorio actual.
- Como el JPEG no tiene transparencia, el logo solo se usa sobre superficies blancas (barra lateral, bienvenida); el hero oscuro del Panel no lo lleva (el prototipo usaba `mix-blend-mode: screen` y no se verificó que Flet lo replique). El activo vigente es `assets/logo.jpeg`. Detalle: `docs/DISENO_UI.md`.

## D-03 Gemini como proveedor principal
`google-genai` (SDK oficial; `google-generativeai` está en desuso) con `AI_PROVIDER=auto` (Gemini solo con clave). Se retiraron las variables `INTERPRETE`/`LLM_*` (proveedor «compatible OpenAI») para no duplicar configuración. Ver `docs/IA_GEMINI.md`.

## D-04 Modo web de Flet y fuentes
`ft.run(..., no_cdn=True)` deja la página en blanco con flet-web 1.0.4 (las fuentes no están en el paquete: 404 verificado). Se usa el modo por defecto, que **carga CanvasKit/fuentes desde la CDN: el navegador cliente necesita internet**. Los datos siguen siendo locales. Para uso sin internet, use la **app de escritorio** (`python app.py`): solo necesita internet **una vez** para descargar el cliente de Flet a `~/.flet/client` (comprobado: el paquete `flet-desktop` pesa ~100 KB y el cliente se crea al primer arranque). Para una Raspberry en una red sin salida a internet queda pendiente evaluar la app de escritorio en la propia Pi o servir los recursos localmente.

## D-05 Carpeta `PANTALLA_DE_MONITOREO` — **retirada tras verificar**
- Contenido: un sketch `PANTALLA_DE_MONITOREO.ino` (parpadeo del LED RGB, pin 42) y `libraries/` (354 MB, 5564 archivos).
- Significado: era el espacio de trabajo de Arduino para la pantalla Hosyond ESP32-S3; **no** era una interfaz de monitoreo del servidor. El trabajo real del ESP32 vive en `firmware/esp32s3_monitor/` (consulta la API; nunca SQLite).
- `libraries/` era una copia **idéntica por SHA-256 (5564/5564 archivos)** de `C:\Users\Victor Montes\Documents\Arduino\libraries`, que es donde Arduino IDE las lee; no había referencias en el código. Se eliminó solo tras esa comprobación.
- El sketch del LED se **conservó** como `firmware/pruebas/led_rgb/led_rgb.ino` (copia verificada con `cmp`; es una prueba válida de la placa).
- `Demo/` (51 MB, ejemplos del fabricante) **no se tocó**: sigue sin versionar; decide el usuario.

## D-06 Skills y `AGENTS.md`
Entorno: Claude Code → skills en `.claude/skills/<nombre>/SKILL.md`; `CLAUDE.md` importa `AGENTS.md`. Seis skills con responsabilidades distintas (arquitectura, Flet, backend/datos, IA/Gemini, pruebas, hardware); seguridad y auditoría viven dentro de backend e IA para no duplicar. **No verificado en esta sesión**: que se descubran (ocurre al abrir el proyecto en una sesión nueva).

## D-07 Base de datos
Se mantiene SQLite con tablas documento (JSON validado por Pydantic): migraciones por campos con valor por defecto; ver `docs/BASE_DE_DATOS.md`.

## D-08 Reparto nube: Supabase + Vercel (API) + UI Flet como proceso (2026-10-17)
- **Decisión:** Supabase = PostgreSQL + Storage privado + Auth; Vercel = solo la API FastAPI (una Function, `pyproject.toml` con `tool.vercel.entrypoint = "api.main:app"`); la UI Flet sigue siendo un proceso persistente (Windows/Raspberry/otro host) conectado a la misma base.
- **Evidencia:** la UI Flet guarda el estado de sesión en el servidor y usa WebSocket; Vercel soporta WebSocket en Python solo en **beta**, cierra cada conexión al llegar a `maxDuration` (300 s Hobby / 800 s Pro) y no comparte memoria entre instancias. Fuentes y fecha en `docs/DESPLIEGUE.md` §2-3. Python 3.14 **sí** está soportado en Vercel (3.12 por defecto, 3.13, 3.14).
- **Alternativas descartadas por ahora:** UI en Vercel (arriba); reescribir la UI como web estática (fuera de alcance); alojar la UI en un servicio de procesos de terceros (viable, no verificado ni contratado).
- **Dependencias:** se conserva `requirements.txt` (conjunto completo; la guía de instalación no cambia). La función de Vercel se instala desde `pyproject.toml` + `uv.lock` (solo API, ≈ 63 MB), mantenidos en sincronía con `requirements-servidor.txt` por `tests/test_despliegue.py`.
- **Fotos:** el límite de 4,5 MB por petición obliga a un tope de 1 MiB por foto en la ruta HTTP o a subida directa a Storage (§5 de `docs/DESPLIEGUE.md`).
- **Riesgos aceptados:** arranque en frío, estado en memoria (límites de tasa) por instancia, Hobby solo no comercial. **No desplegado ni probado en las plataformas reales.**

## D-09 Secretos y perfiles
`.env.example` documenta todas las variables con perfiles (local, pruebas, producción) y marca las secretas. `scripts/revisar_secretos.py` (y `tests/test_despliegue.py`) escanea los archivos versionados en busca de claves de Google, JWT, cadenas PostgreSQL con contraseña, `service_role` y tokens largos. Las pruebas no leen `.env`. Un hallazgo exige rotar la clave, no solo borrarla.
