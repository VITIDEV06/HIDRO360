# Base de datos (SQLite local · PostgreSQL/Supabase)

`data/database.py` es una **fachada con dos motores intercambiables**; `services/` solo ve su API (`insertar, actualizar, obtener, listar, ultimo, borrar, vaciar, transaccion, conexion, registrar_evento_limite, limpiar_limites`) y no sabe qué hay debajo.

| Motor | Se elige cuando | Archivo | Para qué |
|---|---|---|---|
| SQLite (stdlib) | **por defecto** (`DATABASE_URL` vacía) | `data/_sqlite.py` · `agua_clara.db` (`HIDRO360_DB`) | demo sin internet, Raspberry, pruebas |
| PostgreSQL (psycopg 3) | `DATABASE_URL` definida (`config.modo_postgres()`) | `data/_postgres.py` · `supabase/migrations/*.sql` | despliegue en Supabase + Vercel |

Misma semántica y firmas en ambos: `tests/test_database_contrato.py` ejecuta **las mismas aserciones** sobre los dos. El código común (lista blanca de tablas, excepciones) está en `data/_comun.py`.

## Tablas
Todas guardan el documento completo (modelo Pydantic) y, en PostgreSQL, además **columnas tipadas derivadas del documento** (`datos jsonb NOT NULL` es la fuente de verdad del contenido; el adaptador rellena las columnas en cada escritura, así que nunca divergen). Las tablas son las mismas en SQLite (`id` + `datos` TEXT) y PostgreSQL.

| Tabla | Clave | Columnas tipadas (PostgreSQL) | Notas |
|---|---|---|---|
| `sectores` | `nombre` | población, vulnerabilidad, lat/lng, ficticio | |
| `cisternas` | `id` texto | capacidad, litros, estado, sector_base, ficticio | |
| `reportes` | `id` IDENTITY | estado, sector, severidad, fecha_creacion, **codigo_seguimiento UNIQUE**, clave_idempotencia, canal, duplicado_de, lat/lng, ficticio | índices por estado, sector, fecha, idempotencia |
| `planes` | `id` IDENTITY | estado, version, fecha_creacion, metodo | |
| `decisiones_duplicado` | `id` IDENTITY | reporte_id, otro_id, es_duplicado, responsable, fecha | |
| `auditoria` | `id` IDENTITY | fecha, responsable, accion, entidad, entidad_id, **hash_previo UNIQUE**, **hash UNIQUE**, `datos_texto` | append-only; ver «Auditoría» |
| `usuarios` | `id` texto (= usuario) | usuario, rol, activo, creado | no se borra al recargar la demo |
| `sesiones` | `id` texto = SHA-256 del token | usuario, **expira** | índice por expiración |
| `limites` | `id` IDENTITY | clave, **instante** (servidor) | índice (clave, instante); límites de tasa compartidos entre instancias |
| `fotos` | `id` texto | reporte_id, tipo_mime, bytes, sha256, fecha | solo metadatos; no se borra al recargar la demo |
| `dispositivos` | `id` = `cisterna_id` | cisterna_id (FK), **token_hash UNIQUE**, activo, ultima_conexion | no se borra al recargar la demo |
| `misiones` | `id` IDENTITY | cisterna_id (FK), estado, plan_id, recomendacion_id, creada, ficticio | |
| `eventos_mision` | `id` IDENTITY | mision_id (FK), **uid UNIQUE si resultado='aplicado'**, cisterna_id, tipo, resultado, parada_orden, fecha_servidor | |
| `recomendaciones` | `id` IDENTITY | reporte_id (FK), estado, proveedor, fecha, mision_id | |
| `schema_migraciones` | `version` | checksum | control del aplicador sin CLI |

`CHECK` de estados (`reportes.estado`, `planes.estado`, `cisternas.estado`, `misiones.estado`, `recomendaciones.estado`, severidad/vulnerabilidad, `fotos.tipo_mime`, `eventos_mision.tipo/resultado`, `usuarios.rol`): las columnas admiten NULL (los documentos antiguos o incompletos se leen), pero un valor presente fuera del catálogo se rechaza.

