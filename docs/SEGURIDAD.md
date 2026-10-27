# Seguridad de HIDRO360

## Modelo de acceso (roles)
| Quién | Cómo accede | Qué puede |
|---|---|---|
| **Ciudadano** (sin cuenta) | Portal público de la interfaz o `POST /api/v1/publico/reportes` | Registrar un reporte, consultar su estado con un **código de seguimiento** (`HID-XXXXXXXX`), ver avisos. Nunca ve textos de otras personas ni datos internos |
| **Usuario** (rol `usuario`) | Cuenta creada con `scripts.crear_admin --rol usuario` | Sin permisos administrativos (no accede a la consola ni a endpoints admin) |
| **Administrador** (rol `administrador`) | Login (usuario + contraseña) → sesión; en la API, sesión `Bearer` o token de servicio `HIDRO360_API_TOKEN` | Toda la gestión: revisar, confirmar/rechazar, duplicados, prioridades, planes, aprobar/rechazar, auditoría, trazas, diagnóstico |
| **Terminal de cisterna** (ESP32-S3) | Token propio **por vehículo** en la cabecera `X-Device-Token`, contra `/api/v1/dispositivo/*` (se crea con `python -m scripts.registrar_dispositivo CIS-01`) | Solo lo que expone `/dispositivo/*` para su misión; no administra ni aprueba. Contrato exacto: `docs/API.md` |
| **Dispositivo heredado** (pantalla de monitoreo) | `X-API-Token` = `HIDRO360_DEVICE_TOKEN` (alias obsoleto `AGUA_CLARA_DEVICE_TOKEN`) | Solo `GET /estado`, `POST /dispositivos/confirmaciones`. No lee reportes ni aprueba |

Los tokens de servicio y de dispositivo se llaman ahora `HIDRO360_API_TOKEN` / `HIDRO360_DEVICE_TOKEN`; los nombres `AGUA_CLARA_*` siguen funcionando (**obsoletos**) y, si ambos existen, gana `HIDRO360_*`.

## Dónde se comprueba la autorización (no basta con ocultar botones)
1. **Lógica de negocio** (`services/workflow.py`): las funciones administrativas llevan `@requiere_admin` (`services/auth.py`) y fallan con `ErrorAutenticacion` (sin actor) o `ErrorAutorizacion` (rol sin permiso) **antes** de abrir transacción. La interfaz y la API establecen el actor con `auth.como(actor)`.
2. **API** (`api/main.py`): dependencias `admin`, `autenticado`, `dispositivo_u_operador`; 401 sin credenciales válidas, 403 sin permiso, 429 por límite de tasa.
3. **Interfaz**: la consola no se construye sin sesión de administrador, y cada operación se ejecuta con el actor de la sesión. Es una comodidad: la barrera real son los puntos 1 y 2.
Pruebas (`tests/test_auth.py`): cada función administrativa y **cada ruta** de la API que no es pública se prueba sin credenciales (401), con rol `usuario` (403) y con token de dispositivo (401).

## Cuentas y contraseñas
- **No existen cuentas ni contraseñas predeterminadas.** El primer administrador se crea a mano: `python -m scripts.crear_admin` (la contraseña se pide sin mostrarla y no se acepta por argumento).
- Contraseñas con **scrypt** y sal aleatoria por cuenta (no se guardan en texto plano; hay prueba que inspecciona SQLite). Política mínima: 10 caracteres con mayúsculas, minúsculas y un número.
- Mensaje de error idéntico para usuario inexistente y contraseña incorrecta; **bloqueo temporal** tras 5 fallos por usuario en 5 min, persistido en la base (`services/limites.py`). Un acierto levanta el bloqueo, pero los fallos previos siguen contando hasta que vence su ventana.
- **Gestión de usuarios** (`/api/v1/usuarios`, funciones `*_usuario*` de `workflow`, todas `@requiere_admin` + auditadas): crear, desactivar/reactivar, cambiar rol, listar. Nunca se puede dejar el sistema sin un administrador activo.

