# Hardware: pantalla ESP32-S3 y Raspberry Pi 4 (HIDRO360)

> **Estado honesto.** La pantalla es la **terminal operativa de un carro cisterna** (una por vehículo, con su propio token). El firmware (2.1.0, con HTTPS opcional) está **escrito y se compila** con el `arduino-cli` del Arduino IDE instalado en el equipo de desarrollo (resultado en la sección 6), pero **NO está probado en la placa ni en la Raspberry**: no había hardware. Lo verificado sin placa: el protocolo HTTP completo contra la API real con `scripts/simular_terminal.py` (sección 6.5). Eso **no sustituye** la prueba física (WiFi, táctil, pantalla, NVS, tiempos). Nada se declara funcional en la placa sin esa prueba.

## 1. Modelo de placa (qué evidencia lo identifica)
- **Placa**: «2.8inch ESP32-S3 Display», documentada en https://www.lcdwiki.com/2.8inch_ESP32-S3_Display (consultada en esta revisión). SKU **ES3C28P** (con táctil capacitivo); existe también **ES3N28P** (sin táctil). El proyecto usa el táctil, por lo que se asume ES3C28P; la etiqueta física de la placa no se ha comprobado.
- **Módulo**: ESP32-S3 con «+16M external SPI Flash» y «8M internal OPI PSRAM» (N16R8), según la wiki. Pantalla ILI9341V 240×320; táctil FT6336G (I2C).
- Los ejemplos del fabricante están en `Demo/` (30 sketches, sin versionar; no se modifican).

## 2. Tabla de pines
Estados: **verificado** = coincide en la wiki y en código del fabricante; **wiki** = solo documentación; **no verificado** = sin evidencia en el repo ni en la wiki.

| Función | Pines (GPIO) | Estado | Fuente |
|---|---|---|---|
| TFT ILI9341V (SPI) | CS 10, DC 46, SCLK 12, MOSI 11, MISO 13, BL 45; RST compartido con el reset del ESP32 (`-1`) | verificado | wiki; `Demo/Example_01_Simple_test/Simple_test/spi_dev.h`; `User_Setup.h` de TFT_eSPI en `Documentos/Arduino/libraries` (`TFT_CS 10, TFT_DC 46, TFT_SCLK 12, TFT_MOSI 11, TFT_MISO 13, TFT_BL 45, TFT_RST -1`) |
| Táctil FT6336G (I2C) | SDA 16, SCL 15, INT 17, RST 18 | verificado | wiki; `Demo/Example_08_LVGL_Demos/LVGL_Demos/touch.h` (igual en Ejemplos 14 y 15) |
| LED RGB (1 pixel, un solo hilo) | IO42 | verificado | wiki; `LED_PIN 42` en Ejemplos 06, 09, 12 y 15 |
| microSD (SDIO 4 bits) | CLK 38, CMD 40, D0 39, D1 41, D2 48, D3 47 | verificado | wiki; `Demo/Example_05_show_SD_jpg_picture/Show_SD_Jpg/Show_SD_Jpg.ino`; `Demo/Example_16_music/music/ESP_Panel_Board_Custom.h` |
| Audio I2S (códec ES8311) | MCLK 4, BCLK 5, WS 7, DOUT 8, DIN 6; IO1 habilita el amplificador (nivel bajo = habilitado) | I2S verificado; IO1 solo wiki | wiki; `Demo/Example_17_echo/echo/echo.ino` (GPIO 4, 5, 7, 8, 6); `ESP_Panel_Board_Custom.h`. El códec comparte el bus I2C 15/16 (`I2C_SCL 15`, `I2C_SDA 16` en ese archivo) |
| Botón BOOT / descarga | IO0 (el botón EN reinicia) | verificado | wiki; `BUTTON_PIN 0` en `Demo/Example_09_key/Key_test/Key_test.ino` |
| UART0 (conector 4P, requiere adaptador USB-serie externo) | IO43 e IO44 | wiki; **discrepancia** RX/TX | La wiki (releída en esta revisión) escribe «RXD0 (IO43), TXD0 (IO44)»; en el chip ESP32-S3 U0TXD es GPIO43 y U0RXD GPIO44 (dato general del chip, no está en el repo). Hasta mirar la serigrafía de la placa, no cablee RX/TX por la wiki. El firmware no usa este UART |
| **GPS / GNSS** | — | **la placa NO lo documenta** | La wiki del fabricante no menciona GPS/GNSS (relectura de la página) y `Demo/` no contiene ni una referencia a GPS/NMEA/GNSS (búsqueda en los 30 ejemplos). **No hay GPS integrado verificado.** Consecuencia de diseño: la terminal **no reporta posición**; el destino y sus coordenadas vienen de la misión (`lat/lng` de la parada) y la ubicación mostrada en el mapa del panel es la de la última parada alcanzada, no una posición en vivo. Un GPS externo (UART por el conector 4P o por los GPIO de expansión) sería una ampliación **no diseñada ni probada** |
| Batería (ADC) | IO9 | wiki | wiki; `Demo/Example_13_Get_Battery_Voltage/GetBatteryVoltage/GetBatteryVoltage.ino` usa `ADC_UNIT_1`, `ADC_CHANNEL_8` (que ADC1 canal 8 sea GPIO9 es dato del chip, no del repo) |
| **Conector «Expand pin»** (1.25 mm, 4 pines) | GPIO **2, 3, 14, 21**, «normal IO» | wiki (única fuente) | wiki. **No da el orden pin a pin del conector (cuál es VCC, GND o señal)**: no verificado |
| Conectores UART (4P), I2C (4P, comparte IO16/IO15 con el táctil), batería (2P), altavoz (2P) | los indicados | wiki (solo existencia) | wiki. Orden de pines y polaridad: **no verificado** |
| Strapping, USB, flash/PSRAM | strapping 0, 3, 45, 46; USB 19/20; flash/PSRAM octal en 26-37 con módulos R8 | **no verificado en el repo** | datos generales del chip (datasheet de Espressif), no consultado en esta revisión: confirmar antes de usarlos |

