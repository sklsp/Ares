<#
.SYNOPSIS
    One-click launcher for Ares.

.DESCRIPTION
    Starts the backend API and frontend console, prepares the database,
    ensures a local admin account exists, and opens the browser.

    Usage:
        powershell -File scripts\start.ps1          # start everything
        powershell -File scripts\start.ps1 -Stop    # stop everything

    Safe to run repeatedly: services already running are left alone.
#>
param(
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$VenvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
$RunDir = Join-Path $Root '.run'
$LogDir = Join-Path $Root 'logs'

function Test-PortInUse {
    param([int]$Port)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        if (-not $task.Wait(500)) { return $false }
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Wait-ForUrl {
    param([string]$Url, [int]$TimeoutSeconds = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 600 }
    }
    return $false
}

function Stop-TrackedService {
    param([string]$Name)
    $pidFile = Join-Path $RunDir "$Name.pid"
    if (Test-Path $pidFile) {
        $procId = Get-Content $pidFile -ErrorAction SilentlyContinue
        if ($procId) {
            try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Host "  stopped $Name (pid $procId)" }
            catch { Write-Host "  $Name was not running" }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

if ($Stop) {
    Write-Host 'Stopping Ares...'
    Stop-TrackedService -Name 'frontend'
    Stop-TrackedService -Name 'backend'
    # Fallback: free the ports even if pid files were lost.
    foreach ($port in 8000, 3000) {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) {
            try { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop; Write-Host "  freed port $port" } catch { }
        }
    }
    Write-Host 'Done.'
    exit 0
}

Write-Host 'Starting Ares...'
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

# --- 1. Backend dependencies -------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Host '  creating Python environment (first run only)...'
    $py = (Get-Command py -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (Get-Command python -ErrorAction Stop).Source }
    & $py -m venv (Join-Path $BackendDir '.venv')
    & $VenvPython -m pip install --quiet -r (Join-Path $BackendDir 'requirements-dev.txt')
}

# --- 2. Frontend dependencies -------------------------------------------------
if (-not (Test-Path (Join-Path $Root 'node_modules'))) {
    Write-Host '  installing frontend packages (first run only)...'
    Push-Location $Root
    & npm.cmd install --no-fund --no-audit 2>&1 | Out-Null
    Pop-Location
}

# --- 3. Backend ----------------------------------------------------------------
if (Test-PortInUse -Port 8000) {
    Write-Host '  backend already running on :8000'
} else {
    Write-Host '  starting backend...'
    $out = Join-Path $LogDir 'backend.log'
    $err = Join-Path $LogDir 'backend.err.log'
    $proc = Start-Process -FilePath $VenvPython `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $BackendDir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Set-Content -Path (Join-Path $RunDir 'backend.pid') -Value $proc.Id
}

if (-not (Wait-ForUrl -Url 'http://127.0.0.1:8000/ready' -TimeoutSeconds 90)) {
    Write-Host '  ERROR: backend did not become ready. See logs\backend.err.log' -ForegroundColor Red
    exit 1
}
Write-Host '  backend ready on :8000'

# --- 4. Database (idempotent: keeps existing data) ------------------------------
# Alembic logs to stderr; suppress locally so it is not treated as a failure.
Push-Location $BackendDir
$previousEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $VenvPython -m app.seed --keep 2>&1 | Out-Null
$ErrorActionPreference = $previousEap
Pop-Location

# --- 5. Local admin account ------------------------------------------------------
$adminEmail = 'admin@ares.local'
$adminPassword = 'ares-local-admin'
try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8000/auth/register' -Method Post `
        -ContentType 'application/json' `
        -Body (@{ email = $adminEmail; password = $adminPassword; role = 'admin' } | ConvertTo-Json) | Out-Null
    Write-Host "  created admin account: $adminEmail"
} catch {
    Write-Host "  admin account already exists: $adminEmail"
}

# --- 6. Frontend -----------------------------------------------------------------
if (Test-PortInUse -Port 3000) {
    Write-Host '  frontend already running on :3000'
} else {
    Write-Host '  starting frontend...'
    $out = Join-Path $LogDir 'frontend.log'
    $err = Join-Path $LogDir 'frontend.err.log'
    $npm = Join-Path $Root 'node_modules\.bin\next.cmd'
    if (Test-Path $npm) {
        $proc = Start-Process -FilePath $npm -ArgumentList 'dev' `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    } else {
        $proc = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev' `
            -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    }
    Set-Content -Path (Join-Path $RunDir 'frontend.pid') -Value $proc.Id
}

if (-not (Wait-ForUrl -Url 'http://localhost:3000' -TimeoutSeconds 120)) {
    Write-Host '  WARNING: frontend did not respond yet. See logs\frontend.err.log' -ForegroundColor Yellow
} else {
    Write-Host '  frontend ready on :3000'
}

# --- 7. Open the console -----------------------------------------------------------
Start-Process 'http://localhost:3000/login'

Write-Host ''
Write-Host 'Ares is running.' -ForegroundColor Green
Write-Host '  Console : http://localhost:3000'
Write-Host "  Login   : $adminEmail / $adminPassword"
Write-Host '  API docs: http://localhost:8000/docs'
Write-Host ''
Write-Host 'To stop everything: scripts\stop-ares.bat (or .\scripts\start.ps1 -Stop)'
