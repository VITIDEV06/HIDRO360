# Despliegue en la nube: Supabase + Vercel (y UI Flet aparte)

> Consulta de documentación oficial: **2026-10-17**. Los límites de plataformas cambian: revíselos antes de depender de ellos.
> Marcas de evidencia: **[doc]** verificado en documentación oficial · **[local]** probado en este equipo · **[no probado]** exige cuentas/plataforma real.
> **Nada de esto se ha desplegado todavía.** No existen URLs de producción; los marcadores `<...>` los rellena quien despliega.

## 1. Reparto de componentes (decisión)

```mermaid
flowchart LR
  subgraph Cliente
    N[Navegador / app de escritorio<br/>ciudadanía y administración]
    E[ESP32-S3 por cisterna<br/>HTTPS + X-Device-Token]
  end
  subgraph Host de procesos: Windows, Raspberry u otro
    UI[UI Flet portal + panel<br/>python app.py]
  end
  subgraph Vercel
    API[API FastAPI sin estado<br/>una Vercel Function]
  end
  subgraph Supabase
    PG[(PostgreSQL)]
    ST[(Storage privado<br/>fotos)]
    AU[Auth: identidad]
  end
  G[Gemini opcional]
  N -->|WebSocket de Flet| UI
  UI -->|DATABASE_URL pooler sesion 5432| PG
  UI -->|clave de servicio, solo servidor| ST
  E -->|HTTPS| API
  N -.->|clientes HTTP / integraciones| API
  API -->|DATABASE_URL pooler transaccion 6543| PG
  API --> ST
  API --> AU
  UI -.-> G
  API -.-> G
```

| Componente | Dónde | Por qué |
|---|---|---|
| Datos (PostgreSQL), fotos (Storage **privado**), identidad (Auth) | **Supabase** | Persistencia gestionada; Vercel no tiene disco de escritura persistente. |
| API FastAPI (público, administración, terminales ESP32) | **Vercel** (una Function) | HTTPS gratuito y sin estado: lo que necesitan las terminales y las integraciones. |
| UI Flet (portal público + panel) | **Proceso Python** en Windows/Raspberry/otro host de procesos, conectado a **la misma base** | Flet mantiene el estado de cada sesión **en memoria del servidor** y la comunica por WebSocket; ver §3. |
| Gemini | Llamado desde el servidor (UI o API) | La clave nunca sale del servidor. |

