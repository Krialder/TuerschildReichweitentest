# Workspace-Map: RangetestComplete

Beschreibung jeder Datei und ihres Zwecks. Bei Verschiebungen oder neuen
Dateien bitte hier mitpflegen.

## Wurzel

| Datei | Zweck |
|-------|-------|
| `README.md` | Quick Start und Bedienung |
| `WORKSPACE.md` | Diese Datei |
| `pyproject.toml` | Python-Paket-Metadaten und Pytest-Config |
| `requirements.txt` | Pip-Abhaengigkeiten (Spiegel zu pyproject) |
| `config.toml` | Laufzeit-Einstellungen (DB-Pfad, Host/Port, pio-Pfad) |
| `setup.ps1` | Windows-Setup (venv, pip install, PlatformIO-Check) |
| `setup.cmd` | Doppelklick-Wrapper, der `setup.ps1` ohne PowerShell-Hindernisse startet |
| `setup.sh` | Bash-Variante des Setups (Linux/macOS) |
| `start.cmd` | Doppelklick-Starter (Windows). Ruft bei Bedarf das Setup auf, startet danach das Dashboard und oeffnet den Browser. |
| `start.ps1` | PowerShell-Variante des Starters |
| `start.sh` | Linux/macOS-Variante des Starters |
| `.gitignore` | Standard-Excludes (venv, build-Artefakte, DB-Dateien) |

## firmware/

ESP32-Firmware fuer PlatformIO. Eine `main.cpp` mit drei Rollen, die per
Build-Flag `ROLE_SENDER` / `ROLE_RECEIVER` / `ROLE_RELAY` aktiviert werden.

| Datei | Zweck |
|-------|-------|
| `platformio.ini` | Manuelle Build-Envs `sender`, `receiver`, `relay5`, `relay6`. AUTO-Block wird vom Wizard zwischen `; AUTO_TOPOLOGY_BEGIN/END` ergaenzt. |
| `src/protocol.h` | Wire-Protokoll: Magics, Offsets, CRC16, Pfad-Layout |
| `src/main.cpp` | Komplette Firmware in einer Translation-Unit. Per `#if ROLE_*` werden die rollenspezifischen Pfade aktiviert. |

## dashboard/

FastAPI-Backend und Frontend. Start ueber `python -m dashboard`.

### Python-Paket

| Datei | Zweck |
|-------|-------|
| `__init__.py` | Exportiert nur `create_app` |
| `__main__.py` | Erlaubt `python -m dashboard` (uvicorn-Bootstrap) |
| `app.py` | FastAPI-Factory: Statics, Health, Router-Mounts |
| `config.py` | `Settings`, `load_settings`, Projekt-Pfade, pio-Aufloesung |
| `pio_helpers.py` | `parse_pio_envs`, Build-Flag-Parser |
| `serial_io.py` | UART-Helpers: `send_line`, `read_json_lines`, `wait_for_ready`, `query_version` |
| `db.py` | SQLite-Schema, Migrationen, `open_db` und `open_db_ro` |
| `topology.py` | Wizard-Logik: ID-Plan, prev_mask, ini-Block schreiben/loeschen |
| `topology_api.py` | Duenner Router fuer `/api/topology/plan` |
| `flash.py` | Router fuer `/api/ports`, `/api/envs`, WebSocket `/ws/flash` (UNC-sicheres pushd-Wrapper) |
| `rx_logger.py` | Thread, der RX-Ports liest und in DB schreibt |
| `buckets.py` | 60-Sekunden-Aggregat-Helper fuer den Live-Run |
| `engine.py` | `LongRunEngine` und Singleton `ENGINE`: Run-Lifecycle, Worker-Loop, Pub/Sub |
| `runs_api.py` | Router fuer Run-Liste, Detail, Start/Stop, WebSocket `/ws/live` |
| `stats.py` | Perzentile, Wilson-CI, Fail-Streak, `iso_duration_s` |
| `analysis.py` | Router fuer alle `/api/analysis/*`-Endpoints (Aggregat, Heatmap, Timeline, Distribution, Scatter) |

### Frontend (`dashboard/static/`)

