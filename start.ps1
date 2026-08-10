# ============================================================================
# Mailigence - One-click launcher (Windows PowerShell 5.1 compatible)
#
# What this script does automatically:
#   1. Finds a PostgreSQL: bundled portable build (.pginstall) or system install
#   2. Initializes the data directory on first run and starts the server
#   3. Creates the "mailigence" role + database if missing
#   4. Creates backend/.venv + installs dependencies on first run
#   5. Generates backend/.env (with a fresh Fernet key) on first run
#   6. Installs frontend deps on first run
#   7. Starts backend (:8000) + frontend (:5173) and opens the browser
#
# Usage:   .\start.ps1          (or double-click start.bat)
# Options: -Port <n>  override the database port (default: read from .env,
#                     otherwise 5432)
#          -OpenBrowser 0   skip auto-opening the browser
# ============================================================================
param(
    [int]$Port = 0,
    [int]$OpenBrowser = 1
)

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$PgInstall  = Join-Path $Root '.pginstall'
$PgBin      = Join-Path $PgInstall 'pgsql\bin'
$PgData     = Join-Path $PgInstall 'pgdata'
$PgLog      = Join-Path $PgInstall 'pg.log'
$EnvFile    = Join-Path $BackendDir '.env'

# ---- 1. Resolve database port ---------------------------------------------
if ($Port -eq 0) {
    $Port = 5432
    if (Test-Path $EnvFile) {
        $urlLine = Get-Content $EnvFile | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
        if ($urlLine -match '@127\.0\.0\.1:(\d+)/') { $Port = [int]$Matches[1] }
    }
}
Write-Host "[1/6] Database port: $Port"

# ---- 2. Locate PostgreSQL --------------------------------------------------
$Psql = $null
$PgCtl = $null
$Bundled = $false
if (Test-Path (Join-Path $PgBin 'psql.exe')) {
    $Psql = Join-Path $PgBin 'psql.exe'
    $PgCtl = Join-Path $PgBin 'pg_ctl.exe'
    $Bundled = $true
    Write-Host "[2/6] Using bundled portable PostgreSQL (.pginstall)"
} else {
    $cmdPsql = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmdPsql) {
        $Psql = $cmdPsql.Source
        $PgCtl = (Get-Command pg_ctl -ErrorAction SilentlyContinue).Source
        Write-Host "[2/6] Using system PostgreSQL"
    }
}
if (-not $Psql) {
    Write-Host ""
    Write-Host "PostgreSQL was not found. Two options:" -ForegroundColor Yellow
    Write-Host "  A) Download the portable build and unzip it to:"
    Write-Host "     $PgInstall\pgsql   (keep the folder name 'pgsql')"
    Write-Host "     https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64-binaries.zip"
    Write-Host "  B) Install PostgreSQL 14+ and add its bin folder to PATH:"
    Write-Host "     https://www.postgresql.org/download/windows/"
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# ---- 3. Start PostgreSQL ----------------------------------------------------
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    if ($Bundled) {
        if (-not (Test-Path $PgData)) {
            Write-Host "[3/6] First run: initializing data directory..."
            $initdb = Join-Path $PgBin 'initdb.exe'
            if (-not (Test-Path $initdb)) { throw "initdb.exe not found in $PgBin" }
            & $initdb -D $PgData -U postgres -A trust -E UTF8 2>&1 | Out-Null
            if (-not (Test-Path $PgData)) { throw "initdb failed" }
        }
        Write-Host "[3/6] Starting PostgreSQL on port $Port ..."
        & $PgCtl -D $PgData -l $PgLog -o "-p $Port" -w start | Out-Null
    } else {
        Write-Host "[3/6] Trying to start the system PostgreSQL service..."
        try {
            $svc = Get-Service -ErrorAction Stop | Where-Object { $_.Name -like 'postgresql*' -and $_.Status -ne 'Running' } | Select-Object -First 1
            if ($svc) { Start-Service -Name $svc.Name }
        } catch { }
        Start-Sleep -Seconds 3
        $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $listening) {
            Write-Host "PostgreSQL is not running on port $Port. Please start it manually and rerun this script." -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host "[3/6] PostgreSQL already listening on port $Port"
}
Start-Sleep -Seconds 1

# ---- 4. Create role + database if missing ----------------------------------
$pgUser = 'mailigence'
$pgPass = 'mailigence_dev_pw'
$pgDb   = 'mailigence'
$env:PGPASSWORD = 'postgres_dev_pw'

$roleExists = & $Psql -h 127.0.0.1 -p $Port -U postgres -tA -c "SELECT 1 FROM pg_roles WHERE rolname='$pgUser'"
if ($roleExists -match '1') {
    Write-Host "[4/6] Role '$pgUser' already exists"
} else {
    Write-Host "[4/6] Creating role '$pgUser' ..."
    & $Psql -h 127.0.0.1 -p $Port -U postgres -c "CREATE ROLE $pgUser LOGIN PASSWORD '$pgPass'" | Out-Null
}
$dbExists = & $Psql -h 127.0.0.1 -p $Port -U postgres -tA -c "SELECT 1 FROM pg_database WHERE datname='$pgDb'"
if ($dbExists -match '1') {
    Write-Host "[4/6] Database '$pgDb' already exists"
} else {
    Write-Host "[4/6] Creating database '$pgDb' ..."
    & $Psql -h 127.0.0.1 -p $Port -U postgres -c "CREATE DATABASE $pgDb OWNER $pgUser" | Out-Null
}
Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue

# ---- 5. Backend deps + .env -------------------------------------------------
$pyExe = Join-Path $BackendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $pyExe)) {
    Write-Host "[5/6] Creating Python venv and installing backend deps..."
    python -m venv (Join-Path $BackendDir '.venv')
    if (-not (Test-Path $pyExe)) { throw "Failed to create venv. Is Python 3.11+ installed and on PATH?" }
    & $pyExe -m pip install -r (Join-Path $BackendDir 'requirements.txt') --quiet
}
if (-not (Test-Path $EnvFile)) {
    Write-Host "[5/6] Generating backend/.env with a fresh encryption key..."
    Copy-Item (Join-Path $BackendDir '.env.example') $EnvFile
    try {
        $key = & $pyExe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        $content = Get-Content $EnvFile -Raw
        $content = $content -replace '^CREDENTIAL_ENCRYPTION_KEY=.*$', "CREDENTIAL_ENCRYPTION_KEY=$key" -replace '(?m)^CREDENTIAL_ENCRYPTION_KEY=.*$', "CREDENTIAL_ENCRYPTION_KEY=$key"
        $content = $content -replace '@127\.0\.0\.1:\d+/', "@127.0.0.1:$Port/"
        [IO.File]::WriteAllText($EnvFile, $content)
    } catch {
        Write-Host "Warning: could not auto-generate encryption key - set CREDENTIAL_ENCRYPTION_KEY in backend/.env manually" -ForegroundColor Yellow
    }
}