`vaciar()` sin argumentos limpia solo `TABLAS` (sectores, cisternas, reportes, planes, decisiones, auditoría, misiones, eventos, recomendaciones) y **reinicia los contadores** (en PostgreSQL con `setval`). `usuarios`, `sesiones`, `dispositivos`, `fotos` y `limites` sobreviven al reinicio de la demo.

### Claves foráneas (criterio)
| FK | Acción | Por qué |
|---|---|---|
| `misiones.cisterna_id → cisternas` | `ON DELETE CASCADE` | una misión no existe sin su vehículo; recargar la demo borra las misiones de todos modos |
| `eventos_mision.mision_id → misiones` | `ON DELETE CASCADE`; **NULL** si el evento se rechazó por apuntar a una misión inexistente | el backend registra los eventos rechazados (con la misión declarada en `datos`); el adaptador solo enlaza si la misión existe |
| `recomendaciones.reporte_id → reportes` | `ON DELETE CASCADE` | la recomendación deriva del reporte |
| `dispositivos.cisterna_id → cisternas` | `DEFERRABLE INITIALLY DEFERRED`, sin acción | las terminales **sobreviven** a «Cargar datos de ejemplo» (borra y recrea cisternas en una transacción); solo falla el COMMIT si una terminal queda apuntando a una cisterna inexistente |
| sin FK: `fotos`/`decisiones_duplicado` → `reportes`, `reportes.sector` | — | deben sobrevivir al reinicio de la demo; las fotos pueden quedar huérfanas por diseño (la migración lo avisa) |

### Zona horaria
Los modelos usan `datetime` **ingenuo** en hora local de Panamá (UTC-5, sin horario de verano). Estrategia:
1. El documento `datos` conserva el **texto original sin cambios**: la lectura devuelve exactamente lo escrito (ida y vuelta sin pérdida; probado con y sin desplazamiento, con microsegundos).
2. La columna `timestamptz` (UTC internamente) se deriva así: ingenuo ⇒ se interpreta en `HIDRO360_TZ` (por defecto `America/Panama`; si falta la base `tzdata` se usa UTC-5 fijo); con desplazamiento o `Z` ⇒ se respeta. Un texto no interpretable deja la columna en NULL (el documento no se toca).
3. Consultas por fecha/orden usan la columna; **mostrar** fechas sigue siendo trabajo del modelo. Recomendación para el backend en serverless (el reloj del servidor suele ser UTC): generar `datetime.now(ZoneInfo("America/Panama")).replace(tzinfo=None)` para mantener el contrato ingenuo-local; si se genera hora UTC ingenua, defina `HIDRO360_TZ=UTC` (no mezclar ambas en una misma base).

## Transacciones y concurrencia
`db.transaccion()` es **atómica y reentrante**: cambio de negocio + auditoría + traza van dentro; si algo falla se revierte todo.
- SQLite: `BEGIN IMMEDIATE` (serializa escritores).
- PostgreSQL: `BEGIN` + `pg_advisory_xact_lock(<constante>)` al abrir la transacción **raíz**. Es el equivalente de `BEGIN IMMEDIATE`: la auditoría encadena hashes (cada entrada incluye el hash de la anterior), así que quien lee «la última entrada» y escribe la siguiente debe estar serializado o dos transacciones bifurcarían la cadena. La cerradura es de transacción (se libera sola, también si la conexión muere), no bloquea lecturas y funciona con el pooler. Segunda barrera: `UNIQUE(hash_previo)` hace que una bifurcación **falle** en vez de corromper. Las escrituras sueltas (`insertar` fuera de `transaccion()`) también toman la cerradura.
- `registrar_evento_limite(clave, ventana_s)`: alta + recuento atómicos; en PostgreSQL se serializa **por clave** (no con la cerradura de negocio) y usa el reloj del servidor, de modo que varias instancias comparten el límite. Dentro de una `transaccion()` se une a ella (si esa transacción se revierte, el evento también).

