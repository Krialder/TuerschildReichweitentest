# Start-Skript fuer Windows (PowerShell-Variante).
# Aktiviert die venv und startet das Dashboard.
# Wenn die venv fehlt, wird automatisch setup.ps1 ausgefuehrt.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Test-PortInUse([int]$port) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $c.Connect('127.0.0.1', $port)
        return $true
    } catch {
        return $false
    } finally {
        $c.Close()
    }
}

if (Test-PortInUse 8000) {
    Write-Host "[start] Dashboard laeuft bereits. Oeffne nur den Browser." -ForegroundColor Yellow
    Start-Process "http://127.0.0.1:8000"
    exit 0
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "[start] Keine .venv gefunden, fuehre Setup aus..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[start] Setup fehlgeschlagen." -ForegroundColor Red
        exit 1
    }
}

Write-Host "[start] Dashboard wird gestartet auf http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "[start] Zum Beenden Strg+C druecken." -ForegroundColor Cyan
Write-Host ""

# Browser nach kurzer Verzoegerung oeffnen
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8000"
} | Out-Null

& $python -m dashboard
