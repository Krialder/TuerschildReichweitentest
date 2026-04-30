"""60-Sekunden-Bucket-Aggregation.

Sammelt waehrend eines Long-Runs einzelne Result-Datenpunkte, gruppiert
sie pro Minute nach (dst, packet_size) und schreibt am Minutenwechsel
Median/p95-Statistiken in die ``bucket_stats``-Tabelle. Wird vom
``LongRunEngine`` aufgerufen.
"""
from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from typing import Callable, Optional


class BucketAggregator:
    """Aggregiert Results in 60-Sekunden-Fenstern."""

    def __init__(self, conn: sqlite3.Connection,
                 db_lock: threading.Lock,
                 run_id: int,
                 broadcast: Callable[[dict], None]) -> None:
        self._conn = conn
        self._db_lock = db_lock
        self._run_id = run_id
        self._broadcast = broadcast
        self._buf: list[dict] = []
        self._minute: Optional[int] = None

    def add(self, obj: dict, dst: int, size: int, success: bool) -> None:
        """Einen Result-Eintrag aufnehmen. Loest bei Minutenwechsel ein Flush aus."""
        ts = time.time()
        minute = int(ts // 60)
        if self._minute is None:
            self._minute = minute
        if minute != self._minute:
            self.flush()
            self._minute = minute
        self._buf.append({
            "dst": int(obj.get("dst", dst)),
            "size": int(obj.get("size", size)),
            "ok": 1 if success else 0,
            "rssi": int(obj.get("rssi_remote", 0)) if success else None,
            "snr":  int(obj.get("snr_remote", 0))  if success else None,
            "lat":  int(obj.get("latency_ms", 0))  if success else None,
        })

    def maybe_flush(self) -> None:
        """Flush, falls die laufende Minute schon vorbei ist (idle-Phase)."""
        if self._minute is None:
            return
        if int(time.time() // 60) != self._minute:
            self.flush()
            self._minute = int(time.time() // 60)

    def flush(self) -> None:
        """Aktuellen Puffer in DB schreiben und an Listener broadcasten."""
        if not self._buf or self._minute is None:
            self._buf = []
            return
        groups: dict[tuple[int, int], list[dict]] = {}
        for r in self._buf:
            groups.setdefault((r["dst"], r["size"]), []).append(r)
        bucket_iso = time.strftime(
            "%Y-%m-%dT%H:%M:00Z", time.gmtime(self._minute * 60),
        )
        rows: list[tuple] = []
        events: list[dict] = []
        for (dst, size), rs in groups.items():
            ok = sum(r["ok"] for r in rs)
            total = len(rs)
            rssis = [r["rssi"] for r in rs if r["rssi"] is not None]
            snrs  = [r["snr"]  for r in rs if r["snr"]  is not None]
            lats  = sorted(r["lat"] for r in rs if r["lat"] is not None)
            rssi_med = statistics.median(rssis) if rssis else None
            snr_med  = statistics.median(snrs)  if snrs  else None
            lat_med  = statistics.median(lats)  if lats  else None
            lat_p95 = (
                lats[max(0, int(round(0.95 * (len(lats) - 1))))]
                if lats else None
            )
            rows.append((
                self._run_id, bucket_iso, self._minute,
                dst, size, ok, total,
                rssi_med, snr_med, lat_med, lat_p95,
            ))
            events.append({
                "type": "bucket", "run_id": self._run_id,
                "bucket_ts": bucket_iso, "dst": dst, "size": size,
                "ok": ok, "total": total,
                "rssi_med": rssi_med, "snr_med": snr_med,
                "lat_med": lat_med, "lat_p95": lat_p95,
            })
        if rows:
            with self._db_lock:
                self._conn.executemany(
                    """INSERT INTO bucket_stats(run_id, bucket_ts, bucket_minute,
                                                dst_id, packet_size, ok, total,
                                                rssi_med, snr_med, lat_med, lat_p95)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                self._conn.commit()
        for evt in events:
            self._broadcast(evt)
        self._buf = []
