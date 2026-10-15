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
# Usage:   start.ps1          (double-click start.bat, or from PowerShell:
#                             powershell -ExecutionPolicy Bypass -File start.ps1)
# Options: -Port <n>  override the database port (default: read from .env,
#                     otherwise 5432)
#          -OpenBrowser 0   skip auto-opening the browser
# Env:     MAILIGENCE_PYTHON   full path to the interpreter used to build
#                     backend/.venv; must be Python 3.11 or 3.12
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
# Locate pg_isready, used by Wait-PgReady below for a real readiness probe.
$PgIsReady = $null
if ($Bundled) {
    $candReady = Join-Path $PgBin 'pg_isready.exe'
    if (Test-Path $candReady) { $PgIsReady = $candReady }
}
if (-not $PgIsReady) {
    $cmdReady = Get-Command pg_isready -ErrorAction SilentlyContinue
    if ($cmdReady) { $PgIsReady = $cmdReady.Source }
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

# ---- Helpers ----------------------------------------------------------------
# Wait until PostgreSQL actually ACCEPTS connections. A listening TCP port is
# not enough: right after an unclean shutdown the postmaster binds the port
# while it is still replaying WAL, and every query fails with "the database
# system is starting up" until recovery completes. pg_isready performs a real
# connection attempt, so it only succeeds once the server is genuinely ready.
function Wait-PgReady {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSec = 60
    )
    # Deadline-based rather than attempt-based: a probe against a port that is
    # listening but still replaying WAL answers at once, but one against a port
    # nothing answers can burn ~2s in TCP retries. Counting attempts would let
    # the loop overshoot TimeoutSec several-fold; a deadline honours it.
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ($PgIsReady) {
            # A throw here just means "not ready yet" (see the note below).
            try {
                & $PgIsReady -h 127.0.0.1 -p $Port -U postgres -q | Out-Null
                if ($LASTEXITCODE -eq 0) { return $true }
            } catch { }
        } else {
            # pg_isready missing (very old client): fall back to a real query.
            $env:PGPASSWORD = 'postgres_dev_pw'
            try {
                # NOTE: under $ErrorActionPreference='Stop', redirecting a native
                # command's stderr raises NativeCommandError and terminates the
                # statement. psql does exactly that while PG replays WAL ("the
                # database system is starting up"), so the throw caught below is
                # the normal "not ready yet" signal and must NOT abort the loop.
                & $Psql -h 127.0.0.1 -p $Port -U postgres -tA -c 'SELECT 1' 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { return $true }
            } catch { } finally {
                Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# ---- 3. Start PostgreSQL ----------------------------------------------------
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    if ($Bundled) {
        if (-not (Test-Path $PgData)) {
            Write-Host "[3/6] First run: initializing data directory..."
            $initdb = Join-Path $PgBin 'initdb.exe'
            if (-not (Test-Path $initdb)) { throw "initdb.exe not found in $PgBin" }
            # Use scram auth + the same postgres password the script connects
            # with below, so the auth setup and PGPASSWORD stay consistent
            # (and no misleading trust mode that ignores passwords).
            $pwFile = Join-Path $PgInstall 'pgpw.txt'
            Set-Content -Path $pwFile -Value 'postgres_dev_pw' -NoNewline
            try {
                & $initdb -D $PgData -U postgres -A scram-sha-256 --pwfile=$pwFile -E UTF8 2>&1 | Out-Null
            } catch {
                # initdb logs progress to stderr, and the redirect above can raise
                # NativeCommandError under $ErrorActionPreference='Stop'. Whether
                # it really succeeded is checked right below via the data dir, so
                # don't abort the script from here.
                Write-Host "  initdb reported errors; verifying the data directory ..." -ForegroundColor Yellow
            } finally {
                Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
            }
            if (-not (Test-Path $PgData)) { throw "initdb failed" }
        }
        Write-Host "[3/6] Starting PostgreSQL on port $Port ..."
        # Robust start. pg_ctl spawns the postmaster as a console app that
        # inherits this script's output handles, so a plain `& pg_ctl ... |
        # Out-Null` never returns (the pipeline stays open while postgres runs)
        # and the script appears frozen at this step even though the database
        # actually came up. Launch pg_ctl detached and wait on the process
        # instead — this also keeps postgres out of the user's console, so
        # closing the launcher window no longer Ctrl+C-kills the database.
        #
        # Clean up a stale postmaster.pid left behind by an unclean shutdown
        # (e.g. closing the console while postgres was running) so a leftover
        # lock file owned by no live server never blocks startup.
        $pidFile = Join-Path $PgData 'postmaster.pid'
        if (Test-Path $pidFile) {
            try {
                $oldPid = [int]((Get-Content $pidFile -TotalCount 1).Trim())
                $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
                if (-not $oldProc -or $oldProc.ProcessName -ne 'postgres') {
                    Write-Host "  Removing stale postmaster.pid (PID $oldPid is no longer a live postgres)"
                    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
                    Remove-Item (Join-Path $PgData 'postmaster.opts') -Force -ErrorAction SilentlyContinue
                }
            } catch {
                # Unreadable pid file and no listener on the port: it can only
                # be stale, so drop it.
                Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            }
        }
        # Start pg_ctl fully detached. Do NOT use -Wait: waiting on pg_ctl
        # hangs while the spawned postmaster keeps the console handles open.
        # Readiness is verified below by polling the port instead.
        $ctlArgs = "-D `"$PgData`" -l `"$PgLog`" -o `"-p $Port`" start"
        Start-Process -FilePath $PgCtl -ArgumentList $ctlArgs -WindowStyle Hidden | Out-Null
        $pgUp = $false
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Milliseconds 500
            if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
                $pgUp = $true
                break
            }
        }
        if (-not $pgUp) {
            Write-Host "PostgreSQL did not become ready on port $Port. See $PgLog" -ForegroundColor Red
            if (Test-Path $PgLog) { Get-Content $PgLog -Tail 10 }
            exit 1
        }
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
# A listening port does NOT mean the server accepts queries (see Wait-PgReady),
# so gate here for every path — bundled start, system service and the
# "already listening" case — before touching the role/database below.
Write-Host "[3/6] Waiting for PostgreSQL to accept connections ..."
if (-not (Wait-PgReady -Port $Port -TimeoutSec 60)) {
    Write-Host "PostgreSQL is listening on port $Port but is not accepting connections. See $PgLog" -ForegroundColor Red
    if (Test-Path $PgLog) { Get-Content $PgLog -Tail 10 }
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[3/6] PostgreSQL is ready"

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

# ---- 5. Backend deps (self-healing) + .env -----------------------------------
$VenvDir = Join-Path $BackendDir '.venv'
$pyExe   = Join-Path $VenvDir 'Scripts\python.exe'

# Run a native command and return its exit code. $ErrorActionPreference is
# 'Stop' script-wide, and under it ANY stderr line from a native tool becomes a
# terminating NativeCommandError. That is not theoretical: it aborted the very
# `pip install` whose job was to repair a broken venv (pip writes an upgrade
# notice to stderr). Native calls go through here instead, and success is judged
# by the exit code rather than by whether the tool happened to be chatty.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Pipe to Out-Host so the tool's output stays visible to the user but does
        # NOT leak into this function's output stream. A bare `& $Exe @Arguments`
        # would hand the caller the tool's entire stdout followed by the exit
        # code, and `$exit = Invoke-Native ...` would then be an array, making a
        # check such as `$exit -ne 0` run against the text instead of the code.
        & $Exe @Arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Same relaxation, but capturing stdout — used to probe interpreter versions and
# whether the pinned deps import.
function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = (& $Exe @Arguments 2>$null | Out-String).Trim()
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Text = $text }
    } catch {
        return [pscustomobject]@{ ExitCode = -1; Text = '' }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# A venv that merely EXISTS can still be unusable, and that is exactly how the
# earlier failure stayed silent: an aborted install leaves a shell with only pip
# inside, or the base interpreter recorded in pyvenv.cfg is later removed
# (uv-managed / throwaway conda envs), and the backend then dies at runtime with
# "ModuleNotFoundError: pydantic". So probe the venv for real instead of
# trusting the folder's presence, and rebuild it when it is unhealthy.
function Test-VenvHealthy {
    param([string]$Dir)
    $exe = Join-Path $Dir 'Scripts\python.exe'
    if (-not (Test-Path $exe)) { return $false }

    # pyvenv.cfg records the interpreter the venv was built from; if that base is
    # gone the venv can no longer run, however intact its folders look.
    $cfg = Join-Path $Dir 'pyvenv.cfg'
    if (-not (Test-Path $cfg)) { return $false }
    $cfgHome = $null
    $cfgVer  = $null
    foreach ($line in Get-Content $cfg) {
        if ($line -match '^\s*home\s*=\s*(.+)$')    { $cfgHome = $Matches[1].Trim() }
        if ($line -match '^\s*version\s*=\s*(.+)$') { $cfgVer  = $Matches[1].Trim() }
    }
    if ($cfgHome -and -not (Test-Path (Join-Path $cfgHome 'python.exe'))) {
        Write-Host "  venv base interpreter is gone: $cfgHome" -ForegroundColor Yellow
        return $false
    }
    if ($cfgVer -and $cfgVer -notmatch '^3\.(11|12)\.') {
        Write-Host "  venv was built on unsupported Python $cfgVer" -ForegroundColor Yellow
        return $false
    }

    # Finally prove the pinned deps actually import — the check that would have
    # caught the original incident.
    $probe = Invoke-NativeCapture -Exe $exe -Arguments @('-c', 'import pydantic, psycopg, sqlalchemy, fastapi, uvicorn')
    return ($probe.ExitCode -eq 0)
}

# Stop anything still running out of this venv before deleting it: a live
# python.exe from there holds the files open and makes the removal fail.
function Stop-VenvProcesses {
    param([string]$Dir)
    foreach ($p in @(Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($p.Path -and $p.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase)) {
                Write-Host "  Stopping $($p.ProcessName) (PID $($p.Id)) still using $Dir" -ForegroundColor Yellow
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
}

if (-not (Test-VenvHealthy -Dir $VenvDir)) {
    if (Test-Path $VenvDir) {
        Write-Host "[5/6] backend/.venv is present but unusable - rebuilding it"
        Stop-VenvProcesses -Dir $VenvDir
        try {
            Remove-Item $VenvDir -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Host "Could not delete $VenvDir : $($_.Exception.Message)" -ForegroundColor Red
            Write-Host "Close anything using it, then rerun this script."
            Read-Host "Press Enter to exit"
            exit 1
        }
    } else {
        Write-Host "[5/6] Creating Python venv and installing backend deps..."
    }

    # Collect candidate interpreters and keep the first one whose version the
    # pinned deps have wheels for. Plain `python` from PATH is whatever the user
    # installed last and is often too new: on 3.13/3.14 pip cannot resolve wheels
    # for pins such as pydantic 2.9.2, the install fails and the backend then
    # dies with ModuleNotFoundError: pydantic (leaving a venv that looks fine).
    # So never build from a candidate without checking its version first.
    $candidates = @()
    if ($env:MAILIGENCE_PYTHON) { $candidates += $env:MAILIGENCE_PYTHON }

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $launcherPath = $launcher.Source
        foreach ($v in @('-3.12', '-3.11')) {
            try {
                $cand = & $launcherPath $v -c "import sys; print(sys.executable)" 2>$null
                if ($cand) { $candidates += $cand.Trim() }
            } catch { }
        }
    }

    # Conda envs are a common place for a working 3.11/3.12 to live when the
    # `python` on PATH is a too-new base install.
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($conda) {
        try {
            $envList = (& $conda.Source env list --json 2>$null | Out-String) | ConvertFrom-Json
            if ($envList -and $envList.envs) {
                foreach ($envPath in $envList.envs) {
                    foreach ($sub in @('python.exe', 'Scripts\python.exe')) {
                        $cand = Join-Path $envPath $sub
                        if (Test-Path $cand) { $candidates += $cand; break }
                    }
                }
            }
        } catch { }
    }

    $cmdPy = Get-Command python -ErrorAction SilentlyContinue
    if ($cmdPy) { $candidates += $cmdPy.Source }

    $basePy = $null
    $pyVer  = $null
    foreach ($cand in $candidates) {
        $probe = Invoke-NativeCapture -Exe $cand -Arguments @('-c', "import sys; print('%d.%d' % sys.version_info[:2])")
        $v = if ($probe.ExitCode -eq 0 -and $probe.Text) { $probe.Text } else { '?' }
        if ($v -eq '3.11' -or $v -eq '3.12') { $basePy = $cand; $pyVer = $v; break }
        Write-Host "  skipping $cand (Python $v)" -ForegroundColor DarkGray
    }
    if (-not $basePy) {
        Write-Host ""
        Write-Host "No Python 3.11/3.12 interpreter found." -ForegroundColor Red
        Write-Host "backend/requirements.txt pins packages (pydantic 2.9.2, psycopg 3.3.4, ...)"
        Write-Host "that only ship wheels up to 3.12; installing on 3.13+ fails and the"
        Write-Host "backend then crashes with 'ModuleNotFoundError: pydantic'."
        Write-Host ""
        Write-Host "Install Python 3.12, or point the script at an existing 3.11/3.12:"
        Write-Host "  `$env:MAILIGENCE_PYTHON = '<python.exe of a 3.11/3.12 env>'"
        Write-Host "then rerun this script."
        Write-Host ""
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "[5/6] Base interpreter: $basePy (Python $pyVer)"

    $venvExit = Invoke-Native -Exe $basePy -Arguments @('-m', 'venv', $VenvDir)
    if ($venvExit -ne 0 -or -not (Test-Path $pyExe)) {
        throw "Failed to create venv with $basePy (exit code $venvExit)"
    }

    # Deliberately NOT using pip's --quiet: a silent install is how the original
    # broken venv went unnoticed. Output stays visible, and the exit code decides.
    $pipExit = Invoke-Native -Exe $pyExe -Arguments @('-m', 'pip', 'install', '-r', (Join-Path $BackendDir 'requirements.txt'))
    if ($pipExit -ne 0) {
        Write-Host "pip install failed (exit code $pipExit). See the output above." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }

    # The point of the whole rebuild: never hand a broken venv to the backend.
    if (-not (Test-VenvHealthy -Dir $VenvDir)) {
        Write-Host "backend/.venv is still unusable after the rebuild - see the pip output above." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "[5/6] backend/.venv is healthy" -ForegroundColor Green
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
    $npmInstall = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npmInstall) { $npmInstall = 'npm.cmd' }
    Push-Location $FrontendDir
    try {
        # Same native/stderr hazard as the pip call above: npm prints deprecation
        # and funding notices to stderr, which under $ErrorActionPreference='Stop'
        # becomes a terminating NativeCommandError and aborts an otherwise fine
        # install. Run it with the error preference relaxed and judge by exit code.
        $npmExit = Invoke-Native -Exe $npmInstall -Arguments @('install')
    } finally {
        Pop-Location
    }
    if ($npmExit -ne 0) {
        Write-Host "npm install failed (exit code $npmExit). See the output above." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
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
    # Drop the old logs so a stale stack trace can never be mistaken for a
    # failure of the run that is starting now.
    Remove-Item $out, $err -Force -ErrorAction SilentlyContinue
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
    Remove-Item $fout, $ferr -Force -ErrorAction SilentlyContinue
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
