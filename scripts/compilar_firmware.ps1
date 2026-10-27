# Compila el firmware de la terminal (ESP32-S3) sin tocar su config_dispositivo.h real ni cargar nada a la placa.
# Hace una COPIA del sketch en una carpeta temporal, le pone un config_dispositivo.h generado desde el .example
# (solo valores de relleno: NO hay credenciales) y ejecuta arduino-cli. Muestra tamano de flash/RAM y advertencias.
#
# Uso (desde la raiz del repo):
#   powershell -ExecutionPolicy Bypass -File scripts\compilar_firmware.ps1                 # HTTP y HTTPS
#   powershell -ExecutionPolicy Bypass -File scripts\compilar_firmware.ps1 -Variante http
#   powershell -ExecutionPolicy Bypass -File scripts\compilar_firmware.ps1 -Variante https -UrlHttps https://miapi.example/api/v1
# Compilar NO prueba el firmware en la placa.
param(
  [ValidateSet("http", "https", "ambas")] [string]$Variante = "ambas",
  [string]$UrlHttps = "https://ejemplo.invalid/api/v1",
  [string]$UrlHttp = "http://192.168.1.50:8000/api/v1",
  [string]$Fqbn = "esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,FlashMode=qio",
  [string]$CliPath = $env:HIDRO360_ARDUINO_CLI,
  [switch]$Insecure   # compila ademas con API_TLS_INSECURE (solo para comprobar que la macro funciona)
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$origen = Join-Path $raiz "firmware\esp32s3_monitor"

function Buscar-Cli {
  if ($CliPath -and (Test-Path $CliPath)) { return $CliPath }
  $c = Get-Command arduino-cli -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @("$env:LOCALAPPDATA\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
                   "$env:ProgramFiles\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe")) {
    if (Test-Path $p) { return $p }
  }
  throw "No se encontro arduino-cli. Defina HIDRO360_ARDUINO_CLI o instale Arduino IDE 2.x."
}
$cli = Buscar-Cli
Write-Host "arduino-cli: $cli"
& $cli version

function Compilar([string]$nombre, [string]$url, [string]$extra) {
  $base = Join-Path ([IO.Path]::GetTempPath()) "hidro360_fw_$nombre"
  # Solo se recrea la copia del sketch; la carpeta "build" se conserva entre ejecuciones (compilacion incremental:
  # la primera vez compila el core y las librerias y tarda varios minutos en Windows; las siguientes, segundos).
  $sketch = Join-Path $base "esp32s3_monitor"      # la carpeta debe llamarse como el .ino
  if (Test-Path $sketch) { Remove-Item -Recurse -Force $sketch }
  New-Item -ItemType Directory -Force $sketch | Out-Null
  Copy-Item (Join-Path $origen "esp32s3_monitor.ino") $sketch
  $cfg = Get-Content (Join-Path $origen "config_dispositivo.h.example") -Raw
  $cfg = [regex]::Replace($cfg, '(?m)^#define API_BASE_URL\s+".*"', "#define API_BASE_URL   `"$url`"")
  if ($extra) { $cfg += "`r`n$extra`r`n" }
  Set-Content -Path (Join-Path $sketch "config_dispositivo.h") -Value $cfg -Encoding ascii
  Write-Host "`n=== Variante $nombre : API_BASE_URL=$url $extra ==="
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  $salida = & $cli compile --fqbn $Fqbn --build-path (Join-Path $base "build") --warnings default $sketch 2>&1 | Out-String
  $codigo = $LASTEXITCODE
  $ErrorActionPreference = $prev
  $salida -split "`r?`n" | Where-Object { $_ -match "warning|error|Sketch us|Global|variables|#warning" -and $_ -notmatch "TOUCH_CS" } | ForEach-Object { Write-Host $_ }
  if ($salida -match "TOUCH_CS") { Write-Host "(advertencia inofensiva de TFT_eSPI: TOUCH_CS no definido; el tactil es I2C)" }
  if ($codigo -ne 0) { Write-Host $salida; throw "Fallo la compilacion de la variante $nombre (codigo $codigo)" }
  Write-Host "OK: $nombre compila. Binario en $(Join-Path $base 'build')"
}

if ($Variante -in @("http", "ambas")) { Compilar "http" $UrlHttp "" }
if ($Variante -in @("https", "ambas")) { Compilar "https" $UrlHttps "" }
if ($Insecure) { Compilar "https_insecure" $UrlHttps "#define API_TLS_INSECURE" }
Write-Host "`nListo. Nada se cargo a la placa y config_dispositivo.h real no se toco."
