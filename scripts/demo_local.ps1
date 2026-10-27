# HIDRO360 - modo demostración local (Windows, PowerShell).
# - Fuerza SQLite: no usa DATABASE_URL ni Supabase aunque exista en .env.
# - Guarda los reportes en datos_demo\hidro360_demo.db (archivo nuevo; no toca agua_clara.db).
# - Si el puerto está ocupado, usa el siguiente libre. NUNCA detiene procesos ajenos.
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts/demo_local.ps1 [-Puerto 8501]
#       ... -CrearAdmin     crea una cuenta de administración en la base de demostración (pide la contraseña sin mostrarla)
#       ... -RestablecerAdmin   cambia la contraseña de una cuenta existente
#       ... -Bot            inicia el bot de Telegram (otra terminal) con la MISMA base SQLite de la demostración
#       ... -ListarAdmins   muestra usuario, rol y estado de las cuentas guardadas (nunca contraseñas)

param([int]$Puerto = 8501, [switch]$CrearAdmin, [switch]$RestablecerAdmin, [switch]$ListarAdmins, [switch]$Bot)

$raiz = Split-Path -Parent $PSScriptRoot
$python = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No existe el entorno virtual. Créelo con: python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

$carpeta = Join-Path $raiz "datos_demo"
New-Item -ItemType Directory -Force -Path $carpeta | Out-Null

# Variables SOLO para este proceso. DATABASE_URL vacía impide que config.py lea la de .env.
$env:HIDRO360_BD = "sqlite"
$env:HIDRO360_ENV = "dev"
$env:HIDRO360_DB = Join-Path $carpeta "hidro360_demo.db"
$env:DATABASE_URL = ""
$env:HIDRO360_STORAGE = "local"                        # fotos y audios en disco local (nunca Supabase Storage)
$env:HIDRO360_TRACE = Join-Path $carpeta "trazas_demo.jsonl"   # trazas a archivo (no a la consola)

Set-Location $raiz
if ($CrearAdmin) {
    # Mismo entorno que la demo: la cuenta queda en datos_demo\hidro360_demo.db, nunca en Supabase.
    & $python -m scripts.crear_admin
    exit $LASTEXITCODE
}
if ($RestablecerAdmin) {
    # Cambia la contraseña de una cuenta que YA existe (CrearAdmin nunca reemplaza cuentas).
    & $python -m scripts.crear_admin --restablecer
    exit $LASTEXITCODE
}
if ($Bot) {
    & $python -m scripts.bot_telegram
    exit $LASTEXITCODE
}
if ($ListarAdmins) {
    & $python -c "from services import auth; [print(u['usuario'], '|', u['rol'], '|', 'activo' if u.get('activo', True) else 'desactivado') for u in auth.listar_usuarios()] or print('Sin cuentas: use -CrearAdmin')"
    exit $LASTEXITCODE
}

function Puerto-Libre([int]$p) {
    return -not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

$elegido = $Puerto
while (-not (Puerto-Libre $elegido)) {
    $dueno = Get-NetTCPConnection -LocalPort $elegido -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    Write-Host "Puerto $elegido ocupado por el proceso $($dueno.OwningProcess). Pruebo el siguiente." -ForegroundColor Yellow
    $elegido++
    if ($elegido -gt $Puerto + 20) { Write-Host "No hay puertos libres entre $Puerto y $($Puerto + 20)." -ForegroundColor Red; exit 1 }
}

$url = "http://127.0.0.1:$elegido/"
Write-Host "HIDRO360 demostración local (SQLite: $($env:HIDRO360_DB))" -ForegroundColor Cyan
Write-Host "Abra: $url" -ForegroundColor Green
Write-Host "Para detener esta instancia, pulse Ctrl+C en esta ventana."

Start-Job -ScriptBlock {
    param($u)
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        try { Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 3 | Out-Null; Start-Process $u; return }
        catch { }
    }
} -ArgumentList $url | Out-Null

Set-Location $raiz
& $python app.py --web --host 127.0.0.1 --port $elegido --sin-navegador
