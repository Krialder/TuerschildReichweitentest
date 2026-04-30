"""Analyse-API.

Liefert aggregierte KPIs aus der SQLite-DB fuer beliebige Run-Auswahlen.
Alle Endpoints sind reine Lese-Operationen.

Vergleichs-Achsen (``group_by``):
    hops, size, dst, channel, lr, hour, weekday, date, fw, notes, run,
    fail_reason, last_hop_ok

Metriken pro Gruppe:
    total, ok, pdr_pct, pdr_ci_low_pct, pdr_ci_high_pct (Wilson 95 %),
    rssi_med/_p10/_p90, snr_med/_p10/_p90, lat_med/_p95/_max,
    avg_attempts, retry_rate_pct, throughput_bps,
    longest_fail_streak, fail_reason_top, last_hop_ok_top
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import open_db
from .stats import (
    iso_duration_s, longest_fail_streak, percentile, wilson,
)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

_DB_PATH: Optional[Path] = None


def set_db_path(p: Path) -> None:
    """Wird vom Hauptmodul beim App-Bau gesetzt."""
    global _DB_PATH
    _DB_PATH = p
    # Sicherstellen, dass die Migrationsspalten existieren - sonst kippen
    # SELECTs auf alten DBs mit OperationalError.
    try:
        if p.exists():
            conn = open_db(p)
            conn.close()
    except Exception:
        pass


def _conn() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise HTTPException(500, "DB-Pfad nicht gesetzt.")
    if not _DB_PATH.exists():
        raise HTTPException(404, f"DB fehlt: {_DB_PATH}")
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


# Whitelist: erlaubte group_by-Werte und das zugehoerige SQL-Snippet.
# Keine User-Eingabe wird je in SQL eingesetzt - immer ueber dieses Mapping.
_GROUP_SQL = {
    "hops":    "COALESCE(r.hops, 0)",
    "size":    "r.packet_size",
    "dst":     "r.dst_id",
    "channel": "json_extract(run.lora_config, '$.channel')",
    "lr":      "json_extract(run.lora_config, '$.lr')",
    "hour":    "CAST(strftime('%H', r.ts) AS INTEGER)",
    "weekday": "CAST(strftime('%w', r.ts) AS INTEGER)",  # 0 = Sonntag
    "date":    "date(r.ts)",
    "fw":      "run.firmware_version",
    "notes":   "run.notes",
    "run":     "r.run_id",
    "fail_reason": "COALESCE(r.fail_reason, '')",
    "last_hop_ok": "COALESCE(r.last_hop_ok, 0)",
}


# ---------- Models ---------------------------------------------------
class RunSummaryItem(BaseModel):
    id: int
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_s: Optional[int] = None
    is_long: bool = False
    fw: Optional[str] = None
    channel: Optional[int] = None
    lr: Optional[int] = None
    notes: Optional[str] = None
    total: int = 0
    ok: int = 0
    pdr_pct: float = 0.0
    max_hops: int = 0
    dst_ids: list[int] = Field(default_factory=list)
    packet_sizes: list[int] = Field(default_factory=list)


class AggregateRequest(BaseModel):
    run_ids: list[int] = Field(default_factory=list)
    min_duration_s: int = 0
    group_by: str = "size"
    success_only: bool = False
    filter_hops: Optional[list[int]] = None
    filter_size: Optional[list[int]] = None
    filter_dst: Optional[list[int]] = None


class HeatmapRequest(BaseModel):
    run_ids: list[int] = Field(default_factory=list)
    x: str = "hour"
    y: str = "weekday"
    metric: str = "pdr"


class TimelineRequest(BaseModel):
    run_ids: list[int] = Field(default_factory=list)
    bucket_s: int = 60
    metric: str = "pdr"


class DistributionRequest(BaseModel):
    run_ids: list[int] = Field(default_factory=list)
    group_by: str = "size"
    metric: str = "rssi"


# ---------- Endpoints ------------------------------------------------
@router.get("/runs")
def list_runs() -> list[RunSummaryItem]:
    """Run-Liste mit Kennzahlen - Basis fuer das Filterpanel im UI."""
    out: list[RunSummaryItem] = []
    with _conn() as c:
        runs = c.execute(
            "SELECT id, started_at, ended_at, firmware_version, lora_config, "
            "       notes FROM run ORDER BY id DESC"
        ).fetchall()
        for run in runs:
            rid = int(run["id"])
            stats = c.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(success),0) AS ok, "
                "       COALESCE(MAX(hops), 0) AS maxh "
                "FROM result WHERE run_id=?", (rid,),
            ).fetchone()
            dsts = [int(r[0]) for r in c.execute(
                "SELECT DISTINCT dst_id FROM result WHERE run_id=? "
                "ORDER BY dst_id", (rid,)).fetchall()]
            sizes = [int(r[0]) for r in c.execute(
                "SELECT DISTINCT packet_size FROM result WHERE run_id=? "
                "ORDER BY packet_size", (rid,)).fetchall()]
            try:
                lc = json.loads(run["lora_config"] or "{}")
            except (json.JSONDecodeError, TypeError):
                lc = {}
            duration = None
            if run["started_at"] and run["ended_at"]:
                duration = iso_duration_s(run["started_at"], run["ended_at"])
            n = int(stats["n"] or 0)
            ok = int(stats["ok"] or 0)
            out.append(RunSummaryItem(
                id=rid,
                started_at=run["started_at"],
                ended_at=run["ended_at"],
                duration_s=duration,
                fw=run["firmware_version"],
                channel=lc.get("channel"),
                lr=int(bool(lc.get("lr"))) if "lr" in lc else None,
                notes=run["notes"],
                total=n, ok=ok,
                pdr_pct=(100.0 * ok / n) if n else 0.0,
                max_hops=int(stats["maxh"] or 0),
                dst_ids=dsts, packet_sizes=sizes,
            ))
    return out


@router.post("/aggregate")
def aggregate(req: AggregateRequest) -> dict:
    """Aggregat ueber Runs, gruppiert nach ``req.group_by``."""
    if req.group_by not in _GROUP_SQL:
        raise HTTPException(400, f"Unbekanntes group_by: {req.group_by}")
    where, params = _build_filters(req)
    sql = (
        f"SELECT {_GROUP_SQL[req.group_by]} AS g, "
        "       r.success AS s, r.rssi_remote AS rssi, r.snr_remote AS snr, "
        "       r.latency_ms AS lat, r.attempt_no AS att, r.packet_size AS sz, "
        "       r.fail_reason AS fr, r.last_hop_ok AS lh, r.id AS rid "
        "FROM result r JOIN run ON run.id = r.run_id "
        f"WHERE {where} ORDER BY r.run_id, r.id"
    )
    rows: dict[Any, list[sqlite3.Row]] = {}
    with _conn() as c:
        for row in c.execute(sql, params).fetchall():
            rows.setdefault(row["g"], []).append(row)
    out_groups = []
    for g, grows in sorted(rows.items(), key=lambda kv: (kv[0] is None, kv[0])):
        out_groups.append(_group_metrics(g, grows))
    return {
        "group_by": req.group_by,
        "n_runs": len(req.run_ids) if req.run_ids else None,
        "groups": out_groups,
    }


def _group_metrics(g: Any, grows: list[sqlite3.Row]) -> dict:
    """Faltet eine Gruppe von Result-Zeilen zu einer Kennzahl-Reihe."""
    ok_list = [r["s"] for r in grows]
    ok = sum(ok_list)
    n = len(ok_list)
    rssis = [r["rssi"] for r in grows if r["s"] and r["rssi"] is not None]
    snrs  = [r["snr"]  for r in grows if r["s"] and r["snr"]  is not None]
    lats  = [r["lat"]  for r in grows if r["s"] and r["lat"]  is not None]
    atts  = [r["att"]  for r in grows if r["s"] and r["att"]  is not None]
    bytes_ok = sum((r["sz"] or 0) for r in grows if r["s"])
    lat_sum_ok_s = sum(lats) / 1000.0 if lats else 0.0
    thr = (bytes_ok * 8 / lat_sum_ok_s) if lat_sum_ok_s > 0 else None
    ci_lo, ci_hi = wilson(ok, n)
    fr_top = Counter(r["fr"] or "" for r in grows if not r["s"]).most_common(1)
    lh_top = Counter(r["lh"] or 0 for r in grows if not r["s"]).most_common(1)
    return {
        "group": g,
        "total": n, "ok": ok, "fail": n - ok,
        "pdr_pct": (100.0 * ok / n) if n else 0.0,
        "pdr_ci_low_pct": 100.0 * ci_lo,
        "pdr_ci_high_pct": 100.0 * ci_hi,
        "rssi_med": percentile(rssis, 50),
        "rssi_p10": percentile(rssis, 10),
        "rssi_p90": percentile(rssis, 90),
        "snr_med":  percentile(snrs, 50),
        "snr_p10":  percentile(snrs, 10),
        "snr_p90":  percentile(snrs, 90),
        "lat_med":  percentile(lats, 50),
        "lat_p95":  percentile(lats, 95),
        "lat_max":  max(lats) if lats else None,
        "avg_attempts": (sum(atts) / len(atts)) if atts else None,
        "retry_rate_pct":
            (100.0 * sum(1 for a in atts if a > 1) / len(atts)) if atts else None,
        "throughput_bps": thr,
        "longest_fail_streak": longest_fail_streak(ok_list),
        "fail_reason_top": fr_top[0][0] if fr_top else None,
        "last_hop_ok_top": lh_top[0][0] if lh_top else None,
    }


@router.post("/heatmap")
def heatmap(req: HeatmapRequest) -> dict:
    """Liefert eine ``x x y``-Matrix mit gewaehlter Metrik."""
    if req.x not in _GROUP_SQL or req.y not in _GROUP_SQL:
        raise HTTPException(400, "x/y unbekannt.")
    if req.metric not in ("pdr", "rssi", "snr", "lat"):
        raise HTTPException(400, "metric unbekannt.")
    base = AggregateRequest(run_ids=req.run_ids, group_by=req.x)
    where, params = _build_filters(base)
    sql = (
        f"SELECT {_GROUP_SQL[req.x]} AS gx, {_GROUP_SQL[req.y]} AS gy, "
        " r.success AS s, r.rssi_remote AS rssi, "
        " r.snr_remote AS snr, r.latency_ms AS lat "
        "FROM result r JOIN run ON run.id = r.run_id "
        f"WHERE {where}"
    )
    cells: dict[tuple, list] = {}
    with _conn() as c:
        for row in c.execute(sql, params):
            cells.setdefault((row["gx"], row["gy"]), []).append(row)
    xs = sorted({k[0] for k in cells}, key=lambda v: (v is None, v))
    ys = sorted({k[1] for k in cells}, key=lambda v: (v is None, v))
    matrix = [[_metric_for(cells.get((x, y), []), req.metric) for x in xs]
              for y in ys]
    return {
        "x": req.x, "y": req.y, "metric": req.metric,
        "x_labels": [str(v) for v in xs],
        "y_labels": [str(v) for v in ys],
        "cells": matrix,
    }


@router.post("/timeline")
def timeline(req: TimelineRequest) -> dict:
    """Verlauf der Metrik in Zeit-Buckets je Run."""
    if req.bucket_s not in (60, 300, 900, 1800, 3600):
        raise HTTPException(400, "bucket_s nicht in {60,300,900,1800,3600}.")
    if req.metric not in ("pdr", "rssi", "snr", "lat"):
        raise HTTPException(400, "metric unbekannt.")
    base = AggregateRequest(run_ids=req.run_ids, group_by="size")
    where, params = _build_filters(base)
    bucket_expr = (
        f"datetime((CAST(strftime('%s', r.ts) AS INTEGER) / {req.bucket_s}) "
        f"* {req.bucket_s}, 'unixepoch')"
    )
    sql = (
        f"SELECT r.run_id AS rid, {bucket_expr} AS ts, "
        " r.success AS s, r.rssi_remote AS rssi, "
        " r.snr_remote AS snr, r.latency_ms AS lat "
        f"FROM result r JOIN run ON run.id = r.run_id WHERE {where}"
    )
    cells: dict[tuple, list] = {}
    with _conn() as c:
        for row in c.execute(sql, params):
            cells.setdefault((int(row["rid"]), row["ts"]), []).append(row)
    series: dict[int, dict[str, list]] = {}
    for (rid, ts), data in cells.items():
        s = series.setdefault(rid, {"ts": [], "value": [], "total": []})
        s["ts"].append(ts)
        s["value"].append(_metric_for(data, req.metric))
        s["total"].append(len(data))
    out_runs = []
    for rid, s in series.items():
        order = sorted(range(len(s["ts"])), key=lambda i: s["ts"][i])
        out_runs.append({
            "run_id": rid,
            "ts":    [s["ts"][i]    for i in order],
            "value": [s["value"][i] for i in order],
            "total": [s["total"][i] for i in order],
        })
    out_runs.sort(key=lambda r: r["run_id"])
    return {"bucket_s": req.bucket_s, "metric": req.metric, "runs": out_runs}


@router.post("/distribution")
def distribution(req: DistributionRequest) -> dict:
    """Quantile pro Gruppe + reduzierte CDF-Stichproben."""
    if req.group_by not in _GROUP_SQL:
        raise HTTPException(400, f"group_by={req.group_by} unbekannt.")
    if req.metric not in ("rssi", "snr", "lat"):
        raise HTTPException(400, "metric: nur rssi/snr/lat.")
    base = AggregateRequest(run_ids=req.run_ids, group_by=req.group_by)
    where, params = _build_filters(base)
    col = {
        "rssi": "r.rssi_remote",
        "snr": "r.snr_remote",
        "lat": "r.latency_ms",
    }[req.metric]
    sql = (
        f"SELECT {_GROUP_SQL[req.group_by]} AS g, {col} AS v "
        "FROM result r JOIN run ON run.id = r.run_id "
        f"WHERE {where} AND r.success = 1 AND {col} IS NOT NULL"
    )
    buckets: dict[Any, list[float]] = {}
    with _conn() as c:
        for row in c.execute(sql, params):
            buckets.setdefault(row["g"], []).append(float(row["v"]))
    out = []
    for g, vals in sorted(buckets.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if not vals:
            continue
        cdf = vals
        if len(vals) > 1000:
            step = len(vals) / 1000
            cdf = [vals[int(i * step)] for i in range(1000)]
        out.append({
            "group": g, "n": len(vals),
            "p10": percentile(vals, 10), "p25": percentile(vals, 25),
            "p50": percentile(vals, 50), "p75": percentile(vals, 75),
            "p90": percentile(vals, 90),
            "min": min(vals), "max": max(vals),
            "cdf_samples": sorted(cdf),
        })
    return {"group_by": req.group_by, "metric": req.metric, "groups": out}


@router.post("/scatter_distance")
def scatter_distance(req: AggregateRequest) -> dict:
    """Distanz vs RSSI (aus ``target_meta``) pro (run, dst)."""
    where, params = _build_filters(req)
    sql = (
        "SELECT r.run_id AS rid, r.dst_id AS dst, "
        "       AVG(r.rssi_remote) AS rssi_avg, "
        "       COUNT(*) AS n, SUM(r.success) AS ok, "
        "       MAX(r.hops) AS hops, "
        "       tm.distance_m AS dist, tm.walls AS walls, tm.label AS label "
        "FROM result r JOIN run ON run.id = r.run_id "
        "LEFT JOIN target_meta tm "
        "  ON tm.run_id = r.run_id AND tm.dst_id = r.dst_id "
        f"WHERE {where} AND r.success = 1 "
        "GROUP BY r.run_id, r.dst_id"
    )
    points = []
    with _conn() as c:
        for row in c.execute(sql, params):
            if row["dist"] is None:
                continue
            points.append({
                "run_id": int(row["rid"]),
                "dst": int(row["dst"]),
                "label": row["label"],
                "distance_m": float(row["dist"]),
                "walls": row["walls"],
                "rssi_avg": float(row["rssi_avg"])
                            if row["rssi_avg"] is not None else None,
                "n": int(row["n"]), "ok": int(row["ok"] or 0),
                "hops": int(row["hops"] or 0),
            })
    return {"points": points}


# ---------- Internals ------------------------------------------------
def _metric_for(data: list, metric: str) -> Optional[float]:
    if not data:
        return None
    if metric == "pdr":
        n = len(data)
        ok = sum(r["s"] for r in data)
        return 100.0 * ok / n
    key = {"rssi": "rssi", "snr": "snr", "lat": "lat"}[metric]
    vals = [r[key] for r in data if r["s"] and r[key] is not None]
    return percentile(vals, 50) if vals else None


def _build_filters(req: AggregateRequest) -> tuple[str, list]:
    """Baut die WHERE-Klausel mit nur parametrisierten User-Werten."""
    where = ["1=1"]
    params: list = []
    if req.run_ids:
        ids = [int(x) for x in req.run_ids]
        if not ids:
            raise HTTPException(400, "run_ids ungueltig.")
        where.append(f"r.run_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if req.min_duration_s and req.min_duration_s > 0:
        where.append(
            "(julianday(run.ended_at) - julianday(run.started_at)) * 86400 >= ?"
        )
        params.append(req.min_duration_s)
    if req.success_only:
        where.append("r.success = 1")
    if req.filter_hops:
        ids = [int(x) for x in req.filter_hops]
        where.append(f"COALESCE(r.hops, 0) IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if req.filter_size:
        ids = [int(x) for x in req.filter_size]
        where.append(f"r.packet_size IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if req.filter_dst:
        ids = [int(x) for x in req.filter_dst]
        where.append(f"r.dst_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    return " AND ".join(where), params