### Compatibilidad con Supabase (pooler, serverless)
- Conectar con el **pooler en modo transacción (puerto 6543)** es válido: `prepare_threshold=None` (sin sentencias preparadas), todo el estado de sesión es `SET LOCAL`/`set_config(..., true)` dentro de la transacción (probado: tras COMMIT no queda nada), sin LISTEN/NOTIFY.
- **Migraciones/DDL y `scripts.migrar_sqlite_a_postgres`**: usar la conexión directa (5432) o de sesión, no el pooler de transacción.
- SSL: `sslmode=require` forzado para cualquier servidor no local (aunque la cadena diga `disable/prefer`). `connect_timeout=10 s`; por transacción: `statement_timeout=15 s`, `lock_timeout=10 s`, `idle_in_transaction_session_timeout=30 s`.
- Conexiones cortas; reutilización opcional y acotada (`HIDRO360_DB_REUSO_S`, defecto 30 s; `0` = una conexión nueva por transacción; `HIDRO360_DB_REUSO_MAX`, defecto 4 inactivas). No afecta a la corrección: si una conexión guardada murió, se reabre y se reintenta el inicio de la transacción.
- Errores: `ErrorBaseDeDatos` (y `ErrorConexionBD`, `ErrorIntegridadBD`, `EsquemaNoAplicado`) con mensajes fijos: **nunca** incluyen la cadena de conexión, usuario, servidor ni contraseña (probado). `ErrorIntegridadBD` solo nombra tabla/restricción/columna del esquema, no valores.
- `HIDRO360_DB_SCHEMA` (**solo pruebas**) fija un esquema por prueba. En producción no se define.

## Auditoría
`services/audit.py` encadena SHA-256. En PostgreSQL:
- trigger `auditoria_sin_modificar`/`auditoria_sin_truncar`: **rechaza UPDATE, DELETE y TRUNCATE para cualquier rol**, salvo DELETE/TRUNCATE dentro de `vaciar(["auditoria"])` (reinicio de la **demo**, que abre una excepción local de transacción y **se niega con `HIDRO360_ENV=prod`**).
- `datos_texto` guarda el JSON exacto que se hasheó: `jsonb` normaliza el orden de claves y el formato numérico (p. ej. `1e22`), y la verificación no debe depender de eso. Las lecturas de `auditoria` usan `datos_texto`.
- **Qué NO impide**: un superusuario o el dueño de la tabla puede desactivar/borrar el trigger; cualquier rol que pueda ejecutar `set_config` podría abrir la excepción de reinicio. La barrera real para el rol de la aplicación (`hidro360_app`) es que no tiene privilegios UPDATE/DELETE/TRUNCATE. Detectar alteraciones hechas con control total de la base es trabajo de la cadena de hashes (`verificar_integridad()`), que no detecta truncar el final.

## Row Level Security
Toda la lógica y los permisos viven en el **backend**, que se conecta con un rol de servidor. Migración `…03_seguridad_rls.sql`:
- **RLS activada en todas las tablas**; **ninguna política para `anon`/`authenticated`**; además `REVOKE ALL` (tablas, secuencias, funciones y privilegios por defecto) a esos roles y a `PUBLIC`. Doble barrera: sin privilegio ni llegan a RLS; si un `GRANT` erróneo los devolviera, RLS sigue negando (0 filas, `INSERT` rechazado).
- No se creó ninguna política de acceso directo: los avisos públicos y la consulta por código pasan por la API del backend (valida y limita la tasa).
- Rol `hidro360_app` (privilegio mínimo; `NOLOGIN` al crearlo: el operador hace `ALTER ROLE hidro360_app LOGIN PASSWORD '…'` fuera del repositorio): políticas propias; en `auditoria` solo `SELECT/INSERT`. Probado que el backend completo funciona conectado como ese rol y que no puede alterar el esquema ni la auditoría.
- El dueño (`postgres`) y `service_role` saltan RLS por diseño de Postgres/Supabase: son credenciales de administración, no se usan para atender al público ni llegan al navegador. **Uso de la `anon key`/`service_role key` de Supabase desde el navegador: no necesario ni previsto.**

