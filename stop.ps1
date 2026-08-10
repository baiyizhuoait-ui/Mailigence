# ============================================================================
# Mailigence - stop the backend and frontend (optionally the database too)
#
# Usage: .\stop.ps1             stop backend + frontend
#        .\stop.ps1 -StopDb     also stop the bundled PostgreSQL server
# ============================================================================
param([switch]$StopDb)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-PortProcess([int]$Port, [string]$Name) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        foreach ($c in $conn) {
            try {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped $Name (PID $($c.OwningProcess), port $Port)"
            } catch { }
        }
    } else {
        Write-Host "$Name not running on port $Port"
    }
}

Stop-PortProcess 8000 'Backend'
Stop-PortProcess 5173 'Frontend'

if ($StopDb) {
    $PgCtl = Join-Path $Root '.pginstall\pgsql\bin\pg_ctl.exe'
    $PgData = Join-Path $Root '.pginstall\pgdata'
    if (Test-Path $PgCtl) {
        Write-Host "Stopping PostgreSQL..."
        & $PgCtl -D $PgData stop -m fast | Out-Null
        Write-Host "PostgreSQL stopped"
    }
}

Write-Host "Done."
