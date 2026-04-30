@echo off
REM Doppelklick-Starter fuer Windows.
REM Aktiviert die venv und startet das Dashboard auf http://127.0.0.1:8000.
REM Wenn das Dashboard schon laeuft, wird nur der Browser geoeffnet.
REM Wenn die venv fehlt, wird einmalig setup.ps1 aufgerufen.

setlocal
cd /d "%~dp0"

REM --- Pruefen, ob das Dashboard bereits auf Port 8000 laeuft ----------
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient;try{$c.Connect('127.0.0.1',8000);exit 0}catch{exit 1}finally{$c.Close()}" >nul 2>&1
if %errorlevel% equ 0 (
    echo [start] Dashboard laeuft bereits. Oeffne nur den Browser.
    start "" http://127.0.0.1:8000
    timeout /t 2 /nobreak >nul
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo [start] Keine .venv gefunden, fuehre Setup aus...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 (
        echo.
        echo [start] Setup fehlgeschlagen. Bitte Meldungen oben pruefen.
        pause
        exit /b 1
    )
)

echo [start] Dashboard wird gestartet auf http://127.0.0.1:8000
echo [start] Zum Beenden dieses Fenster schliessen oder Strg+C druecken.
echo.

REM Browser nach 2 Sekunden oeffnen, im Hintergrund
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"

".venv\Scripts\python.exe" -m dashboard

if errorlevel 1 (
    echo.
    echo [start] Dashboard wurde mit Fehler beendet.
    pause
)

endlocal
