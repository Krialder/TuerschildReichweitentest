"""Long-Run-Engine.

Orchestriert einen langlaufenden Reichweitentest in einem Hintergrund-Thread:
oeffnet den seriellen Port zum Sender, registriert Empfaenger-Logger fuer
RX-Events, schreibt jedes ``result`` in die DB, aggregiert 60-s-Buckets
und streamt Status/Events ueber asyncio-Queues an angeschlossene
WebSocket-Clients.

Es kann immer nur ein Run gleichzeitig laufen (Singleton ``ENGINE``).
"""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

import serial

from .buckets import BucketAggregator
from .db import open_db
from .rx_logger import ReceiverLogger
from .serial_io import (
    now_iso,
    query_version,
    read_json_lines,
    send_line,
    wait_for_ready,
)

# Felder, die wir aus dem ``result``-JSON in Live-Events durchreichen.
_RESULT_FIELDS = (
    "seq", "dst", "size", "attempt", "success", "code",
    "rssi_remote", "snr_remote", "rssi_local", "snr_local",
    "latency_ms", "airtime_ms", "via_relay", "path", "hops",
    "reached_path", "reached_hops", "last_hop_ok", "fail_reason",
)


class LongRunEngine:
    """Singleton-Engine fuer einen aktiven Langzeit-Run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._db_lock = threading.Lock()
        self._cfg: Optional[dict] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._run_id: Optional[int] = None
        self._status: str = "idle"
        self._counters: dict[str, Any] = {
            "sent": 0, "ok": 0, "fail": 0, "started_at": None,
        }
        self._bucket: Optional[BucketAggregator] = None
        self._listeners: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ----- Lifecycle -----------------------------------------------
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, cfg: dict, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            if self.is_running():
                raise RuntimeError("Es laeuft bereits ein Run.")
            self._cfg = cfg
            self._loop = loop
            self._stop.clear()
            self._counters = {
                "sent": 0, "ok": 0, "fail": 0,
                "started_at": time.time(), "ended_at": None,
            }
            self._thread = threading.Thread(
                target=self._run, name="LongRunEngine", daemon=True,
            )
            self._thread.start()

    def stop(self, wait_s: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=wait_s)

    def status(self) -> dict:
        cfg = self._cfg or {}
        started = self._counters.get("started_at")
        ended   = self._counters.get("ended_at")
        if started:
            # Nach Run-Ende einfrieren, sonst tickt die Stoppuhr im Idle weiter.
            end_ts = ended if ended else time.time()
            elapsed = max(0, end_ts - started)
        else:
            elapsed = 0
        return {
            "running": self.is_running(),
            "status": self._status,
            "run_id": self._run_id,
            "counters": dict(self._counters),
            "elapsed_s": int(elapsed),
            "cfg_summary": {
                "port": cfg.get("port"),
                "rx_ports": cfg.get("rx_ports", []),
                "duration_s": cfg.get("duration_s"),
                "targets": cfg.get("targets", []),
                "packet_sizes": cfg.get("packet_sizes", []),
                "size_mode": cfg.get("size_mode", "list"),
                "size_min": cfg.get("size_min"),
                "size_max": cfg.get("size_max"),
                "size_step": cfg.get("size_step"),
                "db": cfg.get("db"),
            },
        }

    # ----- Pub/Sub -------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _broadcast(self, evt: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        dead = []
        for q in list(self._listeners):
            try:
                loop.call_soon_threadsafe(q.put_nowait, evt)
            except (asyncio.QueueFull, Exception):
                dead.append(q)
        for q in dead:
            self._listeners.discard(q)

    def _set_status(self, s: str) -> None:
        self._status = s
        self._broadcast({"type": "status", "status": s})

    # ----- Worker --------------------------------------------------
    def _run(self) -> None:
        cfg = self._cfg or {}
        rx_threads: list[ReceiverLogger] = []
        try:
            self._set_status("connecting")
            db_path = Path(cfg.get("db") or "data/espnow.sqlite")
            conn = open_db(db_path)
            self._conn = conn

            run_id_ref: dict = {"id": None}
            for p in cfg.get("rx_ports", []) or []:
                rx = ReceiverLogger(
                    p, int(cfg.get("baud", 115200)), conn,
                    run_id_ref, self._stop, self._db_lock,
                )
                rx_threads.append(rx)
                rx.start()

            with serial.Serial(cfg["port"], int(cfg.get("baud", 115200)),
                               timeout=0.05) as ser:
                time.sleep(1.5)
                ready = wait_for_ready(ser, 5.0)
                fw = (ready or {}).get("fw", "unknown")
                ver = query_version(ser, 2.0) or {}
                if ver:
                    fw = ver.get("fw", fw)
                run_id = self._create_run(conn, cfg, fw, ver)
                self._run_id = run_id
                run_id_ref["id"] = run_id
                self._bucket = BucketAggregator(
                    conn, self._db_lock, run_id, self._broadcast,
                )
                self._broadcast({
                    "type": "run_started", "run_id": run_id, "fw": fw,
                    "started_at": now_iso(),
                    "cfg_summary": self.status()["cfg_summary"],
                })
                send_line(ser, f"cmd=setrun run_id={run_id & 0xFFFF}")
                # Drain ack einmal kurz (nicht blockierend lange).
                for _ in read_json_lines(ser, 1.0):
                    break
                self._set_status("running")
                self._loop_send(ser, run_id, cfg)

                with self._db_lock:
                    conn.execute("UPDATE run SET ended_at=? WHERE id=?",
                                 (now_iso(), run_id))
                    conn.commit()
                if self._bucket:
                    self._bucket.flush()
                self._counters["ended_at"] = time.time()
                self._broadcast({
                    "type": "run_ended", "run_id": run_id,
                    "ended_at": now_iso(),
                    "counters": dict(self._counters),
                })
        except Exception as e:           # pragma: no cover - Hardware-Pfad
            self._broadcast({"type": "error", "msg": f"{type(e).__name__}: {e}"})
        finally:
            self._stop.set()
            if self._counters.get("ended_at") is None and self._counters.get("started_at"):
                self._counters["ended_at"] = time.time()
            for t in rx_threads:
                try:
                    t.join(timeout=2.0)
                except Exception:
                    pass
            self._set_status("idle")
            self._thread = None

    # ----- DB-Helpers ----------------------------------------------
    def _create_run(self, conn: sqlite3.Connection, cfg: dict,
                    fw: str, ver: dict) -> int:
        with self._db_lock:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO run(started_at, relay_id, notes, environment,
                                   firmware_version, lora_config)
                   VALUES (?,?,?,?,?,?)""",
                (
                    now_iso(),
                    int(cfg.get("relay_id", 1)),
                    str(cfg.get("run_notes", "")),
                    json.dumps(cfg.get("environment", {}) or {}, ensure_ascii=False),
                    fw,
                    json.dumps(ver or {}, ensure_ascii=False),
                ),
            )
            run_id = int(cur.lastrowid)
            for t in cfg.get("targets", []) or []:
                cur.execute(
                    """INSERT OR REPLACE INTO target_meta(
                           run_id, dst_id, label, distance_m, walls, notes)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        run_id,
                        int(t["id"]),
                        str(t.get("label", "")),
                        t.get("distance_m"),
                        t.get("walls"),
                        str(t.get("notes", "")),
                    ),
                )
            conn.commit()
        return run_id

    # ----- Send-Schleife -------------------------------------------
    def _loop_send(self, ser: serial.Serial, run_id: int, cfg: dict) -> None:
        targets = cfg.get("targets") or []
        if not targets:
            return
        size_picker = _make_size_picker(cfg)
        retry = int(cfg.get("retry_limit", 1))
        timeout_ms = int(cfg.get("timeout_ms", 200))
        inter_ms = int(cfg.get("inter_send_ms", 50))
        jitter = max(0, min(90, int(cfg.get("jitter_pct", 20)))) / 100.0
        duration_s = cfg.get("duration_s")
        try:
            duration_s = float(duration_s) if duration_s is not None else None
        except (TypeError, ValueError):
            duration_s = None
        t_start = time.time()
        while not self._stop.is_set():
            if duration_s is not None and (time.time() - t_start) >= duration_s:
                return
            for t in targets:
                if self._stop.is_set():
                    return
                if duration_s is not None and (time.time() - t_start) >= duration_s:
                    return
                dst = int(t["id"])
                size = size_picker()
                self._do_send_one(ser, run_id, cfg, dst, size, retry, timeout_ms)
                if self._bucket:
                    self._bucket.maybe_flush()
                self._pacing_sleep(inter_ms, jitter)

    def _pacing_sleep(self, inter_ms: int, jitter: float) -> None:
        base = inter_ms / 1000.0
        if jitter > 0:
            base *= random.uniform(1.0 - jitter, 1.0 + jitter)
        end = time.time() + base
        while not self._stop.is_set():
            remaining = end - time.time()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def _do_send_one(self, ser: serial.Serial, run_id: int, cfg: dict,
                     dst: int, size: int, retry: int, timeout_ms: int) -> None:
        send_line(ser, (
            f"cmd=send dst={dst} size={size} "
            f"retry={retry} timeout_ms={timeout_ms}"
        ))
        budget = (retry * timeout_ms / 1000.0) + 2.0
        for obj in read_json_lines(ser, budget):
            t = obj.get("type")
            if t == "result":
                self._handle_result(obj, run_id, cfg, dst, size)
            elif t == "done":
                break
        with self._db_lock:
            if self._conn:
                self._conn.commit()

    def _handle_result(self, obj: dict, run_id: int, cfg: dict,
                       dst: int, size: int) -> None:
        path_list, hops_val = _coerce_path(obj.get("path"), obj.get("hops"))
        reached_list, reached_hops = _coerce_path(
            obj.get("reached_path"), obj.get("reached_hops"))
        last_hop_ok = obj.get("last_hop_ok")
        if last_hop_ok is None:
            last_hop_ok = reached_list[-1] if reached_list else 0
        success = bool(obj.get("success"))
        row = (
            run_id,
            int(obj.get("dst", dst)),
            int(obj.get("via_relay", cfg.get("relay_id", 1))),
            int(obj.get("attempt", 0)),
            int(obj.get("size", size)),
            1 if success else 0,
            str(obj.get("code", "")),
            int(obj.get("rssi_remote", 0)),
            int(obj.get("snr_remote", 0)),
            int(obj.get("rssi_local", 0)),
            int(obj.get("snr_local", 0)),
            int(obj.get("latency_ms", 0)),
            int(obj.get("airtime_ms", 0)),
            int(obj.get("seq", 0)),
            json.dumps(path_list),
            int(hops_val),
            json.dumps(reached_list),
            int(reached_hops),
            int(last_hop_ok),
            str(obj.get("fail_reason", "")),
            now_iso(),
        )
        with self._db_lock:
            assert self._conn is not None
            self._conn.execute(
                """INSERT INTO result(run_id, dst_id, via_relay_id, attempt_no,
                                      packet_size, success, return_code,
                                      rssi_remote, snr_remote, rssi_local, snr_local,
                                      latency_ms, airtime_ms, seq, path, hops,
                                      reached_path, reached_hops, last_hop_ok,
                                      fail_reason, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
        self._counters["sent"] += 1
        if success:
            self._counters["ok"] += 1
        else:
            self._counters["fail"] += 1
        evt = {"type": "result", "run_id": run_id}
        for k in _RESULT_FIELDS:
            evt[k] = obj.get(k)
        self._broadcast(evt)
        if self._bucket:
            self._bucket.add(obj, dst, size, success)


def _coerce_path(raw_list, raw_hops) -> tuple[list[int], int]:
    """Macht aus rohen JSON-Daten ein sauberes (path, hops)-Tupel."""
    try:
        path = [int(x) for x in (raw_list or [])]
    except (TypeError, ValueError):
        path = []
    if raw_hops is None:
        hops = len(path)
    else:
        try:
            hops = int(raw_hops)
        except (TypeError, ValueError):
            hops = len(path)
    return path, hops


# ESP-NOW-Frame-Limits aus protocol.h gespiegelt.
# Min: DATA_HDR_SIZE (13) + 2 Byte CRC = 15.
# Max: 250 Byte (ESP-NOW-Standardlimit; die Firmware kappt sowieso).
SIZE_MIN = 15
SIZE_MAX = 250


def _clip_size(v: int) -> int:
    return max(SIZE_MIN, min(SIZE_MAX, int(v)))


def _make_size_picker(cfg: dict):
    """Liefert eine Closure, die pro Aufruf die naechste Paketgroesse
    zurueckgibt. Modi:
      - "list"   (default): rotiert ueber cfg["packet_sizes"].
      - "range":             rotiert ueber range(min, max+1, step).
      - "random":            zieht jede Groesse zufaellig aus [min..max].
    """
    mode = (cfg.get("size_mode") or "list").lower()

    if mode == "random":
        lo = _clip_size(cfg.get("size_min", 32))
        hi = _clip_size(cfg.get("size_max", 240))
        if hi < lo:
            lo, hi = hi, lo
        return lambda: random.randint(lo, hi)

    if mode == "range":
        lo = _clip_size(cfg.get("size_min", 32))
        hi = _clip_size(cfg.get("size_max", 240))
        step = max(1, int(cfg.get("size_step", 32)))
        seq = list(range(lo, hi + 1, step))
        if not seq:
            seq = [lo]
        idx = [0]
        def _next() -> int:
            v = seq[idx[0] % len(seq)]
            idx[0] += 1
            return v
        return _next

    # default: feste Liste rotieren
    sizes = [_clip_size(s) for s in (cfg.get("packet_sizes") or [32])]
    if not sizes:
        sizes = [32]
    idx = [0]
    def _next_list() -> int:
        v = sizes[idx[0] % len(sizes)]
        idx[0] += 1
        return v
    return _next_list


# Singleton-Instanz fuer das Dashboard.
ENGINE = LongRunEngine()
