"""SQLite schema, connections and startup recovery.

WAL mode lets the UI read while the writer thread writes. Schema version is kept in
the `meta` table; future migrations go in MIGRATIONS keyed by target version.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config.defaults import SCHEMA_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uid TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('live', 'demo')),
    track_id INTEGER,
    track_name TEXT,
    session_type TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('recording', 'complete', 'interrupted')),
    app_version TEXT
);
CREATE TABLE IF NOT EXISTS laps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lap_number INTEGER NOT NULL,
    lap_time_ms INTEGER NOT NULL,
    sector1_ms INTEGER,
    sector2_ms INTEGER,
    sector3_ms INTEGER,
    valid INTEGER NOT NULL,
    samples_complete INTEGER NOT NULL,
    pit_lap INTEGER NOT NULL,
    out_lap INTEGER NOT NULL,
    max_speed_kph REAL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (session_id, lap_number)
);
CREATE TABLE IF NOT EXISTS samples (
    lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
    session_time REAL NOT NULL,
    lap_ms INTEGER NOT NULL,
    lap_distance REAL NOT NULL,
    speed REAL NOT NULL,
    throttle REAL NOT NULL,
    brake REAL NOT NULL,
    steer REAL NOT NULL,
    gear INTEGER NOT NULL,
    rpm INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_by_lap ON samples (lap_id);
CREATE INDEX IF NOT EXISTS laps_by_session ON laps (session_id);
"""

MIGRATIONS: dict[int, str] = {
    # v2: track map positions, per-lap fuel/tyre use, imported laps from other drivers
    2: """
    ALTER TABLE samples ADD COLUMN pos_x REAL;
    ALTER TABLE samples ADD COLUMN pos_y REAL;
    ALTER TABLE samples ADD COLUMN pos_z REAL;
    ALTER TABLE laps ADD COLUMN fuel_used_kg REAL;
    ALTER TABLE laps ADD COLUMN tyre_wear_pct REAL;
    ALTER TABLE sessions ADD COLUMN imported INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE sessions ADD COLUMN driver_name TEXT;
    """,
    # v3: car setup and tyre at each line crossing, race control messages
    3: """
    ALTER TABLE laps ADD COLUMN setup_json TEXT;
    ALTER TABLE laps ADD COLUMN tyre_age_laps INTEGER;
    ALTER TABLE laps ADD COLUMN tyre_compound TEXT;
    CREATE TABLE IF NOT EXISTS events (
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        session_time REAL NOT NULL,
        lap_number INTEGER,
        code TEXT NOT NULL,
        message TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS events_by_session ON events (session_id);
    """,
}


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(path: Path) -> int:
    """Create/upgrade the schema and mark sessions left 'recording' by a crash as 'interrupted'.
    Returns the number of recovered sessions. Call once at startup, before the writer starts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        with conn:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            version = int(row["value"]) if row else 0
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema v{version} is newer than this app (v{SCHEMA_VERSION}).")
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for stmt in filter(str.strip, MIGRATIONS.get(v, "").split(";")):
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as exc:  # re-run after a half-applied upgrade
                        if "duplicate column" not in str(exc):
                            raise
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            cur = conn.execute("UPDATE sessions SET status = 'interrupted' WHERE status = 'recording'")
            # a crash before the first completed lap leaves an empty shell: drop it
            conn.execute("DELETE FROM sessions WHERE status = 'interrupted' "
                         "AND id NOT IN (SELECT DISTINCT session_id FROM laps)")
            return cur.rowcount
    finally:
        conn.close()