# ---- 6. Frontend deps --------------------------------------------------------
if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
    Write-Host "[6/6] Installing frontend deps..."
    Push-Location $FrontendDir
    npm install --silent
    Pop-Location
}
Write-Host "[6/6] Dependencies ready"

# ---- Start backend -----------------------------------------------------------
$backendPort = 8000
$backendRunning = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
if ($backendRunning) {
    Write-Host "Backend already running on port $backendPort - skipping"
} else {
    Write-Host "Starting backend on :$backendPort ..."
    $out = Join-Path $BackendDir 'backend.log'
    $err = Join-Path $BackendDir 'backend.err.log'
    Start-Process -FilePath $pyExe -ArgumentList 'run.py' -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden | Out-Null
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 750
        try {
            $r = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
            if ($r.status -eq 'ok') { $ok = $true; break }
        } catch { }
    }
    if ($ok) {
        Write-Host "Backend is up (health OK)" -ForegroundColor Green
    } else {
        Write-Host "Backend failed to start. See $err" -ForegroundColor Red
        if (Test-Path $err) { Get-Content $err -Tail 10 }
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ---- Start frontend -----------------------------------------------------------
$frontPort = 5173
$frontRunning = Get-NetTCPConnection -LocalPort $frontPort -State Listen -ErrorAction SilentlyContinue
if ($frontRunning) {
    Write-Host "Frontend already running on port $frontPort - skipping"
} else {
    Write-Host "Starting frontend on :$frontPort ..."
    $fout = Join-Path $FrontendDir 'frontend.log'
    $ferr = Join-Path $FrontendDir 'frontend.err.log'
    $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npm) { $npm = 'npm.cmd' }
    Start-Process -FilePath $npm -ArgumentList 'run','dev' -WorkingDirectory $FrontendDir `
        -RedirectStandardOutput $fout -RedirectStandardError $ferr -WindowStyle Hidden | Out-Null
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 750
        try {
            $resp = Invoke-WebRequest "http://localhost:$frontPort" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { $ok = $true; break }
        } catch { }
    }
    if ($ok) {
        Write-Host "Frontend is up" -ForegroundColor Green
    } else {
        Write-Host "Frontend failed to start. See $ferr" -ForegroundColor Red
        if (Test-Path $ferr) { Get-Content $ferr -Tail 10 }
        Read-Host "Press Enter to exit"
        exit 1
    }
}

Write-Host ""
Write-Host "Mailigence is running:" -ForegroundColor Green
Write-Host "  Frontend:  http://localhost:5173"
Write-Host "  Backend:   http://localhost:8000/api/health"
Write-Host ""
if ($OpenBrowser -eq 1) {
    try { Start-Process "http://localhost:5173" } catch { }
}
