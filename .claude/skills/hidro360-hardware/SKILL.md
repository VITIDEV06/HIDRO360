---
name: hidro360-hardware
description: Usar al trabajar con la pantalla ESP32-S3 Hosyond 2.8", la Raspberry Pi 4 o el contrato dispositivo-API de HIDRO360. Distingue simulación de prueba física y evita modificar firmware sin revisar el hardware real.
---

# Hardware y dispositivos

Detalle: `docs/HARDWARE.md`. Código: `firmware/esp32s3_monitor/` (tablero), `firmware/pruebas/led_rgb/` (prueba del LED RGB, pin 42), `deploy/raspberry/`.

## Contrato (no se rompe)
- El dispositivo **solo lee** `GET /api/v1/estado` y **publica** `POST /api/v1/dispositivos/confirmaciones` con el **token de dispositivo** (`X-API-Token`). No aprueba planes, no lee reportes, **nunca** toca SQLite.
- `/estado` no incluye borradores de comunicación ni textos de ciudadanos. Cambiar ese JSON = cambio de contrato (`SCHEMA_VERSION`, `docs/API.md`) y revisar el parseo del firmware (`esp32s3_monitor.ino`, filtro ArduinoJson).
- El firmware debe tolerar WiFi caído, timeout, HTTP ≠ 200 y JSON inválido mostrando el error (no colgarse).

## Hardware real (lcdwiki 2.8inch_ESP32-S3_Display; SKU ES3C28P con táctil / ES3N28P sin táctil; módulo N16R8)
Tabla de pines completa con fuente y estado en `docs/HARDWARE.md` §2. Resumen: TFT ILI9341V CS10 DC46 SCLK12 MOSI11 MISO13 BL45 · táctil FT6336G SDA16 SCL15 INT17 RST18 · LED RGB IO42 · microSD CLK38 CMD40 D0-3 39/41/48/47 · I2S 4/5/6/7/8 e IO1 (amplificador) · BOOT IO0 · batería IO9. **Pines libres: solo GPIO2/3/14/21 (conector de expansión, según la wiki); el orden de pines de los conectores es «no verificado». No suponga que otros GPIO están libres.** `TFT_eSPI/User_Setup.h` del paquete del fabricante coincide; los comentarios de pines de `Demo/` están desactualizados. Librerías en `Documentos/Arduino/libraries` (no dentro del sketch). Hay dos carpetas sin `library.properties` (FT6336-arduino, XT_DAC_Audio): por eso falla instalarlas como ZIP; cópielas a mano.

## Reglas
1. **Simulación ≠ prueba física.** Probar la API con `TestClient`/`curl` no prueba el firmware. Marque como «pendiente de prueba física»: compilación, táctil, WiFi, consumo, arranque tras corte de luz.
2. No modifique firmware sin releer la wiki y los pines de arriba; no añada sensores ni GPIO (la propuesta lo descarta); el firmware usa el token de DISPOSITIVO, nunca el de operador.
3. Config de dispositivo (WiFi, IP, token) en `config_dispositivo.h` (en `.gitignore`); solo se versiona el `.example`.
4. Raspberry: servicios systemd en `deploy/raspberry/` (UI Flet modo web + API; nombres `hidro360-api` y `hidro360-ui`). Sin tokens la API solo acepta localhost; con tokens acepta la red local. Roles y login de administración: `docs/SEGURIDAD.md`; mantenga la Pi en red de confianza. El navegador cliente necesita internet para cargar Flet web (CanvasKit/fuentes), la Pi no.

## Pasos para un cambio de firmware
1. Leer wiki + contrato. 2. Cambiar. 3. Compilar (Arduino IDE: ESP32S3 Dev Module, PSRAM OPI, 16 MB) — si no puede compilar aquí, dígalo. 4. Probar en la placa con un servidor local con tokens. 5. Actualizar `docs/HARDWARE.md` con lo realmente verificado.

## Verificación
`pytest -q tests/test_backend.py` (contrato y autorización de dispositivos) · en placa: la pantalla muestra «SIN DATOS» al apagar la API y se recupera al volver; la confirmación aparece en Auditoría con `dispositivo:<id>`.
