"""SQLite-Schema und Verbindungs-Helpers.

Legt die Tabellen ``run``, ``target_meta``, ``result``, ``rx_event``,
``noise_sample`` sowie ``bucket_stats`` an und enthaelt die Migration
fuer aeltere DBs (zusaetzliche Spalten wie ``path``, ``hops``,
``reached_path`` u. a.).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    relay_id         INTEGER NOT NULL,
    notes            TEXT,
    environment      TEXT,
    firmware_version TEXT,
    lora_config      TEXT
);

CREATE TABLE IF NOT EXISTS target_meta (
    run_id     INTEGER NOT NULL REFERENCES run(id),
    dst_id     INTEGER NOT NULL,
    label      TEXT,
    distance_m REAL,
    walls      INTEGER,
    notes      TEXT,
    PRIMARY KEY (run_id, dst_id)
);

CREATE TABLE IF NOT EXISTS result (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES run(id),
    dst_id        INTEGER NOT NULL,
    via_relay_id  INTEGER NOT NULL,
    attempt_no    INTEGER NOT NULL,
    packet_size   INTEGER NOT NULL,
    success       INTEGER NOT NULL,
    return_code   TEXT NOT NULL,
    rssi_remote   INTEGER,
    snr_remote    INTEGER,
    rssi_local    INTEGER,
    snr_local     INTEGER,
    latency_ms    INTEGER,
    airtime_ms    INTEGER,
    seq           INTEGER,
    path          TEXT,
    hops          INTEGER,
    reached_path  TEXT,
    reached_hops  INTEGER,
    last_hop_ok   INTEGER,
    fail_reason   TEXT,
    ts            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rx_event (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES run(id),
    port     TEXT,
    src_id   INTEGER,
    seq      INTEGER,
    size     INTEGER,
    rssi     INTEGER,
    snr      INTEGER,
    is_dup   INTEGER NOT NULL DEFAULT 0,
    ts       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS noise_sample (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES run(id),
    samples  INTEGER,
    avg      INTEGER,
    min      INTEGER,
    max      INTEGER,
    ts       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bucket_stats (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    bucket_ts     TEXT NOT NULL,
    bucket_minute INTEGER NOT NULL,
    dst_id        INTEGER,
    packet_size   INTEGER,
    ok            INTEGER,
    total         INTEGER,
    rssi_med      REAL,
    snr_med       REAL,
    lat_med       REAL,
    lat_p95       REAL
);

CREATE INDEX IF NOT EXISTS idx_result_run  ON result(run_id);
CREATE INDEX IF NOT EXISTS idx_result_dst  ON result(dst_id);
CREATE INDEX IF NOT EXISTS idx_rx_run      ON rx_event(run_id);
CREATE INDEX IF NOT EXISTS idx_bucket_run  ON bucket_stats(run_id);
"""

# Spalten, die in alten DBs nachgeruestet werden muessen.
_RESULT_MIGRATIONS = (
    ("path", "TEXT"),
    ("hops", "INTEGER"),
    ("reached_path", "TEXT"),
    ("reached_hops", "INTEGER"),
    ("last_hop_ok", "INTEGER"),
    ("fail_reason", "TEXT"),
)


def open_db(db_path: Path) -> sqlite3.Connection:
    """Oeffnet (legt ggf. an) die SQLite-Datei und stellt das Schema sicher."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(result)").fetchall()}
    for name, typ in _RESULT_MIGRATIONS:
        if name not in cols:
            conn.execute(f"ALTER TABLE result ADD COLUMN {name} {typ}")
    conn.commit()
    return conn


def open_db_ro(db_path: Path) -> sqlite3.Connection | None:
    """Read-only-Verbindung. Liefert ``None`` wenn die Datei fehlt."""
    if not db_path.exists():
        return None
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