## Autenticación con Supabase Auth (`HIDRO360_AUTH=supabase`)
**Identidad en Supabase, autorización en nuestra base.** El backend verifica el JWT de acceso (`services/auth_supabase.py`): firma (JWKS ES256/RS256/EdDSA con caché de 10 min y recarga por rotación de `kid` con enfriamiento de 30 s; HS256 solo con `SUPABASE_JWT_SECRET` para proyectos legados), `exp`, `nbf`, `iss` = `{SUPABASE_URL}/auth/v1`, `aud` = `authenticated`, y `role` = `authenticated` (se rechazan JWT `anon`/`service_role`). Se rechazan `alg=none`, cualquier algoritmo fuera de la lista blanca y la confusión de algoritmos (el `alg` de la cabecera decide el *tipo* de clave; se pasa a PyJWT un único algoritmo).
- El **rol y el estado salen de la tabla local `usuarios`**; `user_metadata`/`app_metadata` (editables por el cliente) no se leen jamás para autorizar. Sin perfil o desactivado → 403 aunque el JWT siga vigente (revocación inmediata).
- Vínculo: el perfil guarda `supabase_uid` (= `sub`). Alta controlada: `python -m scripts.crear_admin --supabase-email jefa@ejemplo.com` (o `POST /usuarios` en modo Supabase) crea el perfil «pendiente»; en el primer acceso se vincula por correo y desde entonces manda el `sub`. Un correo ya vinculado a otro `sub` se deniega.
- **Configuración obligatoria en Supabase**: desactive el registro abierto (Authentication → Providers → Email → «Allow new users to sign up» = off) y cree las cuentas de administración desde el panel o por invitación; así nadie puede registrar antes el correo de un administrador. Los ciudadanos **no** tienen cuenta (código de seguimiento).
- El token de servicio `X-API-Token` sigue válido en este modo (integraciones). `POST /auth/login` responde 410.
- Un JWT no es revocable por sí mismo (sin estado); la revocación es por perfil desactivado, y caduca solo (1 h por defecto en Supabase).
- **Estado: simulado.** Pruebas (`tests/test_auth_supabase.py`) con claves EC/RSA generadas en la prueba y JWKS simulado. **No probado contra un proyecto Supabase real.**
- Sesiones: token aleatorio de 256 bits; en la tabla `sesiones` **solo vive su SHA-256** (con vencimiento a las 8 h), así que sobreviven a reinicios y valen en cualquier instancia. Se invalidan al cerrar sesión y **en cada petición se revalidan contra el usuario actual**: desactivar la cuenta o cambiar su rol surte efecto al instante.
- El responsable en la auditoría es el **usuario autenticado** (con el token de servicio se registra `servicio:<nombre declarado>`; el ciudadano figura como `ciudadano`).

## Portal público
Código de seguimiento aleatorio (`secrets`); código inválido e inexistente dan el mismo error; límite de 10 reportes/min y 20 consultas/min por origen (persistidos en la base: no se evaden cambiando de instancia); la consulta devuelve solo código, estado público comprensible, sector (si está confirmado) y fecha: ni texto original, ni motivos de rechazo, ni responsables, ni ids internos. Los avisos públicos no incluyen litros, cisternas ni horas.

## Terminales por vehículo y fotografías
- **Un token por cisterna**: si una terminal se pierde o se compromete, se revoca solo esa y no las demás (a diferencia de un token compartido). Genere el token con `scripts.registrar_dispositivo` (aleatorio; no lo escriba a mano ni lo reutilice) y guárdelo solo en `config_dispositivo.h` de esa placa (ignorado por Git). Qué se guarda del token en la base y cómo se revoca: ver `docs/BASE_DE_DATOS.md` y `docs/API.md`.
- **Fotos**: `HIDRO360_STORAGE=local` las guarda en `HIDRO360_MEDIA_DIR` (en la Raspberry, `/var/lib/hidro360/media`, fuera del código y de Git; `media/` está en `.gitignore`). `HIDRO360_STORAGE=supabase` las sube a un **bucket PRIVADO** de Supabase Storage por REST (`httpx`): firma y tamaño se validan *antes* de subir; el objeto se llama `<128 bits aleatorios>.<ext>` (el nombre del cliente nunca participa); la **clave de servicio** (`SUPABASE_SERVICE_ROLE_KEY`) solo vive en el servidor y nunca aparece en respuestas, errores, auditoría ni trazas (los fallos del almacén se reducen a «no se pudo guardar» + código HTTP); no hay URLs públicas ni firmadas: la lectura pasa por el backend con rol de administrador. Si la subida falla, el reporte no se crea y se borran los objetos ya subidos. El borrado controlado (`eliminar_foto`) exige motivo y se audita. Cambiar de backend no migra las fotos existentes. Prueba con Storage **simulado** (`tests/test_storage_supabase.py`); **no probado contra Supabase real**. Límites del servidor: 3 fotos por reporte y 3 MB por foto (`config.py`). Una foto puede mostrar rostros, matrículas o interiores de viviendas: es dato personal; defina quién la ve y cuánto se conserva. Las copias de seguridad también las contienen.
- Las fotos y los tokens no deben aparecer en trazas ni en la auditoría (revise si añade registros nuevos).

## Secretos
`GEMINI_API_KEY` y los tokens salen solo de variables de entorno o `.env` (en `.gitignore`; solo se versiona `.env.example`). En la Raspberry viven en `/etc/hidro360/hidro360.env` (permisos `600`, generado por `install.sh` con tokens aleatorios `openssl rand -hex 32`, sin claves por defecto). No hay claves en código, tests, README ni frontend. Las trazas y la auditoría no guardan claves ni el texto completo del ciudadano. Si una clave se filtra: revóquela y cree otra.

## Entrada no confiable e IA
Texto del ciudadano = dato: frases tipo instrucción se excluyen; el modelo no tiene herramientas; teléfonos y correos se ocultan antes de enviar a Gemini; la salida se valida con Pydantic y con código; la IA no aprueba, rechaza, asigna ni prioriza. Ver `docs/IA_GEMINI.md`.