**Pines realmente libres**: la única evidencia es la wiki: **GPIO2, GPIO3, GPIO14 y GPIO21** salen al conector de expansión. Ningún otro GPIO se puede dar por libre: o están asignados arriba o no hay evidencia (**no verificado**). GPIO3 es pin de strapping según el datasheet general del chip (no verificado aquí). Los comentarios de pines dentro de los `.ino` de `Demo/` (p. ej. `DC=2`, `LED=21`, `BAT_VOLT_ADC 34`) están copiados de otras placas y **no son fiables**; mandan la wiki y las constantes de código.

## 3. Comprobación del sketch contra la tabla
`firmware/esp32s3_monitor/esp32s3_monitor.ino` usa: TFT por TFT_eSPI (pines de `User_Setup.h`, coinciden con la wiki y con `Demo/Example_01`), táctil FT6336 en 16/15/17/18 (coinciden con `Demo/Example_08/LVGL_Demos/touch.h`) y la luz de fondo IO45 (la enciende TFT_eSPI con `TFT_BL`; se eliminó el `pinMode(45)` redundante). **No usa** IO42, SD, audio, UART0 ni pines de expansión. **No se encontraron conflictos de pines.** Observaciones:
- IO45 es pin de strapping del chip: no conecte nada externo a él.
- `firmware/pruebas/led_rgb/led_rgb.ino` usa IO42 con `NEO_RGB`; los ejemplos del fabricante usan `NEO_GRB`/`NEO_GRBW`: si rojo y verde salen cambiados es el orden de color (no probado).
- El bus I2C 15/16 lo comparten táctil, códec y conector I2C: no añada otros dispositivos sin comprobar direcciones. El conector IIC solo queda libre en la variante sin táctil (ES3N28P), según la wiki.
- La rotación del táctil (`ROTATION_NORMAL`) y la de la pantalla (`setRotation(0)`) siguen la combinación usada en `Demo/Example_08`; **no se ha comprobado en la placa** que los toques caigan sobre los botones dibujados.