## Migraciones
- Archivos: `supabase/migrations/<timestamp>_*.sql` (idempotentes; sin nombres de esquema: se crean en el primero del `search_path`, `public` en Supabase). Orden: `…01_esquema_base` → `…02_auditoria_inmutable` → `…03_seguridad_rls`. **No edite una migración ya aplicada: añada una nueva** (el aplicador compara el checksum y se niega).
- Con Supabase CLI: `supabase db push` (o `supabase start` / `db reset` en local; `supabase/config.toml` es mínimo y **no se ha probado la CLI**).
- Sin CLI: `python -m scripts.aplicar_migraciones` (lee `DATABASE_URL` del entorno; registra versiones en `schema_migraciones`).
- Campos nuevos en los modelos: siguen siendo valores por defecto en Pydantic (los documentos antiguos se leen sin migrar). Si un campo nuevo necesita columna tipada: nueva migración `ALTER TABLE … ADD COLUMN IF NOT EXISTS` + entrada en `_COLUMNAS` de `data/_postgres.py` + backfill (`UPDATE` desde `datos`).
- SQLite: esquema `CREATE TABLE IF NOT EXISTS` en `data/_sqlite.py`; cambio incompatible ⇒ copia previa de `agua_clara.db` y script reversible en `scripts/migraciones/` (a crear cuando haga falta).

## Migrar SQLite → PostgreSQL
`scripts/migrar_sqlite_a_postgres.py` (destino = `DATABASE_URL` del entorno, nunca un argumento):
```powershell
$env:DATABASE_URL = '<conexión directa>'
python -m scripts.aplicar_migraciones
python -m scripts.migrar_sqlite_a_postgres --origen agua_clara.db                 # dry-run (por defecto): no escribe
python -m scripts.migrar_sqlite_a_postgres --origen agua_clara.db --ensayo        # carga + verifica + ROLLBACK
python -m scripts.migrar_sqlite_a_postgres --origen agua_clara.db --aplicar --backup-dir respaldos [--solo-reales] [--informe informe.txt]
```
Pasos: abrir el origen **en solo lectura** (nunca se modifica) → `PRAGMA integrity_check` → copia de seguridad (API de backup + integridad) → validar cada documento con los modelos actuales (`SCHEMA_VERSION`) → verificar la cadena de hashes del origen → comprobar que el destino tiene las tablas y está **vacío** (si no, rechaza salvo `--reanudar`, que es idempotente) → cargar en **una transacción** en orden de dependencias conservando IDs → ajustar secuencias IDENTITY → verificar **dentro de la transacción**: conteos, contenido documento a documento, relaciones sin huérfanos, administradores, tokens de dispositivos, códigos de seguimiento, secuencias y la **cadena de auditoría con `services.audit.verificar_integridad()`** (los hashes se copian tal cual, byte a byte; el último hash debe coincidir) → solo si todo coincide, `COMMIT`; cualquier discrepancia = `ROLLBACK`. Código de salida: 0 correcto · 1 rechazado/discrepancias · 2 error.
- `--solo-reales` omite los registros con `ficticio=true` y sus derivados (recomendaciones, eventos, fotos, decisiones, dispositivos y planes cuyo reparto es solo de cisternas ficticias). **La auditoría se migra completa** (filtrarla rompería la cadena).
- **Fotos**: solo viajan los metadatos. Los archivos los sube el paso de Storage del backend; el script informa de cuántos hay/faltan en `--media-dir` y ofrece el gancho `--hook-fotos paquete.modulo:funcion(metadatos, media_dir)`.
- `sesiones` y `limites` no se migran (efímeras: los usuarios inician sesión de nuevo).
- **Abortar**: Ctrl+C o cualquier error antes del COMMIT = el destino queda como estaba (transacción única).
- **Revertir una migración ya confirmada** (solo válido porque el destino estaba vacío antes): restaure la copia de Supabase (backup/PITR) o, en una transacción: `SELECT set_config('hidro360.permitir_reinicio','si',true);` y `DELETE FROM` en orden inverso (`recomendaciones, eventos_mision, misiones, dispositivos, fotos, usuarios, auditoria, decisiones_duplicado, planes, reportes, cisternas, sectores`). El origen sigue intacto y puede repetirse. Haga siempre una copia previa del destino si no estaba vacío.
- Límites: documentos con números que `jsonb` normaliza (p. ej. `1e22` ⇒ entero) se marcan como «distintos» por no ser idénticos (la auditoría se compara por texto exacto, no tiene este problema); el orden de claves de los diccionarios (`evidencia`, `resumen`…) puede cambiar al leer desde `jsonb` (el contenido no).

