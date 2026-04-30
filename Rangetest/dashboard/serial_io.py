"""Wiederverwendbare Serial-Helpers.

Werden vom Long-Run-Engine und vom RX-Logger genutzt. Halten die Logik
zum zeilenweisen JSON-Lesen sowie zum Versenden von Kommandos zentral.
"""
from __future__ import annotations

import json
import time
from typing import Iterable, Optional

import serial


def now_iso() -> str:
    """ISO-8601 in UTC ohne Mikrosekunden."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def send_line(ser: serial.Serial, line: str) -> None:
    """Schickt eine Zeile (CR/LF wird ergaenzt) als ASCII."""
    if not line.endswith("\n"):
        line += "\n"
    ser.write(line.encode("ascii"))
    ser.flush()


def read_json_lines(ser: serial.Serial, timeout_s: float) -> Iterable[dict]:
    """Yieldet JSON-Objekte bis ``timeout_s`` ohne neue Daten.

    Der Timeout wird bei jedem empfangenen Chunk neu gestartet. So koennen
    laenger laufende Kommandos (Retry-Loops) ihre vollstaendige
    Ausgabe-Sequenz abliefern, solange der Sender weiterhin sendet.
    """
    deadline = time.time() + timeout_s
    buf = b""
    while time.time() < deadline:
        to_read = ser.in_waiting or 1
        chunk = ser.read(to_read)
        if not chunk:
            time.sleep(0.005)
            continue
        deadline = time.time() + timeout_s
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                # Defekte Zeile (CR-Bruch, Boot-Spam) ignorieren.
                continue


def wait_for_ready(ser: serial.Serial, wait_s: float = 5.0) -> Optional[dict]:
    """Liest JSON-Zeilen bis ein ``ready``-Banner mit Rolle ``sender`` kommt."""
    for obj in read_json_lines(ser, wait_s):
        if obj.get("type") == "ready" and obj.get("role") == "sender":
            return obj
    return None


def query_version(ser: serial.Serial, timeout_s: float = 2.0) -> dict:
    """Sendet ``cmd=version`` und gibt die Antwort zurueck (oder leer)."""
    send_line(ser, "cmd=version")
    for obj in read_json_lines(ser, timeout_s):
        if obj.get("type") == "version":
            return obj
    return {}
