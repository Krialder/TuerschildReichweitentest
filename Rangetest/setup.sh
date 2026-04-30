#!/usr/bin/env bash
# Setup-Skript fuer Linux / macOS.
# Prueft Python, legt ein virtuelles Environment an, installiert die
# Python-Abhaengigkeiten und prueft, ob PlatformIO installiert ist.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 0) Doppelstart-Schutz via Lock-Datei -------------------------------
LOCK_FILE="$SCRIPT_DIR/.setup.lock"
if [ -f "$LOCK_FILE" ]; then
    OLD_PID="$(cat "$LOCK_FILE" 2>/dev/null || echo "")"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[setup] Setup laeuft bereits (PID $OLD_PID). Abbruch."
        exit 0
    fi
    echo "[setup] Veralteten Lock entfernt."
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT INT TERM

section() { echo; echo "=== $1 ==="; }
green()   { printf "\033[32m%s\033[0m\n" "$1"; }
red()     { printf "\033[31m%s\033[0m\n" "$1"; }
yellow()  { printf "\033[33m%s\033[0m\n" "$1"; }

# --- 1) Python finden ----------------------------------------------------
section "Python pruefen"
PYTHON_CMD=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PYTHON_CMD="$cand"
            break
        fi
    fi
done
if [ -z "$PYTHON_CMD" ]; then
    red "Python 3.10+ nicht gefunden."
    echo "Installation:"
    yellow "  Linux : sudo apt install python3 python3-venv  (oder Distribution-aequivalent)"
    yellow "  macOS : brew install python  (https://brew.sh)"
    yellow "  Generisch: https://www.python.org/downloads/"
    exit 1
fi
green "Python ok: $($PYTHON_CMD --version)"

# --- 2) venv anlegen oder wiederverwenden -------------------------------
section "Virtuelles Environment"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo "Lege .venv an..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
else
    echo "venv vorhanden, wiederverwenden."
fi

# --- 3) Pip-Abhaengigkeiten installieren --------------------------------
section "Python-Abhaengigkeiten installieren"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r "$SCRIPT_DIR/requirements.txt"
green "Pakete installiert."

# --- 4) PlatformIO pruefen (nicht erzwungen) ----------------------------
section "PlatformIO pruefen"
PIO_CMD=""
for cand in "$HOME/.platformio/penv/bin/pio" pio; do
    if [ -x "$cand" ] || command -v "$cand" >/dev/null 2>&1; then
        PIO_CMD="$cand"
        break
    fi
done
if [ -n "$PIO_CMD" ]; then
    green "PlatformIO ok: $($PIO_CMD --version)"
else
    yellow "PlatformIO nicht gefunden (optional, aber zum Flashen noetig)."
    echo "Installation:"
    yellow "  1) VS Code Extension 'PlatformIO IDE' (empfohlen):"
    yellow "     https://platformio.org/install/ide?install=vscode"
    yellow "  2) Oder als CLI per pipx:"
    yellow "     pipx install platformio"
    echo "Anschliessend Setup erneut starten oder pio-Pfad in config.toml eintragen."
fi

# --- 5) data/-Ordner anlegen --------------------------------------------
section "Daten-Verzeichnis"
DATA_DIR="$SCRIPT_DIR/data"
if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR"
    green "data/ angelegt."
else
    echo "data/ existiert bereits."
fi

# --- 6) Smoke-Test der Installation -------------------------------------
section "Smoke-Test"
"$VENV_PY" -c "import fastapi, uvicorn, pydantic, serial, yaml; print('imports ok')"

echo
green "Setup fertig."
echo
echo "Naechste Schritte:"
echo "  1) .venv aktivieren:"
echo "     source .venv/bin/activate"
echo "  2) Dashboard starten:"
echo "     python -m dashboard"
echo "  3) Browser oeffnen: http://127.0.0.1:8000"
