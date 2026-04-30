# Rangetest Complete

ESP-NOW Reichweiten-Testumgebung für ESP32. Bestehend aus drei Teilen:

1. **Firmware** (`firmware/`): Sender, Relays und Empfänger. Verwendet ESP-NOW
   im 2.4-GHz-Band mit optionalem Long-Range-Modus, Store-and-Forward Relaying
   mit TTL-Flooding plus Dedup, sowie PROBE-Beacons zur Hop-Progress-Erfassung.
2. **Dashboard** (`dashboard/`): FastAPI-Webserver mit
   - Topologie-Wizard (generiert PlatformIO-Envs für 1 Sender + N Ketten)
   - Geführter Flash-Workflow pro Knoten
   - Run-Setup, Live-Charts, Run-History
   - Analyse-Tab: PDR mit Wilson-CI, Verteilungen, Heatmaps, Timelines
3. **Tests** (`tests/`): Pytest-Smoke-Tests für Topologie-Berechnung und
   Aggregations-Statistik.

Der Workspace-Aufbau und welche Datei was tut steht in [`WORKSPACE.md`](WORKSPACE.md).

---

## Schnellstart

### 1. Setup ausführen

**Windows (PowerShell):**

```powershell
.\setup.ps1
```

**Linux / macOS:**

```bash
bash setup.sh
```

Das Skript prüft Python (>= 3.10), legt ein virtuelles Environment unter
`.venv/` an, installiert die Python-Abhängigkeiten und prüft, ob PlatformIO
installiert ist. Falls Python oder PlatformIO fehlen, gibt das Skript Links zur
manuellen Installation aus.

### 2. Dashboard starten

**Windows:** Doppelklick auf `start.cmd` im Projektordner.

Das Skript aktiviert die venv, startet das Dashboard und oeffnet den
Browser. Wenn keine venv vorhanden ist, wird vorher automatisch
`setup.ps1` ausgefuehrt. Alternativ via PowerShell:

```powershell
.\start.ps1
```

**Linux / macOS:**

```bash
./start.sh
```

Manueller Start (nach `setup.ps1` / `setup.sh`):

```powershell
.\.venv\Scripts\python.exe -m dashboard
```

Dashboard laeuft anschliessend auf <http://127.0.0.1:8000>.

### 3. ESP32 flashen

1. Ein ESP32-Board per USB anstecken.
2. Browser auf <http://127.0.0.1:8000> öffnen, Tab **Flash** wählen.
3. Topologie-Wizard ausfüllen (z.B. `X=2` Relays, `Y=1` Kette).
4. **Plan generieren** klicken. Es erscheint eine Tabelle mit allen Knoten in
   Flash-Reihenfolge.
5. Pro Zeile den COM-Port wählen und **Flash** klicken. Boards beschriften.
6. Wenn alle Knoten geflasht sind: Tab **Run-Setup**, Empfänger-IDs eintragen
   und Run starten.

### 4. Daten auswerten

Tab **Analyse** liefert PDR (Packet-Delivery-Ratio) mit Konfidenzintervall,
Latenz-/RSSI-/SNR-Verteilungen, Heatmaps, Zeitverläufe und Distanz-vs-RSSI
Scatter-Plots über beliebige Run-Auswahlen.

### Run-Setup im Detail

Der Tab **Run-Setup** ist als 3-Schritte-Wizard aufgebaut:

1. **Targets** — Empfaenger-NODE_IDs. Drei Wege, sie zu setzen:
   - **Aus Flash uebernehmen**: liest die `auto_*recv*`-Envs aus
     `firmware/platformio.ini` (also genau die, die der Topologie-Wizard
     im Flash-Tab erzeugt hat).
   - **Letzten Run klonen**: kopiert Targets und Notiz aus dem juengsten
     Run in der DB.
   - **Manuell hinzufuegen**: Zeile fuer Zeile.
   `distance_m` und `walls` sind optional.
2. **Profil** — Paketgroessen-Modus (Mix / eigene Liste / Bereich /
   zufaellig pro Paket) und Dauer. Erweiterte Parameter (retry, timeout,
   jitter, ...) sind in einer Klapp-Sektion versteckt.
3. **Start** — Sender-Port, RX-Logger-Ports, Notizen, Start-Button.

Der Inhalt der Pakete ist immer zufaellig (Firmware fuellt den Payload
mit `esp_random`), unabhaengig vom gewaehlten Groessen-Modus.

Profile koennen oben am Run-Setup-Tab unter beliebigem Namen im
Browser-`localStorage` gespeichert und wieder geladen werden (ohne
Hardware-Felder, damit sie auch auf einem anderen Rechner funktionieren).

---

## Konfiguration

Die Datei [`config.toml`](config.toml) im Projekt-Root steuert die
Standard-Pfade des Dashboards. Wichtigster Eintrag:

```toml
[storage]
# Pfad zur SQLite-DB. Standard: leere DB unter data/espnow.sqlite.
# Alternativ: auf eine bestehende DB zeigen, z.B. die alte
# rangetest_espnow/espnow.sqlite, um die Historie weiterhin auszuwerten.
db_path = "data/espnow.sqlite"
```

Wer alte Runs aus dem vorherigen `rangetest_espnow`-Ordner mitnehmen will,
kann den Pfad einfach umbiegen oder die Datei nach `data/` kopieren.

---

## Anforderungen

- **Python** 3.10 oder neuer
- **PlatformIO** (für Firmware-Builds; <https://platformio.org/install>)
- **ESP32 Board** mit USB-UART-Bridge (CP2102 oder CH340 typisch)

Auf Windows klappt PlatformIO über UNC-Pfade nicht direkt; das Dashboard
umgeht das automatisch über `cmd /c pushd ... && pio ... && popd`.

---

## Lizenz / Hinweise

Dieses Projekt ist für interne Tests entstanden. Für Hinweise zu
Architektur, Wire-Protokoll und Datenbank-Schema siehe
[`WORKSPACE.md`](WORKSPACE.md).