| Datei | Zweck |
|-------|-------|
| `index.html` | Skelett mit den fuenf Tabs |
| `css/main.css` | Komplettes Styling |
| `js/app.js` | Definiert globalen `$()`-Helper, Tabs, DOMContentLoaded-Init |
| `js/flash.js` | Manueller Flash plus Topologie-Wizard inkl. Live-Log-WebSocket |
| `js/runsetup.js` | 3-Schritte-Wizard fuer das Run-Setup (Targets aus Flash/letztem Run uebernehmen, Paketgroessen-Modi, Profil-Speicherung im localStorage) |
| `js/live.js` | Live-Charts und Start/Stop-Buttons, WebSocket `/ws/live` |
| `js/history.js` | History-Tab: Run-Liste und Run-Detail |
| `js/analyse.js` | Analyse-Tab: Aggregat, Heatmap, Timeline, Boxplot, CDF, Distanz-Scatter |

## tests/

Pytest-Suite (12 Tests, gruen). Aufruf: `.\.venv\Scripts\python -m pytest tests/`.

| Datei | Zweck |
|-------|-------|
| `__init__.py` | Paket-Marker |
| `test_topology.py` | Wizard-Logik: ID-Schema, prev_mask, ini-Roundtrip, Begrenzungs-Constraint |
| `test_stats.py` | Perzentil, Wilson, Fail-Streak, `iso_duration_s` |

## data/

Wird beim ersten Run automatisch angelegt. Speicherort der SQLite-Datei
`espnow.sqlite` (in `config.toml` umbiegbar).

## Konventionen

### NODE_ID-Schema (Topologie-Wizard)

- IDs fortlaufend: `1` = Sender, dann pro Kette `c` ein Block aus `X` Relays plus `1` Empfaenger.
- Beispiel `X=2, Y=2`: `1 (sender) | 2,3 (chain1) | 4 (recv1) | 5,6 (chain2) | 7 (recv2)`.
- `prev_mask` ist 32 Bit, daher gilt `1 + Y*(X+1) <= 31`.

### Wire-Protokoll-Magics

| Magic | Wert | Zweck |
|-------|------|-------|
| `MAGIC_DATA` | `0xAA` | Datenpaket vom Sender Richtung Empfaenger |
| `MAGIC_ACK` | `0x55` | Bestaetigung vom Empfaenger zurueck |
| `MAGIC_PROBE` | `0xCC` | Hop-Progress-Beacon vom Relay |

Der Payload-Bereich eines DATA-Pakets wird beim Bau im Sender mit
`esp_fill_random` befuellt. Damit ist jeder Sendeinhalt einzigartig
und deckt potentielle Bias-Effekte (z. B. konstante Bitmuster) ab.
Frame-Limits: `15..250` Byte (Header 13 + 2 Byte CRC + Payload).

### Run-Setup: Paketgroessen-Modi

Die Engine unterstuetzt drei Auswahl-Modi (`size_mode` im Request-Body):

- `list` (Default): rotiert ueber `packet_sizes`. Standard-Mix `32, 64,
  128, 192, 240` ist als UI-Preset "Standard-Mix" verfuegbar.
- `range`: rotiert ueber `range(size_min, size_max+1, size_step)`.
- `random`: zieht jede Groesse zufaellig aus `[size_min..size_max]`.

### DB-Schema (Auszug)

- `run` - Metadaten pro Lauf
- `target_meta` - Distanz, Waende, Notizen pro Empfaenger und Lauf
- `result` - ein Eintrag pro gesendetem Paket
- `rx_event` - parallel vom Empfaenger geloggte Events
- `noise_sample` - optionale Noise-Floor-Messungen
- `bucket_stats` - 60-Sekunden-Aggregate vom Long-Run-Engine

### UNC-/Netzlaufwerk-Hinweis

Fuer den PlatformIO-Aufruf wird der Aufruf in
`cmd /c pushd "<firmware>" && "<pio>" run ... && popd` gewickelt, damit
`cmd.exe` auf Windows-Netzlaufwerken (z. B. via `subst` oder gemapptes
`I:`) nicht auf `C:\Windows` zurueckfaellt.
