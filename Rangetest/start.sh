#!/usr/bin/env bash
# Start-Skript fuer Linux/macOS.
# Aktiviert die venv und startet das Dashboard.
# Wenn das Dashboard schon laeuft, wird nur der Browser geoeffnet.
# Wenn die venv fehlt, wird automatisch setup.sh ausgefuehrt.

set -e
cd "$(dirname "$0")"

open_browser() {
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open http://127.0.0.1:8000 >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open http://127.0.0.1:8000 >/dev/null 2>&1 || true
    fi
}

# Pruefen ob Port 8000 bereits belegt ist (bash /dev/tcp).
if (echo > /dev/tcp/127.0.0.1/8000) >/dev/null 2>&1; then
    echo "[start] Dashboard laeuft bereits. Oeffne nur den Browser."
    open_browser
    exit 0
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "[start] Keine .venv gefunden, fuehre Setup aus..."
    bash ./setup.sh
fi

echo "[start] Dashboard wird gestartet auf http://127.0.0.1:8000"
echo "[start] Zum Beenden Strg+C druecken."
echo

# Browser nach kurzer Verzoegerung oeffnen (best effort)
( sleep 2; open_browser ) &

exec ./.venv/bin/python -m dashboard