**Confirmo la propuesta con una corrección.** Que «Vercel no soporta WebSockets» **ya no es cierto**: desde 2026-07-23 hay soporte **beta** para WebSocket en funciones Python (ASGI) **[doc]** ([KB Vercel](https://vercel.com/kb/guide/do-vercel-serverless-functions-support-websocket-connections)). Aun así Flet **no debe** alojarse allí (§3).

## 2. Límites verificados de Vercel (consulta 2026-10-17)

| Tema | Valor | Fuente |
|---|---|---|
| Python soportado | **3.12 (defecto), 3.13 y 3.14**. Se elige con `pyproject.toml` (`requires-python`), `.python-version` o `Pipfile.lock`. Si no está definida o no se soporta, usa 3.12. **3.14 sí está soportado** (el proyecto local usa 3.14.7). Este repo fija **3.13** en `.python-version`. | [Python runtime](https://vercel.com/docs/functions/runtimes/python), [Python version](https://vercel.com/docs/functions/runtimes/python/python-version) |
| FastAPI/ASGI | Se detecta por la dependencia (`requirements.txt`, `pyproject.toml` o `Pipfile`). Entrada: variable `app` en `app.py`, `index.py`, `server.py`, `main.py`, `wsgi.py` o `asgi.py` (también dentro de `src/` o `app/`), **o** `tool.vercel.entrypoint = "modulo.ruta:app"` en `pyproject.toml`. La app completa es **una sola Function**. | [FastAPI en Vercel](https://vercel.com/docs/frameworks/backend/fastapi) |
| Carpeta `api/` | Sin preset de framework, cada `.py` de `api/` sería una función. **Con preset FastAPI el preset manda y `api/` no se parte en funciones.** | [api-directory](https://vercel.com/docs/functions/runtimes/python/api-directory) |
| Dependencias | `pyproject.toml` (con o sin `uv.lock`), `requirements.txt` o `Pipfile`. Sin tree-shaking: se empaqueta todo lo alcanzable; use `excludeFiles` en `vercel.json`. | Python runtime |
| Tamaño de paquete | **500 MB** sin comprimir para Python (250 MB para otros runtimes); «Large Functions» hasta 5 GB (beta). | [Limits](https://vercel.com/docs/functions/limitations) |
| Duración máxima | Con Fluid compute: **Hobby 300 s** (defecto y máximo); **Pro/Enterprise 300 s por defecto, 800 s máx.**, 1800 s beta. Se fija con `maxDuration`. Superarla devuelve 504. | Limits |
| Memoria | Hobby 2 GB/1 vCPU; Pro hasta 4 GB/2 vCPU. | Limits |
| **Cuerpo de petición/respuesta** | **4,5 MB** máximo; si no, **413 `FUNCTION_PAYLOAD_TOO_LARGE`**. | Limits |
| Descriptores de archivo | 1.024 compartidos entre ejecuciones concurrentes (afecta a conexiones a la base). | Limits |
| Sistema de archivos | Python usa el directorio base del proyecto como directorio de trabajo. La escritura persistente **no existe**; en Functions solo `/tmp` es escribible y es efímero. *(El texto «solo lectura salvo /tmp» es conocimiento general de AWS Lambda/Vercel; la página consultada no lo enuncia literalmente.)* | Python runtime / general |
| WebSockets | **Beta pública** en todos los planes y para Python ASGI (desde 2026-07-23). La conexión queda fijada a una instancia, se cierra al llegar a `maxDuration`, y el estado en memoria no se comparte entre instancias. | [KB WebSocket](https://vercel.com/kb/guide/do-vercel-serverless-functions-support-websocket-connections) |
| Regiones | Defecto **`iad1`** (Washington D. C.). Hobby: **una** región; Pro: 5 (la doc también cita 3 en un changelog); Enterprise: todas. Se fija con `regions` en `vercel.json`. | [Regiones](https://vercel.com/docs/functions/configuring-functions/region) |
| Variables de entorno | Por entorno **Production / Preview / Development** (más entornos personalizados), por rama en Preview; 64 KB en total por despliegue; **un cambio solo afecta a despliegues nuevos** (hay que redesplegar). Tipos *Config* y *Secret* (este último de solo escritura tras guardar). | [Env vars](https://vercel.com/docs/environment-variables) |
| Plan Hobby | Gratuito pero **solo uso no comercial/personal**; 1 000 000 invocaciones y 4 CPU-h activas incluidas; 100 despliegues/día; logs de ejecución de **1 hora**. | [Hobby](https://vercel.com/docs/plans/hobby) |
| Cron jobs | **No se usan** en este diseño; no se verificaron sus límites. | — |
| CLI | `vercel` 64.5.0 disponible por `npx` (comprobado). `vercel dev` para FastAPI requiere CLI ≥ 48.1.8. | FastAPI en Vercel |
| Dominios/Preview | Cada push a una rama distinta de la de producción crea un despliegue *Preview* con URL propia; la protección de despliegues (Vercel Authentication) puede bloquear el acceso anónimo a las previews: **las terminales ESP32 deben usar el dominio de producción**, no una preview. *(El comportamiento por defecto de la protección no se verificó; revíselo en Settings → Deployment Protection.)* | Hobby / general |

### Supabase (consulta 2026-10-17, [conexión](https://supabase.com/docs/guides/database/connecting-to-postgres)) **[doc]**
- **Conexión directa** (`db.<ref>.supabase.co:5432`): para backends persistentes (VM, contenedores). Es IPv6 por defecto.
- **Pooler compartido, modo sesión** (`aws-<n>-<region>.pooler.supabase.com:5432`): alternativa IPv4 para procesos persistentes (**útil para la UI en Windows con red solo-IPv4**).
- **Pooler compartido, modo transacción** (mismo host, puerto **6543**): para **serverless/edge**. **No admite sentencias preparadas** → `prepare_threshold=None` en psycopg. Usuario `postgres.<ref-proyecto>`. El pooler compartido es solo IPv4.
- Cargas estándar a Storage: la doc recomienda ≤ 6 MB por la vía estándar y TUS (reanudable) por encima.
- Comando CLI `supabase db push` (opciones `--db-url`, `--linked`, `--dry-run`, `--include-all`, `--password`) **[local: `--help` de supabase 2.121.0]**.

## 3. ¿Por qué la UI Flet no va en Vercel? y alternativas

- Flet web conserva por sesión el árbol de controles y el estado **en el proceso servidor**, y lo sincroniza por WebSocket. En Vercel: (a) el WebSocket es **beta**; (b) cada conexión se **cierra a `maxDuration`** (300 s en Hobby, 800 s en Pro): el usuario sería expulsado y perdería su formulario/sesión en cada reconexión; (c) las instancias no comparten memoria, por lo que tras una reconexión puede caer en otra instancia sin estado; (d) las funciones se facturan por memoria mientras haya conexiones abiertas. No se intentó, pero la combinación no es adecuada para una UI con estado. **Decisión: la UI Flet permanece como proceso persistente.**
- Hoy (demo del concurso) ese proceso es `python app.py` en el equipo del equipo o la Raspberry (`deploy/raspberry/`), apuntando a **la misma `DATABASE_URL` de Supabase** (pooler de **sesión** 5432, por ser un proceso persistente).
- **Alojar la UI en un servicio de procesos de terceros** (no desplegado; sin recomendar compras): servicios que ejecutan contenedores o procesos Python con soporte de WebSocket (p. ej. Google Cloud Run, Fly.io, Render, Railway, un VPS propio). *No se verificaron hoy sus planes gratuitos, límites ni condiciones*: consulte cada proveedor. Necesitarían un `Dockerfile` o comando `python app.py --web --host 0.0.0.0 --port $PORT --sin-navegador`, HTTPS delante (el canal Flet no está cifrado por sí mismo) y una sola instancia (el estado de sesión es local). Además la UI web carga CanvasKit/fuentes desde un CDN (el navegador necesita internet; `docs/DECISIONES.md` D-04).
- **Página estática mínima en Vercel**: opcional y no incluida. Con el preset FastAPI, una carpeta `public/` se sirve desde el CDN; podría ofrecer un aviso y enlaces. No aporta sin la UI y evita ocultar que la API sirve `/docs`; se omite hasta que exista una UI web propia.

## 4. Riesgos conocidos

| Riesgo | Detalle y mitigación |
|---|---|
| **Cold start** | La primera petición tras inactividad carga FastAPI, Pydantic, `google-genai` y psycopg (≈ 63 MB de paquetes **[local]**). Fluid compute reutiliza instancias y reduce arranques en frío, no los elimina. Las terminales ESP32 deben tolerar 1–5 s extra y reintentar (el endpoint de eventos es idempotente por `uid`). La latencia real no se midió. |
| **Gemini frente a `maxDuration`** | Peor caso por llamada de IA: `AI_TIMEOUT_SECONDS=15` × (1 + `AI_MAX_REINTENTOS=1`) = 30 s; el flujo tiene **dos etapas** → ≈ 60 s + base de datos. `vercel.json` fija **`maxDuration: 90`**. Para una API más ágil en Vercel conviene `AI_TIMEOUT_SECONDS=10` y `AI_MAX_REINTENTOS=0` (peor caso ≈ 20 s). Esperar la IA no consume CPU activa pero sí memoria aprovisionada. |
| **Conexiones a Postgres desde serverless** | Muchas instancias × pool = demasiadas conexiones. Usar el **pooler de transacción (6543)**, `prepare_threshold=None` y un pool **pequeño** por instancia (1–3); el límite de 1.024 descriptores de archivo es compartido. El código del pool es del agente de datos; este documento fija la política. |
| Estado en memoria | Límites de tasa del login/terminales y bloqueos por intentos viven **en memoria por instancia**: en Vercel cada instancia cuenta aparte, así que **debilitan** esa defensa. Sesiones: deben persistirse en la base (agente de datos/seguridad). Para producción real: límite de tasa en el borde (Vercel Firewall) o en Postgres. |
| Disco de solo lectura/efímero | Sin SQLite, sin `media/`, sin `logs/trazas.jsonl`: en Vercel `HIDRO360_STORAGE=supabase` y `HIDRO360_TRACE=stdout` son **obligatorios** (los logs de ejecución se conservan 1 h en Hobby; las trazas con cadena de hashes en stdout **no** sustituyen a la auditoría de la base). |
| **Fotos y límite de 4,5 MB** | Ver §5. |
| `/docs` y `/openapi.json` públicos | FastAPI los expone por defecto; revelan el contrato (no secretos). Decisión del agente backend (`docs_url=None` en producción). |
| Hobby = no comercial | Para un piloto con usuarios reales de una institución use un plan que lo permita. |
| Vercel no es entorno de hardware | Las terminales ESP32 solo se probaron simuladas; HTTPS en el firmware (certificados raíz) no está verificado. |

## 5. Fotos y límite de cuerpo de 4,5 MB

Hoy el API recibe **fotos en JSON/base64** (hasta 3 × 3 MB). Base64 aumenta el tamaño en 4/3:

| Escenario | Binario total | En base64 | ¿Cabe en 4,5 MB (4 500 000 B)? |
|---|---|---|---|
| 3 fotos × 3 MB (límite actual de `config.py`) | 9,4 MB | 12,6 MB | **No** |
| 1 foto × 3 MB | 3,1 MB | 4,2 MB | Sí, pero sin margen para el texto |
| Máximo teórico con 3 fotos | ≈ 3,37 MB | 4,5 MB | límite |
| **3 fotos × 1 MiB (recomendado)** | 3,15 MB | 4,19 MB + ≈ 3 KB de JSON | **Sí (≈ 300 KB de margen)** |

Recomendaciones (cambios que corresponden a otros agentes/coordinador):
1. **Tope por foto en la ruta HTTP de Vercel: 1 MiB** (`MAX_BYTES_FOTO`), o un tope **agregado** de 3,2 MB por petición. Los clientes deben **reducir/comprimir** la foto (lado largo ≈ 1280 px, JPEG ≈ 80 %) antes de enviarla.
2. **Alternativa robusta:** subida **directa a Supabase Storage** con URL firmada emitida por la API (la API solo recibe metadatos y verifica la firma/tipo al registrar el reporte). Evita el límite y el coste de procesar binarios en la función. Requiere endpoints nuevos y revalidar la firma/tamaño en el servidor. *No verificado con documentación: la página de «standard uploads» consultada no describe las URL firmadas de subida; confirme en la guía de Supabase Storage antes de implementarlo.*
3. La **UI Flet no se ve afectada**: llama a `services.workflow` en su propio proceso y sube a Storage con la clave de servicio (sin pasar por Vercel).
4. `MAX_CUERPO_BYTES = 16 MB` de `api/main.py` es irrelevante bajo Vercel (la plataforma responde 413 antes).

## 6. Archivos de configuración de despliegue (este repositorio)

| Archivo | Función |
|---|---|
| `pyproject.toml` | `[tool.vercel] entrypoint = "api.main:app"` (la raíz tiene `app.py` de Flet, sin `app` ASGI) y **dependencias solo de la API** (sin flet ni pytest). |
| `uv.lock` | Vercel instala con `uv sync --locked` **[local]**. **Regenere con `uv lock` cada vez que cambie `dependencies`** (la prueba `tests/test_despliegue.py` lo detecta). |
| `.python-version` | `3.13` (Vercel soporta 3.12–3.14). |
| `vercel.json` | Región `iad1`, `maxDuration: 90`, `excludeFiles` (tests, docs, interface, firmware…), cabeceras de seguridad (HSTS, nosniff, DENY, no-referrer, no-store). El backend añade además CSP y Permissions-Policy. |
| `.vercelignore` | Lo que no se sube con `vercel deploy`. |
| `requirements.txt` / `requirements-servidor.txt` | **Sin cambios de contrato**: `pip install -r requirements.txt` sigue instalando el conjunto completo de escritorio. Vercel **no** los usa para instalar (usa `pyproject.toml`+`uv.lock`; en la compilación local eligió pyproject cuando coexisten **[local]**). |

**Reparto de dependencias (decisión):** se evitó mover `requirements.txt` (la guía actual `pip install -r requirements.txt` y la Raspberry siguen igual). La lista de la API vive en `pyproject.toml`; una prueba comprueba que cada paquete esté también en `requirements-servidor.txt`, y que no figuren flet ni pytest.

## 7. Variables de entorno por destino

`S` = secreta (nunca en el repositorio, el navegador, el firmware, capturas ni logs). En Vercel use el tipo *Secret* para las `S`.

| Variable | Vercel Production | Vercel Preview | UI Flet (host propio) | S |
|---|---|---|---|---|
| `HIDRO360_ENV` | `prod` | `dev` | `prod` | no |
| `DATABASE_URL` | pooler **6543** del proyecto de producción | pooler 6543 de un proyecto **de pruebas** | pooler **sesión 5432** o directa | **sí** |
| `HIDRO360_AUTH` | `supabase` (o `local`) | igual | igual | no |
| `HIDRO360_STORAGE` | `supabase` | `supabase` | `supabase` (o `local`) | no |
| `SUPABASE_URL` | URL del proyecto | idem (pruebas) | idem | no |
| `SUPABASE_ANON_KEY` | clave pública | idem | idem | no (pública por diseño) |
| `SUPABASE_SERVICE_ROLE_KEY` | clave de servicio | idem | idem | **sí** |
| `SUPABASE_JWT_SECRET` | solo si el proyecto usa secreto HS256 | idem | idem | **sí** |
| `SUPABASE_STORAGE_BUCKET` | `reportes-fotos` | idem | idem | no |
| `HIDRO360_CORS_ORIGINS` | vacío (o dominios exactos) | vacío | no aplica | no |
| `HIDRO360_TRACE` | **`stdout`** | **`stdout`** | ruta de archivo | no |
| `HIDRO360_API_TOKEN` | aleatorio de 64 hex | distinto | solo si la UI llama a la API | **sí** |
| `HIDRO360_DEVICE_TOKEN` | solo si se usa la pantalla heredada | — | — | **sí** |
| `GEMINI_API_KEY` | opcional | opcional (o ausente) | opcional | **sí** |
| `AI_PROVIDER`, `GEMINI_MODEL` | `auto`, `gemini-3.5-flash-lite` | igual | igual | no |
| `AI_TIMEOUT_SECONDS`, `AI_MAX_REINTENTOS` | `10`, `0` (recomendado) | igual | `15`, `1` | no |
| `HIDRO360_DB`, `HIDRO360_MEDIA_DIR` | **no definir** | **no definir** | solo con SQLite/almacenamiento local | no |

Precedencia: `HIDRO360_*` gana a `AGUA_CLARA_*` (alias obsoletos). Las **pruebas no leen `.env`** (`pytest`/`HIDRO360_ENV=test`). Los cambios de variables en Vercel solo se aplican a **despliegues nuevos**.
Ninguna `S` debe ir en `NEXT_PUBLIC_*`/variables expuestas al navegador (no existen en este proyecto) ni en `firmware/` (las terminales solo llevan su `X-Device-Token`).

## 8. Procedimientos (acciones manuales del usuario marcadas con [CUENTA])

### 8.1 Supabase
1. **[CUENTA]** Cree el proyecto en <https://supabase.com/dashboard>. Elija la región más cercana a su función Vercel (por defecto `iad1` = AWS us-east-1 / «East US (North Virginia)») y una **contraseña de base fuerte** (guárdela en un gestor, no en Git). No contrate planes de pago sin decidirlo usted.
2. **[CUENTA]** *Authentication → Sign In / Providers*: **desactive el registro abierto** (*Allow new users to sign up*) y las proveedoras que no use. Los administradores se crean con `scripts.crear_admin`/invitación, no por autoservicio. *(El nombre exacto del ajuste puede variar: compruebe el panel.)*
3. **[CUENTA]** *Storage*: cree el bucket **`reportes-fotos` PRIVADO** (sin acceso público).
4. **[CUENTA]** *Project Settings → API*: copie `Project URL`, la clave `anon` y la de **servicio** (`service_role`/secret) a un gestor; nunca al chat/Git. *Project Settings → Database / botón Connect*: copie la cadena del **pooler**. Para Vercel use modo **transacción** (puerto **6543**); para la UI y las migraciones, **sesión (5432)** o directa.
5. Migraciones (`supabase/migrations/*.sql`, generadas por el agente de datos). Opción A (sin CLI, desde el repo):
   ```powershell
   $env:DATABASE_URL = "<cadena sesion/directa 5432>"   # solo en esta terminal
   python -m scripts.aplicar_migraciones --listar
   python -m scripts.aplicar_migraciones
   ```
   Opción B (CLI): `npx supabase@latest login` · `npx supabase@latest link --project-ref <ref>` · `npx supabase@latest db push --dry-run` · `npx supabase@latest db push`. **No** aplique migraciones por el pooler de transacción.
6. Migrar datos existentes de SQLite (si se conservan): ver `docs/BASE_DE_DATOS.md` (script a cargo del agente de datos; haga antes una copia de `agua_clara.db`).
7. Primer administrador, con `DATABASE_URL` y `HIDRO360_AUTH` definidos en la terminal: `python -m scripts.crear_admin`.

### 8.2 Vercel
1. **[CUENTA]** Entre en <https://vercel.com> (GitHub). *Add New → Project → Import* el repositorio `HackathonFISC/equipo-06` (si no tiene acceso, pídalo a la organización o haga *fork*). Rama de producción: la que decida el equipo (`main`). Framework: **FastAPI** (autodetectado); *Root Directory* = raíz.
2. En *Settings → Environment Variables* cargue las de §7 columna *Production* (tipo *Secret* para las `S`) y las de *Preview* con valores **distintos** (proyecto Supabase de pruebas).
3. *Settings → Functions*: confirme región (`iad1`) y que Fluid compute esté activo.
4. Despliegue: *Deploy* (o `git push`). Con CLI: `npx vercel@latest login` → `npx vercel@latest link` → `npx vercel@latest --prod`.
5. Verifique (sustituya `<URL>` por el dominio de producción; **no inventar**):
   ```bash
   curl -s https://<URL>/api/v1/salud                    # {"ok":true,"persistencia":"postgres",...}
   curl -s https://<URL>/api/v1/publico/avisos
   curl -s -o /dev/null -w "%{http_code}\n" https://<URL>/api/v1/reportes   # 401 sin credenciales
   curl -s -X POST https://<URL>/api/v1/publico/reportes -H "Content-Type: application/json" \
        -d '{"texto":"Prueba de despliegue: sin agua en Vista Alegre desde ayer"}'   # devuelve codigo HID-...
   ```
   En `/salud` compruebe `persistencia: postgres`, `almacenamiento_fotos: supabase`, `autenticacion`. Borre el reporte de prueba desde el panel.
6. Terminales ESP32: la URL base del firmware es el dominio de **producción** y cada vehículo usa su token (`python -m scripts.registrar_dispositivo CIS-01` con `DATABASE_URL`).

### 8.3 UI Flet contra Supabase (host propio)
```powershell
.\scripts\dev.ps1 instalar        # crea .venv, instala, copia .env.example a .env (sin pisar)
# edite .env: DATABASE_URL (sesion 5432), HIDRO360_STORAGE=supabase, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY ...
.\scripts\dev.ps1 admin           # primer administrador
.\scripts\dev.ps1 ui              # o: web
```
Raspberry: añada las mismas variables a `/etc/hidro360/hidro360.env` y `sudo systemctl restart hidro360-api hidro360-ui`.

### 8.4 Actualizar versiones
- Código: `git push` a la rama de producción → Vercel despliega solo; las ramas generan *Preview*.
- Dependencias de la API: edite `pyproject.toml` **y** `requirements-servidor.txt`, ejecute `uv lock`, `pytest -q`, y suba ambos.
- Esquema: nueva migración `supabase/migrations/<fecha>_<tema>.sql`; **primero** `db push` (compatible hacia atrás), **después** el despliegue del código.
- Secretos: rótelos en la consola de Supabase/Google; actualice la variable en Vercel y **redespliegue**.

### 8.5 Rollback
- **Código:** en Vercel, *Deployments →* el despliegue estable anterior *→ Promote to Production* (o *Instant Rollback*). *(Disponibilidad exacta según plan: no verificado.)*
- **Esquema/datos (Supabase):** las migraciones son hacia adelante; para revertir, escriba una **migración nueva** que deshaga el cambio (forward-fix). Antes de migraciones destructivas haga copia lógica: `pg_dump` con la cadena directa (necesita `psql`/`pg_dump`, no instalados aquí) o la copia que ofrezca su plan de Supabase *(no verificado cuáles incluye su plan)*. Restaurar: restaurar la copia en un proyecto nuevo y apuntar `DATABASE_URL`.
- **Raspberry/SQLite:** `deploy/raspberry/backup.sh` y restauración descritas en `docs/INSTALACION.md` §9.5.
- Compromiso de una clave: rótela primero, luego revise `python -m scripts.revisar_secretos` y el historial de Git.

### 8.6 Acciones que solo puede hacer el usuario (una por una)
1. Crear cuenta/proyecto Supabase y elegir región y contraseña de base.
2. Desactivar el registro abierto y crear el bucket privado.
3. Obtener y guardar `service_role`, `anon`, cadena del pooler.
4. Aplicar migraciones con su `DATABASE_URL` (o `supabase login` + `link`).
5. Crear el primer administrador.
6. Iniciar sesión en Vercel e importar `HackathonFISC/equipo-06` (requiere permiso sobre el repositorio y la organización).
7. Cargar variables por entorno (Production/Preview) y desplegar.
8. Decidir dominio personalizado (no contratado ni cambiado por este trabajo).
9. Obtener `GEMINI_API_KEY` si se quiere IA (opcional).
10. Compilar/flashear el firmware con la URL de producción.

## 9. Qué se probó y qué no

| Comprobación | Estado |
|---|---|
| `import api.main` en un venv limpio con solo las dependencias de `pyproject.toml` (Python 3.14.7); no carga `flet` | **[local]** |
| `uvicorn api.main:app` con SQLite temporal y `HIDRO360_ENV=test`: `/api/v1/salud` 200, `/publico/avisos` 200, `/reportes` sin credenciales 401 | **[local]** |
| `vercel build` (CLI 64.5.0) en una copia limpia: resuelve el entrypoint `api.main:app`, instala desde `uv.lock` para `x86_64-unknown-linux-gnu`, aplica `excludeFiles` y `maxDuration: 90`, genera una sola Function y la regla `/(.*)` → función, y las cabeceras. Requirió simular `.vercel/project.json` con `framework: fastapi` (sin login) | **[local]** |
| Tamaño del paquete: ≈ 63 MB de dependencias Linux (límite 500 MB) | **[local]** |
| Ejecución real en Vercel, cold starts, latencia con Supabase, subida de fotos, ESP32 sobre HTTPS | **[no probado]** |
| `supabase db push` / migraciones contra un Supabase real | **[no probado]** (no hay proyecto) |
| Pruebas de despliegue: `pytest tests/test_despliegue.py` | **[local]** |