## Cómo probar
```powershell
# SQLite (por defecto, sin servidor)
.venv\Scripts\python -m pytest -q
# PostgreSQL local desechable (sin Docker/psql/CLI): binarios del paquete npm embedded-postgres en una carpeta git-ignorada
npm install embedded-postgres --prefix .tools/pgnode                     # una vez
node .tools/pgnode/node_modules/@embedded-postgres/windows-x64/scripts/hydrate-symlinks.js   # si npm no ejecutó el postinstall
python -m scripts.postgres_prueba --puerto 54329                         # deja el servidor y muestra TEST_DATABASE_URL
$env:TEST_DATABASE_URL = 'postgresql://postgres@127.0.0.1:54329/hidro360_test'
.venv\Scripts\python -m pytest tests/test_database_contrato.py tests/test_datos_postgres.py -q
.venv\Scripts\python -m pytest -p tests.plugin_postgres -q               # TODA la suite contra PostgreSQL
```
Sin `TEST_DATABASE_URL` las pruebas de PostgreSQL se **omiten**; nunca usan `DATABASE_URL`. Cada prueba trabaja en su propio esquema `t_<uuid>` con las migraciones reales. Los roles `anon/authenticated/service_role` **no existen en un Postgres plano**: `supabase/tests/00_roles_supabase_simulados.sql` los imita (incluidos los privilegios por defecto de Supabase) — la RLS real de Supabase no está probada.

## Datos ficticios vs reales
`Sector.ficticio`, `Cisterna.ficticio`, `Reporte.ficticio` y `Mision.ficticio` marcan los datos de demo (`data/datos_ficticios.py`); la UI muestra «DATOS FICTICIOS». Ningún dato de demo es real.

## Límites conocidos
- **No probado**: Supabase real, el pooler real (Supavisor/PgBouncer), `supabase` CLI, SSL contra un servidor remoto, el rol `postgres` de Supabase (no superusuario) ni sus defaults. Lo probado es PostgreSQL 18 local (binarios de `embedded-postgres`) con roles imitados.
- El carácter NUL (U+0000) en un documento se rechaza con `ValueError` (jsonb no lo admite); la entrada pública debería filtrarlo antes.
- `jsonb` no conserva el orden de las claves y normaliza números extremos (ver arriba); la auditoría está protegida con `datos_texto`.
- `cargar_datos_demo` vacía la auditoría: en PostgreSQL funciona con el dueño/superusuario fuera de producción y **falla** con `hidro360_app` o con `HIDRO360_ENV=prod` (comportamiento deseado).
- Sin pool de conexiones propio (solo la lista acotada de reutilización); sin migraciones de datos automáticas entre versiones del modelo.
- La auditoría y las trazas siguen siendo cosas distintas: las trazas JSONL no forman parte de la transacción (con `HIDRO360_TRACE=stdout` no hay archivo).