## Integridad
Auditoría y trazas con cadena de hashes: detectan la alteración o el borrado de una entrada intermedia, no la edición del archivo ni el truncado final. La auditoría está en la base (SQLite o PostgreSQL) en la misma transacción que el cambio.
**Trazas sin disco** (`HIDRO360_TRACE=stdout`, o automáticamente con `VERCEL=1` o si el disco es de solo lectura): una línea JSON por evento en la salida estándar, **sin cadena de hashes** (una función serverless no tiene disco persistente ni un único escritor: varias instancias romperían la cadena). La trazabilidad de negocio no se pierde porque la **auditoría** sigue encadenada en la base; las trazas son diagnóstico, y las conserva el agregador de logs de la plataforma. `GET /trazas` devuelve entonces `modo: "stdout"` y ningún evento. Todo texto de una traza pasa por un depurador que enmascara cadenas de conexión, JWT, cabeceras `Bearer`, claves `sb_secret_`/`AIza…` y el valor literal de los secretos del entorno.

## Sin estado local (serverless / varias instancias)
Sesiones (`sesiones`), bloqueos (`sesiones`, prefijo `bloqueo:`), límites de tasa (`limites`, `registrar_evento_limite` atómico) e idempotencia (por clave/`uid` en la base) son **datos**, no memoria: `tests/test_serverless.py` vacía el estado de módulo entre llamadas y comprueba que sesión, bloqueo, límites e idempotencia se comportan igual, y escanea los módulos para impedir que reaparezca un dict/list global de negocio. Único estado de módulo: la caché del JWKS (clave pública, inocua).
**IP de origen** (clave de los límites): se usa la dirección del socket. Las cabeceras de proxy solo se leen si `HIDRO360_ENV=prod` **y** se declara un proxy de confianza (`HIDRO360_TRUST_PROXY=1`, o `VERCEL=1`, que Vercel pone y cuyo borde reescribe `X-Forwarded-For`); entonces se toma la entrada que añadió el último proxy de confianza (`HIDRO360_PROXY_SALTOS` desde la derecha, 1 por defecto) y, si no, `X-Real-IP`; un valor que no sea una IP se descarta. Fuera de ese caso `X-Forwarded-For` se ignora (un cliente podría falsificarlo para evadir los límites). Si despliega tras otro proxy distinto, ajuste los saltos. Credenciales de administración inválidas: tras 20 fallos/5 min por origen la respuesta pasa a 429; las credenciales válidas nunca se bloquean.

## Limitaciones conocidas (honestas)
- Sesiones, bloqueos y límites **persisten en la base** (ya no en memoria); consecuencias: cada petición de terminal/pública/login añade una escritura de límite, y los eventos viejos se purgan de forma oportunista (`limpiar_limites`).
- El bloqueo por usuario permite bloquear a propósito a un administrador (denegación de servicio dirigida). Tras un proxy no declarado como de confianza, el límite por IP agrupa a todos los clientes bajo la IP del proxy.
- **Sin HTTPS propio**: contraseñas, tokens y sesiones viajan en claro en la red local. Úsese solo en una red de confianza o detrás de un proxy TLS.
- Sin recuperación de contraseña ni MFA. Las cuentas se gestionan desde el panel «Usuarios» (solo administradores) o por línea de comandos.
- Si dos ciudadanos envían exactamente el mismo texto en 30 s, la idempotencia devuelve el mismo reporte y el mismo código.
- No se ha realizado una auditoría de seguridad externa ni pruebas de penetración: no afirme que el acceso es «a prueba de ataques»; afirme lo que las pruebas demuestran.
- Datos personales: la demo usa datos ficticios; con datos reales defina retención y revise las condiciones de Google si usa Gemini.

## Red y despliegue en Raspberry
- **No abra puertos a internet ni haga port-forwarding.** En local (Windows) la API y la interfaz escuchan en `127.0.0.1`. En la Raspberry los servicios escuchan en `0.0.0.0` (puertos 8000 y 8501) **para atender a la red local**: restrinja con `ufw` a su subred (comandos en `docs/INSTALACION.md` §9.4; el instalador no cambia el firewall).
- **HTTP sin TLS en la LAN**: contraseñas, sesiones, tokens y fotos viajan **en claro**. Cualquiera en la misma red (Wi-Fi compartida incluida) puede capturarlos o suplantar una terminal. Riesgo aceptable solo en una red local aislada y de confianza; para algo más, ponga un proxy con TLS delante (no incluido ni probado).
- Los servicios corren como usuario `hidro360` sin privilegios, con `NoNewPrivileges`, `ProtectSystem=strict` y escritura únicamente en `/var/lib/hidro360` (unidades en `deploy/raspberry/`; **no probadas con systemd real**). Solo la instalación corre como root.
- Logs en journald (`journalctl -u hidro360-api -f`).

## Qué NO cubre esta seguridad
Sin TLS, sin MFA, sin recuperación de contraseñas, sin cifrado de la base ni de las fotos en disco ni de las copias, sin revisión externa ni pruebas de penetración, sin protección contra quien tenga acceso físico a la Raspberry o a la tarjeta SD, y sin certeza de que el despliegue en Raspberry funcione: está sin probar. Un token de terminal robado permite actuar como esa cisterna hasta revocarlo.
