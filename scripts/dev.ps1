<#
.SYNOPSIS
  Flujo de desarrollo de HIDRO360 en Windows (PowerShell 5.1+).

.DESCRIPTION
  Crea/usa .venv, instala dependencias, crea .env desde .env.example SIN pisarlo y lanza API y/o interfaz.
  Ejecutar desde cualquier carpeta:   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 <accion>
  (o, ya dentro de PowerShell en la raiz:   .\scripts\dev.ps1 <accion>)

  Acciones:
    instalar   venv + pip install -r requirements.txt + .env (por defecto)
    admin      crea el primer administrador (pide usuario y contrasena por consola)
    ui         ventana de escritorio (python app.py)
    web        UI en el navegador (http://127.0.0.1:8501)
    api        API FastAPI en http://127.0.0.1:8000  (/docs)
    todo       API en una ventana nueva + UI de escritorio
    pruebas    pytest -q
    secretos   revisa que no haya claves en los archivos versionados
    vercel     vercel dev (requiere Vercel CLI y sesion; ver docs/DESPLIEGUE.md)

  Nunca imprime ni escribe claves. DATABASE_URL y demas secretos se ponen en .env (ignorado por Git).
#>
[CmdletBinding()]
param(
    [ValidateSet('instalar', 'admin', 'ui', 'web', 'api', 'todo', 'pruebas', 'secretos', 'vercel')]
    [string]$Accion = 'instalar',
    [int]$PuertoApi = 8000,
    [int]$PuertoWeb = 8501
)

$ErrorActionPreference = 'Stop'
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz
$Py = Join-Path $Raiz '.venv\Scripts\python.exe'

function Asegurar-Venv {
    if (-not (Test-Path $Py)) {
        Write-Host '== Creando .venv ...'
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) { $cmd = Get-Command py -ErrorAction SilentlyContinue }
        if (-not $cmd) { throw 'No se encontro Python 3.10+ en el PATH.' }
        & $cmd.Source -m venv .venv
    }
}

function Asegurar-Dependencias {
    Asegurar-Venv
    # Reinstala solo si requirements cambio desde la ultima vez (marca en .venv)
    $marca = Join-Path $Raiz '.venv\.requisitos.hash'
    $hash = (Get-FileHash requirements.txt, requirements-servidor.txt -Algorithm SHA256 | ForEach-Object Hash) -join ''
    if (-not (Test-Path $marca) -or (Get-Content $marca -Raw).Trim() -ne $hash) {
        Write-Host '== Instalando dependencias (requirements.txt) ...'
        & $Py -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'pip fallo.' }
        Set-Content -Path $marca -Value $hash -Encoding ascii
    }
}

function Asegurar-Env {
    if (-not (Test-Path '.env')) {
        Copy-Item '.env.example' '.env'
        Write-Host '== Creado .env desde .env.example (ignorado por Git). Edite solo lo que necesite.'
    } else {
        Write-Host '== .env ya existe: no se modifica.'
    }
}

switch ($Accion) {
    'instalar' { Asegurar-Dependencias; Asegurar-Env
        Write-Host "`nListo. Siguiente: .\scripts\dev.ps1 admin   y luego   .\scripts\dev.ps1 ui" }
    'admin'    { Asegurar-Dependencias; & $Py -m scripts.crear_admin }
    'ui'       { Asegurar-Dependencias; Asegurar-Env; & $Py app.py }
    'web'      { Asegurar-Dependencias; Asegurar-Env; & $Py app.py --web --port $PuertoWeb }
    'api'      { Asegurar-Dependencias; Asegurar-Env
        Write-Host "API: http://127.0.0.1:$PuertoApi/api/v1/salud  (docs: /docs)"
        & $Py -m uvicorn api.main:app --port $PuertoApi }
    'todo'     { Asegurar-Dependencias; Asegurar-Env
        Start-Process -FilePath $Py -ArgumentList @('-m', 'uvicorn', 'api.main:app', '--port', $PuertoApi) -WorkingDirectory $Raiz
        Write-Host "API lanzada en otra ventana (puerto $PuertoApi). Iniciando la interfaz ..."
        & $Py app.py }
    'pruebas'  { Asegurar-Dependencias; & $Py -m pytest -q }
    'secretos' { Asegurar-Venv; & $Py -m scripts.revisar_secretos; exit $LASTEXITCODE }
    'vercel'   {
        if (-not (Get-Command vercel -ErrorAction SilentlyContinue)) {
            throw 'Vercel CLI no esta instalada. Use:  npx vercel@latest dev   (ver docs/DESPLIEGUE.md)'
        }
        vercel dev }
}