## 4. Limitaciones físicas reales
- **Seguridad de red:** con `API_BASE_URL` `http://` (LAN) el `X-Device-Token` viaja **en claro**; cualquiera en la misma red WiFi puede capturarlo y suplantar al vehículo. Mitigación: red local aislada con WPA2/WPA3, un token por vehículo, rotarlo (`rotar-token`) si el vehículo se pierde o se sospecha filtración, y desactivar el dispositivo. Con `https://` (host público) el firmware valida el certificado del servidor con el bundle de CAs raíz del core (sección 6.7); **implementado y compilado, no probado contra un servidor real ni en la placa**. El bundle fija en qué CAs se confía, pero no fija el certificado del servidor (no hay *pinning* de hoja).
- **El token está en el firmware** (`config_dispositivo.h` compilado): quien tenga la placa y un cable puede extraerlo (no se activó Flash Encryption ni Secure Boot).
- **Sin GPS:** la terminal no conoce ni reporta su posición (sección 2). Sin NTP no hay hora real (en HTTP local no se usa): se muestra la hora del servidor de la última sincronización; los eventos viajan siempre con `fecha_dispositivo = null` y el servidor pone la hora. En HTTPS el reloj se sincroniza por NTP (sección 6.7) porque es imprescindible para validar el certificado.
- **Peticiones bloqueantes:** durante una petición (HTTP: hasta `HTTP_TIMEOUT_MS`, 5 s; HTTPS: hasta `HTTPS_TIMEOUT_MS`, 15 s, que incluye el handshake TLS) la pantalla no atiende toques; el toque se pierde, no se encola. Con WiFi caído, sin hora (HTTPS) o mientras se confirma una acción no se hacen peticiones, así que la interfaz responde. Los sondeos fallidos se espacian (10, 20, 40, 60 s) para limitar el bloqueo.
- **Cola:** hasta 24 eventos en NVS. Si se llena (24 acciones sin conexión) el firmware avisa «Cola llena» y no acepta más; los guardados no se pierden.
- **Confirmación:** una acción guardada en la terminal **no se muestra como confirmada** hasta que el servidor responde `aplicado` o `duplicado`: la parada dice «ENTREGA POR ENVIAR» (naranja) y solo pasa a «ENTREGA CONFIRMADA» (verde) cuando el evento sale de la cola; los contadores «Hechas» solo cuentan paradas confirmadas. Si el servidor la **rechaza**, el aviso con el motivo se muestra 12 s y el siguiente sondeo devuelve la parada al estado del servidor.
- **Litros entregados:** ENTREGADO registra los litros planificados de la parada (no hay entrada numérica). Una entrega parcial debe registrarse como incidencia. INCIDENCIA envía una nota fija («incidencia reportada desde la terminal»).
- **Misión completada:** el servidor solo devuelve misiones activas; al completarse devuelve `mision: null` (comprobado con el simulador, no está escrito en el contrato). La terminal muestra «MISION COMPLETADA» solo si localmente todas las paradas estaban cerradas y la cola vacía; en otro caso (p. ej. misión cancelada) muestra «SIN MISION ASIGNADA».
- WiFi 2.4 GHz; pantalla 240×320 solo ASCII: el firmware transcribe los textos del servidor (á→a, ñ→n, ¿→?, comillas y rayas tipográficas) antes de mostrarlos y guardarlos; textos largos se recortan; máximo 10 paradas por misión mostrada (con aviso si hay más).
- Sin batería ni sensores previstos. Alimentación del vehículo (5 V por USB-C), robustez mecánica y térmica en cabina: no evaluadas.

## 5. Qué requiere prueba física (nada de esto está probado)
Carga a la placa; orientación y calibración del táctil; legibilidad al sol; reconexión WiFi; comportamiento con la API apagada («SERVIDOR SIN RESPUESTA» y recuperación); persistencia de la cola tras cortar la luz con eventos pendientes; token rechazado (rotar el token a mitad de misión); consumo; arranque tras corte de luz; salida por Serial (¿USB nativo o puente?); SKU real de la placa y pinout del conector de expansión.

