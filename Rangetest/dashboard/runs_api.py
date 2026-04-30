"""Run-bezogene Endpoints (Liste, Detail, Start/Stop, Live-Stream)."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .config import Settings
from .db import open_db, open_db_ro
from .engine import ENGINE


_VALID_PORT = re.compile(r"^(COM\d+|/dev/[A-Za-z0-9._\-/]+)$")


class TargetIn(BaseModel):
    id: int
    label: str = ""
    distance_m: Optional[float] = None
    walls: Optional[int] = None
    notes: str = ""


class RunStartRequest(BaseModel):
    port: str
    baud: int = 115200
    db: Optional[str] = None
    rx_ports: list[str] = Field(default_factory=list)
    relay_id: int = 1
    retry_limit: int = 1
    timeout_ms: int = 200
    inter_send_ms: int = 50
    jitter_pct: int = 20
    noisefloor_samples: int = 0
    duration_s: Optional[int] = None
    targets: list[TargetIn]

    # Paketgroessen-Auswahl. Modi:
    #   "list"   = packet_sizes der Reihe nach (Default; bestehendes Verhalten)
    #   "range"  = size_min..size_max in size_step
    #   "random" = pro Paket zufaellig aus [size_min..size_max]
    size_mode: str = "list"
    packet_sizes: list[int] = Field(
        default_factory=lambda: [32, 64, 128, 192, 240])
    size_min: Optional[int] = None
    size_max: Optional[int] = None
    size_step: Optional[int] = None

    run_notes: str = ""
    environment: dict = Field(default_factory=dict)


def build_router(settings: Settings) -> APIRouter:
    """Liefert einen Router, dessen Endpoints den globalen DB-Pfad nutzen."""
    router = APIRouter(tags=["runs"])
    db_default = settings.db_path

    @router.get("/api/runs")
    def api_runs(db: Optional[str] = None) -> dict:
        db_path = Path(db) if db else db_default
        conn = open_db_ro(db_path)
        if conn is None:
            return {"db": str(db_path), "runs": []}
        try:
            rows = conn.execute(
                "SELECT id, started_at, ended_at, relay_id, notes, "
                "       firmware_version "
                "FROM run ORDER BY id DESC LIMIT 200"
            ).fetchall()
        except sqlite3.DatabaseError as e:
            raise HTTPException(500, f"DB-Fehler: {e}")
        finally:
            conn.close()
        return {
            "db": str(db_path),
            "runs": [
                {"id": r[0], "started_at": r[1], "ended_at": r[2],
                 "relay_id": r[3], "notes": r[4], "firmware_version": r[5]}
                for r in rows
            ],
        }

    @router.get("/api/run/{run_id}/summary")
    def api_run_summary(run_id: int, db: Optional[str] = None) -> dict:
        db_path = Path(db) if db else db_default
        conn = open_db_ro(db_path)
        if conn is None:
            raise HTTPException(404, f"DB nicht gefunden: {db_path}")
        try:
            return _build_summary(conn, run_id)
        finally:
            conn.close()

    @router.get("/api/run/last/profile")
    def api_run_last_profile(db: Optional[str] = None) -> dict:
        """Liefert Targets + Notes des juengsten Runs als Wizard-Vorbelegung.

        Wird vom Run-Setup-Schritt 1 ("Letzten Run klonen") aufgerufen.
        Hardware-spezifische Felder (port, rx_ports) werden bewusst nicht
        zurueckgegeben, weil sich der Hardware-Aufbau geaendert haben kann.
        """
        db_path = Path(db) if db else db_default
        conn = open_db_ro(db_path)
        if conn is None:
            return {"ok": False, "reason": "no_db"}
        try:
            row = conn.execute(
                "SELECT id, notes FROM run ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"ok": False, "reason": "empty"}
            run_id, notes = row[0], (row[1] or "")
            tm = conn.execute(
                "SELECT dst_id, label, distance_m, walls, notes "
                "FROM target_meta WHERE run_id=? ORDER BY dst_id",
                (run_id,),
            ).fetchall()
            return {
                "ok": True,
                "run_id": run_id,
                "run_notes": notes,
                "targets": [
                    {"id": r[0], "label": r[1] or "",
                     "distance_m": r[2], "walls": r[3],
                     "notes": r[4] or ""}
                    for r in tm
                ],
            }
        finally:
            conn.close()

    @router.post("/api/run/start")
    async def api_run_start(req: RunStartRequest) -> dict:
        if not _VALID_PORT.match(req.port):
            raise HTTPException(400, "invalid port")
        for p in req.rx_ports:
            if not _VALID_PORT.match(p):
                raise HTTPException(400, f"invalid rx_port: {p}")
        if not req.targets:
            raise HTTPException(400, "targets darf nicht leer sein")
        if req.size_mode not in ("list", "range", "random"):
            raise HTTPException(400, f"unbekannter size_mode: {req.size_mode}")
        if req.size_mode in ("range", "random"):
            if req.size_min is None or req.size_max is None:
                raise HTTPException(400,
                    "size_min und size_max sind fuer range/random Pflicht")
            if not (15 <= req.size_min <= 250 and 15 <= req.size_max <= 250):
                raise HTTPException(400, "size_min/size_max muss in [15..250] liegen")
        cfg = req.model_dump()
        cfg["db"] = cfg["db"] or str(db_default)
        loop = asyncio.get_running_loop()
        try:
            ENGINE.start(cfg, loop)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"ok": True, "status": ENGINE.status()}

    @router.post("/api/run/stop")
    def api_run_stop() -> dict:
        ENGINE.stop()
        return {"ok": True, "status": ENGINE.status()}

    @router.delete("/api/run/{run_id}")
    def api_run_delete(run_id: int, db: Optional[str] = None) -> dict:
        """Loescht einen Run inklusive aller abhaengigen Datensaetze."""
        # Aktiv laufenden Run nicht loeschen.
        active = ENGINE.status() or {}
        if (active.get("status") in ("running", "connecting")
                and active.get("run_id") == run_id):
            raise HTTPException(409, "Run laeuft gerade, erst stoppen.")
        db_path = Path(db) if db else db_default
        if not db_path.exists():
            raise HTTPException(404, f"DB nicht gefunden: {db_path}")
        conn = open_db(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM run WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(404, f"run {run_id} nicht gefunden")
            deleted: dict[str, int] = {}
            # Reihenfolge: erst abhaengige Tabellen, dann run selbst.
            for tbl in ("result", "rx_event", "noise_sample",
                        "bucket_stats", "target_meta", "run"):
                try:
                    cur = conn.execute(
                        f"DELETE FROM {tbl} WHERE "
                        f"{'id' if tbl == 'run' else 'run_id'}=?",
                        (run_id,))
                    deleted[tbl] = cur.rowcount
                except sqlite3.OperationalError:
                    # Tabelle fehlt evtl. in alten DBs - ignorieren.
                    deleted[tbl] = 0
            conn.commit()
            return {"ok": True, "run_id": run_id, "deleted": deleted}
        finally:
            conn.close()

    @router.get("/api/run/status")
    def api_run_status() -> dict:
        return ENGINE.status()

    @router.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        """Streamt alle Engine-Events an verbundene Clients."""
        await ws.accept()
        await ws.send_text(json.dumps(
            {"type": "hello", "status": ENGINE.status()}))
        q = ENGINE.subscribe()
        try:
            while True:
                evt = await q.get()
                await ws.send_text(json.dumps(evt, default=str))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            ENGINE.unsubscribe(q)
            try:
                await ws.close()
            except Exception:
                pass

    return router


def _build_summary(conn: sqlite3.Connection, run_id: int) -> dict:
    """Stellt das Detail-JSON fuer einen Run zusammen."""
    meta = conn.execute(
        "SELECT id, started_at, ended_at, relay_id, notes, firmware_version "
        "FROM run WHERE id=?", (run_id,)).fetchone()
    if not meta:
        raise HTTPException(404, f"run {run_id} nicht gefunden")
    totals = conn.execute(
        "SELECT COUNT(*), "
        "       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END), "
        "       ROUND(AVG(CASE WHEN success=1 THEN latency_ms END), 1) "
        "FROM result WHERE run_id=?", (run_id,)).fetchone()
    per_size = conn.execute(
        "SELECT packet_size, COUNT(*), "
        "       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) "
        "FROM result WHERE run_id=? "
        "GROUP BY packet_size ORDER BY packet_size", (run_id,)).fetchall()
    per_dst = conn.execute(
        "SELECT r.dst_id, tm.label, COUNT(*), "
        "       SUM(CASE WHEN r.success=1 THEN 1 ELSE 0 END) "
        "FROM result r LEFT JOIN target_meta tm "
        "  ON tm.run_id=r.run_id AND tm.dst_id=r.dst_id "
        "WHERE r.run_id=? GROUP BY r.dst_id, tm.label", (run_id,)).fetchall()
    buckets: list[tuple] = []
    try:
        buckets = conn.execute(
            "SELECT bucket_ts, dst_id, packet_size, ok, total, "
            "       rssi_med, snr_med, lat_med, lat_p95 "
            "FROM bucket_stats WHERE run_id=? ORDER BY bucket_minute",
            (run_id,)).fetchall()
    except sqlite3.OperationalError:
        pass
    total, ok, lat = totals or (0, 0, None)
    return {
        "run": {
            "id": meta[0], "started_at": meta[1], "ended_at": meta[2],
            "relay_id": meta[3], "notes": meta[4],
            "firmware_version": meta[5],
        },
        "totals": {
            "sent": total or 0, "ok": ok or 0,
            "pdr_pct": (100.0 * (ok or 0) / total) if total else 0.0,
            "avg_latency_ms": lat,
        },
        "per_size": [
            {"size": s, "total": t, "ok": o,
             "pdr_pct": (100.0 * o / t) if t else 0.0}
            for (s, t, o) in per_size
        ],
        "per_dst": [
            {"dst": d, "label": lbl, "total": t, "ok": o,
             "pdr_pct": (100.0 * o / t) if t else 0.0}
            for (d, lbl, t, o) in per_dst
        ],
        "buckets": [
            {"ts": ts, "dst": d, "size": s, "ok": o, "total": t,
             "rssi_med": rm, "snr_med": sm, "lat_med": lm, "lat_p95": lp}
            for (ts, d, s, o, t, rm, sm, lm, lp) in buckets
        ],
    }
