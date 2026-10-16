"""All SQL lives here. Each call uses its own short-lived connection (safe across threads)."""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd

from analysis.lap_tracker import SAMPLE_FIELDS, CompletedLap
from config.defaults import APP_VERSION
from storage.database import connect

LAP_COLUMNS = ("lap_number", "lap_time_ms", "sector1_ms", "sector2_ms", "sector3_ms", "valid",
               "samples_complete", "pit_lap", "out_lap", "max_speed_kph", "sample_count")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SessionRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _conn(self):
        return closing(connect(self.db_path))

    # ---------- writes (called from the writer thread) ----------
    def create_session(self, session_uid: int, source: str, track_name: str | None = None) -> int:
        with self._conn() as c, c:
            cur = c.execute("INSERT INTO sessions (session_uid, source, track_name, started_at, status, app_version) "
                            "VALUES (?, ?, ?, ?, 'recording', ?)", (str(session_uid), source, track_name, _now(),
                                                                     APP_VERSION))
            return cur.lastrowid

    def update_session(self, session_id: int, **meta) -> None:
        allowed = {k: v for k, v in meta.items() if k in ("track_id", "track_name", "session_type")}
        if not allowed:
            return
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self._conn() as c, c:
            c.execute(f"UPDATE sessions SET {sets} WHERE id = ?", (*allowed.values(), session_id))

    def finish_session(self, session_id: int, status: str = "complete") -> None:
        with self._conn() as c, c:
            c.execute("UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?", (status, _now(), session_id))

    def save_lap(self, session_id: int, lap: CompletedLap, samples: list) -> None:
        """Insert (or replace, if a flashback re-ran the lap) a lap and its samples atomically."""
        with self._conn() as c, c:
            c.execute("DELETE FROM laps WHERE session_id = ? AND lap_number = ?", (session_id, lap.lap_number))
            cur = c.execute(
                "INSERT INTO laps (session_id, lap_number, lap_time_ms, sector1_ms, sector2_ms, sector3_ms, valid, "
                "samples_complete, pit_lap, out_lap, max_speed_kph, sample_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, lap.lap_number, lap.lap_time_ms, lap.sector1_ms, lap.sector2_ms, lap.sector3_ms,
                 int(lap.valid), int(lap.samples_complete), int(lap.pit_lap), int(lap.out_lap), lap.max_speed_kph,
                 len(samples)))
            lap_id = cur.lastrowid
            c.executemany(f"INSERT INTO samples (lap_id, {', '.join(SAMPLE_FIELDS)}) VALUES (?{', ?' * len(SAMPLE_FIELDS)})",
                          ((lap_id, *s) for s in samples))

    # ---------- reads (UI) ----------
    def list_sessions(self) -> pd.DataFrame:
        sql = """SELECT s.id, s.started_at, s.ended_at, s.source, s.track_name, s.track_id, s.session_type, s.status,
                        COUNT(l.id) AS laps,
                        MIN(CASE WHEN l.valid = 1 AND l.pit_lap = 0 AND l.out_lap = 0 THEN l.lap_time_ms END) AS best_lap_ms
                 FROM sessions s LEFT JOIN laps l ON l.session_id = s.id
                 GROUP BY s.id ORDER BY s.id DESC"""
        with self._conn() as c:
            return pd.read_sql_query(sql, c)

    def get_session(self, session_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def laps(self, session_id: int) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query(f"SELECT id, {', '.join(LAP_COLUMNS)} FROM laps WHERE session_id = ? "
                                     "ORDER BY lap_number", c, params=(session_id,))

    def lap_samples(self, lap_id: int) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query(f"SELECT {', '.join(SAMPLE_FIELDS)} FROM samples WHERE lap_id = ? "
                                     "ORDER BY rowid", c, params=(lap_id,))

    def reference_laps(self, track_name: str | None, include_demo: bool = False) -> pd.DataFrame:
        """Valid, fully-recorded, non-pit laps on the same track across all sessions."""
        sql = ("SELECT l.id, l.session_id, l.lap_number, l.lap_time_ms, l.sector1_ms, l.sector2_ms, l.sector3_ms, "
               "s.started_at, s.source FROM laps l JOIN sessions s ON s.id = l.session_id "
               "WHERE l.valid = 1 AND l.samples_complete = 1 AND l.pit_lap = 0 AND l.out_lap = 0 "
               "AND s.track_name IS ? " + ("" if include_demo else "AND s.source = 'live' ") +
               "ORDER BY l.lap_time_ms")
        with self._conn() as c:
            return pd.read_sql_query(sql, c, params=(track_name,))

    def delete_session(self, session_id: int) -> bool:
        with self._conn() as c, c:
            return c.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    # ---------- export ----------
    def _session_samples(self, session_id: int) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query(
                f"SELECT l.lap_number, {', '.join('s.' + f for f in SAMPLE_FIELDS)} FROM samples s "
                "JOIN laps l ON l.id = s.lap_id WHERE l.session_id = ? ORDER BY l.lap_number, s.rowid",
                c, params=(session_id,))

    def _export_stem(self, session_id: int) -> str:
        s = self.get_session(session_id)
        if s is None:
            raise KeyError(f"session {session_id} not found")
        stamp = s["started_at"].replace(":", "").replace("-", "")
        return f"purplesector_{s['source']}_session{session_id}_{stamp}"

    def export_csv(self, session_id: int, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{self._export_stem(session_id)}.csv"
        self._session_samples(session_id).to_csv(path, index=False)
        return path

    def export_json(self, session_id: int, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{self._export_stem(session_id)}.json"
        laps = self.laps(session_id).drop(columns="id")
        doc = {
            "format": "purplesector-session/1",
            "session": self.get_session(session_id),
            "laps": json.loads(laps.to_json(orient="records")),
            "samples": {"columns": ["lap_number", *SAMPLE_FIELDS],
                        "rows": self._session_samples(session_id).values.tolist()},
        }
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path
