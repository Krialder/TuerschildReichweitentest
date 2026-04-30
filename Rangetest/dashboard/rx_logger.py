"""RX-Logger als Hintergrund-Thread.

Liest seriell ``rx``-Events von einem Empfaenger-Port und schreibt sie
in die ``rx_event``-Tabelle. Mit Reconnect-Schleife fuer USB-Wackler im
Feldtest.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time

import serial

from .serial_io import now_iso


class ReceiverLogger(threading.Thread):
    """Loggt ``rx``-Events eines Empfaenger-Ports parallel zum Lauf."""

    def __init__(self, port: str, baud: int, conn: sqlite3.Connection,
                 run_id_ref: dict, stop_evt: threading.Event,
                 db_lock: threading.Lock) -> None:
        super().__init__(daemon=True, name=f"rx:{port}")
        self.port = port
        self.baud = baud
        self.conn = conn
        self.run_id_ref = run_id_ref
        self.stop_evt = stop_evt
        self.db_lock = db_lock

    def run(self) -> None:
        backoff = 1.0
        while not self.stop_evt.is_set():
            try:
                ser = serial.Serial(self.port, self.baud, timeout=0.1)
            except Exception as e:
                print(
                    f"[rx:{self.port}] Port-Fehler ({e}), retry in {backoff:.0f}s",
                    file=sys.stderr,
                )
                if self.stop_evt.wait(backoff):
                    return
                backoff = min(backoff * 2, 10.0)
                continue
            backoff = 1.0
            time.sleep(1.5)
            self._read_loop(ser)
            try:
                ser.close()
            except Exception:
                pass
            if not self.stop_evt.is_set():
                print(f"[rx:{self.port}] Verbindung verloren, reconnect...",
                      file=sys.stderr)

    def _read_loop(self, ser: serial.Serial) -> None:
        buf = b""
        while not self.stop_evt.is_set():
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except Exception:
                return
            if not chunk:
                time.sleep(0.01)
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "rx":
                    continue
                self._insert_rx(obj)

    def _insert_rx(self, obj: dict) -> None:
        rid = self.run_id_ref.get("id")
        if rid is None:
            return
        with self.db_lock:
            self.conn.execute(
                """INSERT INTO rx_event(run_id, port, src_id, seq, size,
                                        rssi, snr, is_dup, ts)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    rid, self.port,
                    int(obj.get("src", 0)),
                    int(obj.get("seq", 0)),
                    int(obj.get("size", 0)),
                    int(obj.get("rssi", 0)),
                    int(obj.get("snr", 0)),
                    1 if obj.get("dup") else 0,
                    now_iso(),
                ),
            )
            self.conn.commit()