## 6. Firmware de la terminal (`firmware/esp32s3_monitor/`)
### 6.1 Qué hace (versión de firmware 2.1.0, protocolo 1)
Pantalla principal (240×320, texto ≥ 16 px de alto, botones de acción de 46-48 px): cabecera con el **ID del vehículo** y el **estado de conexión** (verde = en línea, roja = problema; tocarla abre la pantalla de estado), **última sincronización** («hace N s» o «SIN SINCRONIZAR»), misión (#id y estado) y número de parada, **destino actual** (sector) con sus **litros** y el estado de la parada («PENDIENTE», «ENTREGA POR ENVIAR», «ENTREGA CONFIRMADA»…), instrucción de entrega, **próxima parada**, línea de **alertas operativas** (aviso, cola por enviar o casi llena, token rechazado, error TLS, hora sin sincronizar, error de servidor), contadores confirmados («Hechas n/N, Inc») y botón **LISTA**. Flechas `<` `>` para recorrer paradas. Botones táctiles **LLEGUE**, **INCIDENCIA** y **ENTREGADO**; ENTREGADO e INCIDENCIA piden **confirmación explícita** en otra pantalla (CONFIRMAR / CANCELAR; se cancela sola a los 15 s).

Otras pantallas: **LISTA** (todas las paradas con estado: PENDIENTE / POR ENVIAR / ENTREGADA / INCIDENCIA; tocar una fila la selecciona); **ESTADO** (WiFi y RSSI, HTTP o HTTPS, error TLS, hora UTC, último código del servidor, última sincronización, cola n/24, heap libre, versión de firmware y protocolo del servidor); pantallas completas para sin WiFi, **ERROR TLS**, **HORA NO SINCRONIZADA**, token rechazado (401/403), sin misión, misión completada/cancelada, error de servidor (5xx/429), servidor sin respuesta y **ACTUALICE EL FIRMWARE**. Con WiFi caído o error de servidor y una misión en caché, la terminal **sigue operativa** mostrando la última misión conocida marcada «SIN SINCRONIZAR».

### 6.2 Decisiones de diseño
- **Persistencia: NVS (`Preferences`), no LittleFS.** Los datos son pequeños (cola ≤ 24 eventos ≈ 1,2 KB; misión ≤ 10 paradas < 4 KB), NVS escribe por clave (un corte de luz no deja una cadena a medias) y no hay que montar ni formatear un sistema de archivos. Límite: ~4000 bytes por cadena (de ahí el tope de 10 paradas) y desgaste del flash: se escribe la cola en cada acción del operador y la misión al cambiar (decenas de escrituras por turno). Si el formato de la cola cambia entre versiones del firmware, la cola guardada se descarta.
- **uid de evento** = `t<arranque>-<secuencia>-<aleatorio de 32 bits del hardware>`; el contador de arranques es persistente, por lo que es único aunque se reinicie o se pierda la NVS.
- **Entrega fiable:** el evento sale de la cola solo si el servidor responde `aplicado`, `duplicado` o `rechazado` para ese uid. Reintento con retroceso exponencial (2, 4, 8, 16, 32, 60 s; tope 60 s) ante red caída, 401/403, 429 o 5xx. Un 400/422 (lote ilegible) descarta el evento tras 5 intentos para no atascar la cola. Lotes de hasta 10 eventos (el servidor admite 50). La cola admite 24 eventos; a partir del 80 % la pantalla avisa «COLA CASI LLENA» y al llenarse rechaza nuevas acciones con aviso (nunca descarta lo guardado).
- **Estado local vs. servidor:** al actuar, la parada cambia al instante; al sondear, si hay eventos de esa parada aún sin confirmar se mantiene el estado local, y si no manda el servidor.
- **HTTP/HTTPS:** `WiFiClient` o `WiFiClientSecure` + `HTTPClient` locales a cada petición (se liberan con `end()`; sin conexiones persistentes), `setConnectTimeout`/`setTimeout`, `begin()` comprobado, respuesta limitada a 24 KB, JSON con filtro de ArduinoJson, cabeceras `X-Device-Token` y `X-Firmware-Version`. Los códigos propios (`LL_*`) ya no se confunden con los de `HTTPClient` (antes un servidor caído, `HTTPC_ERROR_CONNECTION_REFUSED` = -1, se mostraba como «SIN WIFI»). Las redirecciones 3xx no se siguen (se muestran como error de servidor): use la URL final `https://`.
- **Compatibilidad firmware/API:** el firmware lee `protocolo` (entero) de `GET /dispositivo/mision`; si falta asume 1; si es **mayor** que `PROTOCOLO_ESPERADO` (1) muestra «ACTUALICE EL FIRMWARE», no interpreta la misión y **no envía eventos** (la cola se conserva). El latido sigue enviándose para que el servidor vea la versión.
- **Texto del servidor:** se transcribe a ASCII al recibirlo (también el motivo de un rechazo).
- Sondeo cada `MISION_POLL_MS` (10 s) y latido cada `LATIDO_MS` (30 s), configurables.

### 6.3 Compilación (resultado real)
**Compila sin errores** (2026-10-17, firmware 2.1.0) con `arduino-cli` 1.5.1, core `esp32:esp32` 3.3.12, FQBN `esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,FlashMode=qio`, TFT_eSPI 2.5.43, ArduinoJson 7.4.3 y FT6336-arduino del fabricante:

| Variante | Flash (3 145 728 B de aplicación) | RAM estática (327 680 B) |
|---|---|---|
| HTTP (`http://192.168.1.50:8000/api/v1`) | 1 027 123 B (32 %) | 50 660 B (15 %) |
| HTTPS (`https://ejemplo.invalid/api/v1`, bundle de CAs) | 1 202 991 B (38 %), +175 868 B por TLS y bundle | 51 228 B (15 %) |
| HTTPS + `API_TLS_INSECURE` | compila; emite el `#warning` previsto | — |

Única advertencia propia de librerías: `TOUCH_CS pin not defined` de TFT_eSPI (inofensiva: el táctil es I2C). **Compilar no prueba que funcione en la placa**: no se cargó a ninguna. Reproducible con `scripts/compilar_firmware.ps1` (sección 6.7); la primera compilación en frío tardó más de 20 min en el PC de desarrollo (cargado), las incrementales menos.

Comando exacto (Windows; `arduino-cli` viene en Arduino IDE 2.x; copie antes `config_dispositivo.h.example` a `config_dispositivo.h`):
```
"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" core install esp32:esp32@3.3.12
arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,FlashMode=qio firmware/esp32s3_monitor
```
Librerías (en `Documentos/Arduino/libraries`, del paquete del fabricante): TFT_eSPI 2.5.43 (con el `User_Setup.h` de la placa ya configurado: ILI9341 y pines de la sección 2), **FT6336-arduino** (sin versión: no tiene `library.properties`), **ArduinoJson 7.4.3** (el sketch requiere la API 7.x: `JsonDocument`). Core `esp32:esp32` 3.3.12. WiFi, HTTPClient y Preferences vienen con el core. **Atención:** el `User_Setup.h` está dentro de la carpeta de TFT_eSPI; si se reinstala la librería desde el gestor se pierde y la pantalla no funciona.
Opciones de placa: ESP32S3 Dev Module, Flash 16 MB, PSRAM **OPI** (módulo N16R8 según la wiki), esquema de particiones 16 MB (3 MB app). Si no sale texto por Serial, pruebe «USB CDC On Boot: Enabled» (no se verificó si el USB-C es nativo o puente).

### 6.4 Flasheo y prueba en la placa (pendiente de hacer)
1. Copie `config_dispositivo.h.example` a `config_dispositivo.h` (no se versiona) y complete `WIFI_SSID`, `WIFI_PASS`, `API_BASE_URL` (con `/api/v1`: `http://IP-de-la-Raspberry:8000/api/v1` en LAN o `https://su-dominio/api/v1` para el host público), `DEVICE_TOKEN` (el del vehículo) y `CISTERNA_ID` (solo se muestra). No ponga `API_TLS_INSECURE`.
2. En el servidor: registre la terminal (`python -m scripts.registrar_dispositivo CIS-001 --nombre "Camión 1"`; el token se muestra una vez) y asigne una misión desde el panel o con `POST /api/v1/misiones`.
3. Conecte el USB-C; si el puerto no aparece, mantenga **BOOT** (IO0) y pulse **RESET**; cargue desde Arduino IDE (opciones de 6.3) o con `arduino-cli upload -p COMx --fqbn ...`.
4. Prueba mínima: arranque → cabecera «EN LINEA» y misión (con HTTPS, primero «SINCRONIZANDO HORA» unos segundos); abra ESTADO tocando la cabecera y anote heap libre/mínimo durante una petición HTTPS; LLEGUE → «LLEGADA POR ENVIAR» pasa a «LLEGADA CONFIRMADA» en segundos y el panel muestra la parada en «llegada»; ENTREGADO → pantalla de confirmación → CONFIRMAR; apague el router, registre una acción (debe mostrar «sin conexión» y «Por enviar: 1»), reinicie la placa (cola y misión deben seguir) y reconecte (la cola debe vaciarse sola); rote el token (debe aparecer «TOKEN RECHAZADO»).

### 6.5 Simulador del protocolo (sin placa)
`python -m scripts.simular_terminal --autoprueba` ejecuta (1) **pruebas de protocolo** contra un servidor falso local (cabeceras `X-Device-Token` y `X-Firmware-Version`; `protocolo` ausente → 1, igual → opera, mayor → bloqueo sin enviar eventos, no entero → 1; respuesta perdida → reintento con el **mismo uid** → `duplicado`; 401 conserva la cola; servidor caído ≠ error TLS) y (2) el escenario completo contra la API real (base temporal, uvicorn en un puerto libre): sondeo, cola con uid, reintento con retroceso, duplicado, rechazo, latido, cierre de misión. `--autoprueba --https` repite (2) con la API sirviendo TLS con un certificado autofirmado (necesita `openssl`, p. ej. el de Git for Windows) y comprueba que con el almacén de confianza por defecto el certificado se **rechaza** (código TLS, distinto de «sin red») y que con la CA de la prueba la terminal opera. `--protocolo` ejecuta solo (1). Contra una API ya en marcha: `--base https://host/api/v1 --token <TOKEN> [--ca ca.pem] [--bucle]`. **No prueba** el firmware, la pantalla, el táctil, la NVS ni el bundle de CAs del ESP32 (el simulador usa el almacén del sistema). Resultado de la última ejecución (2026-10-17): protocolo 11/11 OK; escenario HTTP 14/14 OK; escenario HTTPS 15/15 OK (la API ya devuelve `protocolo: 1` en misión y latido).

### 6.6 Solución de problemas
| Síntoma | Causa probable |
|---|---|
| Pantalla en blanco | `User_Setup.h` de TFT_eSPI distinto al de la placa; luz de fondo IO45 |
| Toques desplazados o espejados | rotación del táctil distinta de la de la pantalla (`ts.setRotation(...)`) |
| «SIN WIFI» permanente | SSID/clave mal, red de 5 GHz (solo 2.4 GHz), router lejano |
| «SERVIDOR SIN RESPUESTA» | `API_BASE_URL` mal (¿falta `/api/v1`?), firewall de la Raspberry (puerto 8000), uvicorn en `127.0.0.1` en vez de `0.0.0.0` |
| «TOKEN RECHAZADO» (401/403) | token incorrecto, rotado o dispositivo desactivado; la cola se conserva y se envía al corregirlo |
| «ERROR SERVIDOR» / «LIMITE (429)» | 5xx o 429: reintenta solo |
| «Rechazado: …» | el servidor no aceptó el evento (transición no permitida, misión cancelada); el motivo viene del servidor |
| Errores de `JsonDocument` al compilar | ArduinoJson 6.x instalado: use 7.x |
| «ERROR TLS» (código `-0x....`) | `-0x2700` certificado no verificable: CA fuera del bundle, cadena incompleta en el servidor, dominio distinto del certificado o **hora incorrecta**; `-0x7280`/`-0x7780`: fallo de handshake/lectura (red lenta, aumente `HTTPS_TIMEOUT_MS`). Compruebe que la URL es `https://` y que el host sirve la cadena completa |
| «SINCRONIZANDO HORA» → «HORA NO SINCRONIZADA» | sin salida a NTP (UDP 123) o a `pool.ntp.org`/`time.google.com` (cambie `NTP_SERVIDOR_1/2`); solo afecta a HTTPS |
| «ACTUALICE EL FIRMWARE» | el servidor anuncia un `protocolo` mayor que `PROTOCOLO_ESPERADO`; recompile y cargue el firmware nuevo |
| «TLS INSECURO!» en la cabecera roja | se compiló con `API_TLS_INSECURE`: quítelo |
| «library not valid» al instalar por ZIP | sección 8 |

### 6.7 HTTPS y hora (NTP)
- **Selección por URL:** `API_BASE_URL` con `http://` usa `WiFiClient` (LAN, sin NTP); con `https://` usa `WiFiClientSecure` (`useBuiltinCACertBundle()` del core esp32 3.3.12, que enlaza el bundle de CAs raíz de Mozilla de ESP-IDF). Un esquema distinto no compila (`static_assert`).
- **Opción B, CA fijada:** defina `API_CA_ROOT_PEM` en `config_dispositivo.h` con el PEM de la CA **raíz** (`setCACert`); sustituye al bundle.
- **`API_TLS_INSECURE`** (apagada por defecto) usa `setInsecure()`: no valida nada. Con ella la cabecera es roja con «TLS INSEGURO!», la pantalla de estado lo indica y el puerto serie lo advierte; el compilador emite `#warning`. Solo laboratorio.
- **Hora:** en HTTPS, tras conectar el WiFi se llama a `configTime` (UTC); no se hace ninguna petición hasta que `time()` sea posterior a 2025-01-01. Mientras tanto «SINCRONIZANDO HORA»; pasados `NTP_ESPERA_MS` (15 s) «HORA NO SINCRONIZADA» y se reintenta cada 30 s. La misión en caché sigue visible y la cola se conserva.
- **Errores:** si el fallo ocurre en TLS (`lastError() != 0`) la cabecera dice «ERROR TLS» y se muestra el código mbedTLS; un fallo de TCP/DNS sigue siendo «SIN RESPUESTA».
- **Memoria:** el handshake TLS necesita decenas de KB de heap (orden de 40-60 KB según mbedTLS; **no medido en la placa**). Cada petición abre y cierra su conexión (sin reutilización), por eso se recomienda `MISION_POLL_MS` 15000 con HTTPS. El firmware escribe por serie `[heap] ruta libre/minHistorico/mayorBloque` tras cada petición y la pantalla ESTADO muestra el heap actual y el mínimo: **midalos en la placa** antes de dar la memoria por suficiente (la N16R8 tiene 8 MB de PSRAM, pero no se verificó que mbedTLS la use).
- **Certificado del servidor:** el host público debe servir una cadena que termine en una CA del bundle (Let's Encrypt/ISRG Root X1 y las CAs comerciales habituales están; confírmelo con el host elegido). Si el certificado se renueva con otra CA raíz que no esté en el bundle, la terminal queda en «ERROR TLS» hasta actualizar el firmware.
- **Compilar:** `powershell -ExecutionPolicy Bypass -File scripts\compilar_firmware.ps1 [-Variante http|https|ambas] [-UrlHttps https://su-host/api/v1] [-Insecure]` compila una copia temporal con un `config_dispositivo.h` de relleno (no toca el real ni carga nada). La primera ejecución compila el core y tarda varios minutos; las siguientes reutilizan la carpeta `build` temporal.

## 7. Rutas de integración
- **ESP32-S3 (terminal de cisterna) → HTTP → FastAPI** (`GET /api/v1/dispositivo/mision`, `POST /api/v1/dispositivo/eventos`, `POST /api/v1/dispositivo/latido`) con el `X-Device-Token` del vehículo. La API corre en la Raspberry Pi o en un PC de la misma red. Autorización y roles: `docs/SEGURIDAD.md`, `docs/API.md`.
- **Raspberry Pi 4**: dos servicios systemd (`hidro360-api`, `hidro360-ui`), SQLite local, copias y comprobación de salud (sección 9).

## 8. Error de Arduino IDE «library not valid» — análisis
Error: `Library install failed: moving extracted archive to destination dir: library not valid`.
Lo que se examinó (no se dispone del ZIP original):
- El paquete del fabricante (`libraries/`) **no era una librería sino un conjunto de 21 librerías ya descomprimidas**. Un ZIP de ese conjunto no puede instalarse con «Añadir biblioteca .ZIP»: su raíz no tiene `library.properties` ni `src/`.
- Dentro hay dos carpetas **sin `library.properties`**: `FT6336-arduino` (solo `FT6336.h/.cpp` en la raíz) y `XT_DAC_Audio`. Son formato antiguo; el instalador por ZIP del IDE 2.x/`arduino-cli` puede rechazarlos como no válidos. Las otras 19 tienen estructura válida.
- Una carpeta `libraries` **dentro del sketch no es leída** por Arduino IDE: las librerías deben estar en `Documentos/Arduino/libraries/`.
- Comprobado: `C:\Users\Victor Montes\Documents\Arduino\libraries` ya contiene las 21 carpetas (copiadas a mano), que es el método que evita la validación del instalador.
**Causa más probable (no reproducida)**: intentar instalar por ZIP el conjunto completo o `FT6336-arduino`. **Solución**: copiar cada carpeta a `Documentos/Arduino/libraries/` (ya hecho) o, si se quiere instalar por ZIP, añadir a `FT6336-arduino` un `library.properties` mínimo (`name=FT6336`, `version=1.0.0`, `author=...`, `sentence=...`, `paragraph=...`, `category=Other`, `url=...`, `architectures=*`) y comprimir **esa carpeta sola**.
**Carpeta `PANTALLA_DE_MONITOREO` (resuelta, ver `docs/DECISIONES.md` D-05):** era el espacio de trabajo de Arduino. Su `libraries/` (354 MB) era idéntica por SHA-256 (5564 archivos) a `Documentos/Arduino/libraries` y se eliminó; su sketch de prueba del LED RGB se conservó en `firmware/pruebas/led_rgb/led_rgb.ino`. El firmware del tablero está en `firmware/esp32s3_monitor/`.

## 9. Raspberry Pi 4 (Raspberry Pi OS Lite)
Archivos en `deploy/raspberry/`: `install.sh`, `hidro360-api.service`, `hidro360-ui.service`, `backup.sh`, `healthcheck.sh`, `.env.example`. **Probado por el equipo en una Raspberry Pi (Raspberry Pi OS Lite 64-bit):** los servicios arrancaron y `healthcheck.sh` informó API y panel correctos. Para el panel hizo falta un ajuste manual (ver `docs/INSTALACION.md`, «Raspberry»). (Antes `agua-clara-*.service`; renombrados a HIDRO360.)
```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip
git clone <repo> /opt/hidro360 && cd /opt/hidro360
sudo bash deploy/raspberry/install.sh        # crea usuario, venv, dependencias, /etc/hidro360/hidro360.env y servicios
sudo nano /etc/hidro360/hidro360.env     # fije HIDRO360_API_TOKEN y HIDRO360_DEVICE_TOKEN
sudo systemctl enable --now hidro360-api hidro360-ui
bash deploy/raspberry/healthcheck.sh
```
- **Servicios**: API en `:8000` (uvicorn) y panel Flet en modo web en `:8501` (`python app.py --web --host 0.0.0.0 --sin-navegador`; el navegador cliente carga Flet desde internet, ver `docs/DECISIONES.md` D-04; la Pi no la necesita pero la primera ejecución de Flet descarga su cliente); reinicio controlado con `systemctl restart`; registros con `journalctl -u hidro360-api`.
- **Datos**: SQLite y trazas en `/var/lib/hidro360/`; copias con `backup.sh` (SQLite `.backup`, rotación 14 días; programar con cron).
- **Red**: use una red local aislada; con los tokens definidos, la API acepta peticiones de la red. **Sin tokens solo acepta localhost.** No exponga el panel fuera de la red local de confianza (p. ej. túnel SSH o firewall `ufw allow from 192.168.1.0/24`). Roles y autenticación: `docs/SEGURIDAD.md`.
- Pendiente: HTTPS, prueba de arranque tras corte de luz, instalación real en una Pi.
