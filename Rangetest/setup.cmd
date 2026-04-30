@echo off
REM Doppelklick-Setup fuer Windows.
REM Ruft setup.ps1 auf, das die venv anlegt, Pakete installiert und PIO prueft.

setlocal
cd /d "%~dp0"

echo [setup] Starte Setup ueber PowerShell...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

if errorlevel 1 (
    echo.
    echo [setup] Setup fehlgeschlagen. Bitte Meldungen oben pruefen.
    pause
    exit /b 1
)

echo.
echo [setup] Fertig. Du kannst jetzt start.cmd doppelklicken.
pause
endlocal
