# Setup-Skript fuer Windows (PowerShell).
# Prueft Python, legt ein virtuelles Environment an, installiert die
# Python-Abhaengigkeiten und prueft, ob PlatformIO installiert ist.
# Wenn etwas fehlt und nicht automatisch installiert werden kann, gibt
# das Skript einen Link zur manuellen Installation aus.

$ErrorActionPreference = "Stop"

# --- 0) Doppelstart-Schutz via Lock-Datei -------------------------------
$lockFile = Join-Path $PSScriptRoot ".setup.lock"
if (Test-Path $lockFile) {
    try {
        $existingPid = [int]((Get-Content $lockFile -Raw -ErrorAction Stop).Trim())
        if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
            Write-Host "[setup] Setup laeuft bereits (PID $existingPid). Abbruch." -ForegroundColor Yellow
            exit 0
        }
    } catch { }
    Write-Host "[setup] Veralteten Lock entfernt." -ForegroundColor DarkYellow
    Remove-Item $lockFile -ErrorAction SilentlyContinue
}
$PID | Out-File -FilePath $lockFile -Encoding ascii -Force
Register-EngineEvent PowerShell.Exiting -Action {
    Remove-Item $lockFile -ErrorAction SilentlyContinue
} | Out-Null

function Write-Section($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Test-PythonOk($cmd) {
    try {
        $v = & $cmd --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            return ($major -gt 3) -or ($major -eq 3 -and $minor -ge 10)
        }
    } catch { }
    return $false
}

# --- 1) Python finden ----------------------------------------------------
Write-Section "Python pruefen"
$pythonCmd = $null
foreach ($cand in @("python", "python3", "py")) {
    if (Test-PythonOk $cand) { $pythonCmd = $cand; break }
}
if (-not $pythonCmd) {
    Write-Host "Python 3.10+ nicht gefunden." -ForegroundColor Red
    Write-Host "Bitte manuell installieren von:"
    Write-Host "  https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Beim Installer 'Add Python to PATH' aktivieren."
    exit 1
}
Write-Host "Python ok: $(& $pythonCmd --version)" -ForegroundColor Green

# --- 2) venv anlegen oder wiederverwenden -------------------------------
Write-Section "Virtuelles Environment"
$venvDir = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Lege .venv an..."
    & $pythonCmd -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "venv konnte nicht erstellt werden." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "venv vorhanden, wiederverwenden."
}

# --- 3) Pip-Abhaengigkeiten installieren --------------------------------
Write-Section "Python-Abhaengigkeiten installieren"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Pip-Install fehlgeschlagen." -ForegroundColor Red
    exit 1
}
Write-Host "Pakete installiert." -ForegroundColor Green

# --- 4) PlatformIO pruefen (nicht erzwungen) ----------------------------
Write-Section "PlatformIO pruefen"
$pioCandidates = @(
    (Join-Path $env:USERPROFILE ".platformio\penv\Scripts\pio.exe"),
    "pio.exe",
    "pio"
)
$pioFound = $null
foreach ($p in $pioCandidates) {
    try {
        $v = & $p --version 2>&1
        if ($LASTEXITCODE -eq 0) { $pioFound = $p; break }
    } catch { }
}
if ($pioFound) {
    Write-Host "PlatformIO ok: $(& $pioFound --version)" -ForegroundColor Green
} else {
    Write-Host "PlatformIO nicht gefunden (optional, aber zum Flashen noetig)." -ForegroundColor Yellow
    Write-Host "Installation:"
    Write-Host "  1) VS Code Extension 'PlatformIO IDE' (empfohlen):"
    Write-Host "     https://platformio.org/install/ide?install=vscode" -ForegroundColor Yellow
    Write-Host "  2) Oder als CLI per pipx:"
    Write-Host "     pipx install platformio" -ForegroundColor Yellow
    Write-Host "Nach der Installation diesen Setup-Lauf erneut starten oder"
    Write-Host "den pio-Pfad in config.toml [platformio] pio_exe eintragen."
}

# --- 5) data/-Ordner anlegen --------------------------------------------
Write-Section "Daten-Verzeichnis"
$dataDir = Join-Path $PSScriptRoot "data"
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    Write-Host "data/ angelegt." -ForegroundColor Green
} else {
    Write-Host "data/ existiert bereits."
}

# --- 6) Smoke-Test der Installation -------------------------------------
Write-Section "Smoke-Test"
$smoke = & $venvPython -c "import fastapi, uvicorn, pydantic, serial, yaml; print('imports ok')" 2>&1
Write-Host $smoke

Write-Host ""
Write-Host "Setup fertig." -ForegroundColor Green
Write-Host ""
Write-Host "Naechste Schritte:" -ForegroundColor Cyan
Write-Host "  1) .venv aktivieren:"
Write-Host "     .\.venv\Scripts\Activate.ps1"
Write-Host "  2) Dashboard starten:"
Write-Host "     python -m dashboard"
Write-Host "  3) Browser oeffnen: http://127.0.0.1:8000"

# Lock-Datei freigeben (Engine-Exit-Event greift zusaetzlich)
Remove-Item $lockFile -ErrorAction SilentlyContinue
