"""All SQL lives here. Each call uses its own short-lived connection (safe across threads)."""
from __future__ import annotations

import json
import math
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd

from analysis.lap_tracker import BASE_SAMPLE_FIELDS, SAMPLE_FIELDS, CompletedLap
from config.defaults import APP_VERSION
from storage.database import connect

LAP_COLUMNS = ("lap_number", "lap_time_ms", "sector1_ms", "sector2_ms", "sector3_ms", "valid",
               "samples_complete", "pit_lap", "out_lap", "max_speed_kph", "sample_count", "fuel_used_kg",
               "tyre_wear_pct")
# your own laps only: an imported lap from another driver is never your personal best
OWN = "s.imported = 0"
EXPORT_FORMAT = "purplesector-session/1"
MAX_IMPORT_LAPS = 500
MAX_IMPORT_ROWS = 3_000_000


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
        allowed = {k: v for k, v in meta.items() if k in ("track_id", "track_name", "session_type", "driver_name")}
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
            self._insert_lap(c, session_id, lap, samples)

    @staticmethod
    def _insert_lap(c, session_id: int, lap: CompletedLap, samples: list) -> None:
        c.execute("DELETE FROM laps WHERE session_id = ? AND lap_number = ?", (session_id, lap.lap_number))
        cur = c.execute(
            "INSERT INTO laps (session_id, lap_number, lap_time_ms, sector1_ms, sector2_ms, sector3_ms, valid, "
            "samples_complete, pit_lap, out_lap, max_speed_kph, sample_count, fuel_used_kg, tyre_wear_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, lap.lap_number, lap.lap_time_ms, lap.sector1_ms, lap.sector2_ms, lap.sector3_ms,
             int(lap.valid), int(lap.samples_complete), int(lap.pit_lap), int(lap.out_lap), lap.max_speed_kph,
             len(samples), lap.fuel_used_kg, lap.tyre_wear_pct))
        lap_id, n = cur.lastrowid, len(SAMPLE_FIELDS)
        c.executemany(f"INSERT INTO samples (lap_id, {', '.join(SAMPLE_FIELDS)}) VALUES (?{', ?' * n})",
                      ((lap_id, *s, *(None,) * (n - len(s))) for s in samples))  # pad missing positions

    # ---------- reads (UI) ----------
    def list_sessions(self) -> pd.DataFrame:
        sql = """SELECT s.id, s.started_at, s.ended_at, s.source, s.track_name, s.track_id, s.session_type, s.status,
                        s.imported, s.driver_name, COUNT(l.id) AS laps,
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
               "s.started_at, s.source, s.imported, s.driver_name FROM laps l JOIN sessions s ON s.id = l.session_id "
               "WHERE l.valid = 1 AND l.samples_complete = 1 AND l.pit_lap = 0 AND l.out_lap = 0 "
               "AND s.track_name IS ? " + ("" if include_demo else "AND s.source = 'live' ") +
               "ORDER BY l.lap_time_ms")
        with self._conn() as c:
            return pd.read_sql_query(sql, c, params=(track_name,))

    def best_lap_ms(self, track_name: str | None, source: str) -> int | None:
        """Best valid, non-pit lap ever recorded on this track from this source (live never mixes with demo)."""
        if not track_name:
            return None
        with self._conn() as c:
            row = c.execute("SELECT MIN(l.lap_time_ms) FROM laps l JOIN sessions s ON s.id = l.session_id "
                            "WHERE l.valid = 1 AND l.pit_lap = 0 AND l.out_lap = 0 AND s.track_name = ? "
                            f"AND s.source = ? AND {OWN}", (track_name, source)).fetchone()
            return row[0]

    def best_reference_lap(self, track_name: str | None, source: str) -> tuple[int, pd.DataFrame] | None:
        """(lap time ms, samples) of the fastest fully recorded valid lap on this track, for the live delta."""
        if not track_name:
            return None
        with self._conn() as c:
            row = c.execute("SELECT l.id, l.lap_time_ms FROM laps l JOIN sessions s ON s.id = l.session_id "
                            "WHERE l.valid = 1 AND l.samples_complete = 1 AND l.pit_lap = 0 AND l.out_lap = 0 "
                            f"AND s.track_name = ? AND s.source = ? AND {OWN} ORDER BY l.lap_time_ms LIMIT 1",
                            (track_name, source)).fetchone()
        return None if row is None else (row["lap_time_ms"], self.lap_samples(row["id"]))

    def progress(self, track_name: str, source: str) -> pd.DataFrame:
        """Your best valid lap per session on this track, oldest first (sessions without one are left out)."""
        sql = ("SELECT s.id, s.started_at, s.session_type, MIN(l.lap_time_ms) AS best_lap_ms, "
               "COUNT(l.id) AS valid_laps FROM sessions s JOIN laps l ON l.session_id = s.id "
               "WHERE l.valid = 1 AND l.pit_lap = 0 AND l.out_lap = 0 AND s.track_name = ? AND s.source = ? "
               f"AND {OWN} GROUP BY s.id ORDER BY s.started_at, s.id")
        with self._conn() as c:
            return pd.read_sql_query(sql, c, params=(track_name, source))

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
        rows = self._session_samples(session_id)
        doc = {
            "format": "purplesector-session/1",
            "session": self.get_session(session_id),
            "laps": json.loads(laps.to_json(orient="records")),
            "samples": {"columns": ["lap_number", *SAMPLE_FIELDS],
                        "rows": rows.astype(object).where(rows.notna(), None).values.tolist()},  # NaN is not JSON
        }
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    # ---------- import (another driver's export) ----------
    def import_session(self, doc, driver_name: str) -> int:
        """Store another driver's exported session as a comparison reference. The file is untrusted:
        every field is checked, and the session is flagged imported so it never counts as your best."""
        session, laps, columns, rows = _validate_export(doc)
        name = " ".join(str(driver_name).split())[:48] or "Imported driver"
        started = session.get("started_at")
        try:
            started = datetime.fromisoformat(started).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            started = _now()
        by_lap: dict[int, list] = {}
        width = len(columns) - 1
        for r in rows:
            by_lap.setdefault(int(r[0]), []).append(tuple(r[1:1 + width]))
        with self._conn() as c, c:
            cur = c.execute(
                "INSERT INTO sessions (session_uid, source, track_id, track_name, session_type, started_at, ended_at, "
                "status, app_version, imported, driver_name) VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', ?, 1, ?)",
                (str(session.get("session_uid", ""))[:32], session["source"], session.get("track_id"),
                 session["track_name"], session.get("session_type"), started, _now(), APP_VERSION, name))
            sid = cur.lastrowid
            for lap in laps:
                self._insert_lap(c, sid, lap, by_lap.get(lap.lap_number, []))
        return sid


def _num(v, *, integer=False, optional=True):
    if v is None and optional:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise ValueError(f"expected a number, got {v!r}")
    if integer and v != int(v):
        raise ValueError(f"expected a whole number, got {v!r}")
    return int(v) if integer else float(v)


def _validate_export(doc):
    """(session meta, CompletedLaps, sample columns, sample rows) from an export document, or ValueError."""
    if not isinstance(doc, dict) or doc.get("format") != EXPORT_FORMAT:
        raise ValueError("Not a PurpleSector session export (the JSON 'format' field is missing or different).")
    session, laps_in, samples = doc.get("session"), doc.get("laps"), doc.get("samples")
    if not isinstance(session, dict) or not isinstance(laps_in, list) or not isinstance(samples, dict):
        raise ValueError("The export is incomplete: session, laps or samples are missing.")
    track = session.get("track_name")
    if not isinstance(track, str) or not track.strip() or len(track) > 100:
        raise ValueError("The export has no track name, so it can't be matched to your laps.")
    if session.get("source") not in ("live", "demo"):
        raise ValueError("The export's source must be 'live' or 'demo'.")
    if session.get("track_id") is not None:
        _num(session["track_id"], integer=True)
    if session.get("session_type") is not None and not isinstance(session["session_type"], str):
        raise ValueError("session_type must be text.")
    if not 1 <= len(laps_in) <= MAX_IMPORT_LAPS:
        raise ValueError(f"The export must contain between 1 and {MAX_IMPORT_LAPS} laps.")
    laps, seen = [], set()
    try:
        for d in laps_in:
            if not isinstance(d, dict):
                raise ValueError("each lap must be an object")
            n = _num(d.get("lap_number"), integer=True, optional=False)
            t = _num(d.get("lap_time_ms"), integer=True, optional=False)
            if n in seen or n < 0 or t <= 0:
                raise ValueError(f"lap {n} has a duplicate number or a non-positive time")
            seen.add(n)
            flags = {k: bool(_num(d.get(k), integer=True, optional=False))
                     for k in ("valid", "samples_complete", "pit_lap", "out_lap")}
            laps.append(CompletedLap(n, t, *(_num(d.get(k), integer=True) for k in ("sector1_ms", "sector2_ms",
                                                                                    "sector3_ms")),
                                     **flags, max_speed_kph=_num(d.get("max_speed_kph")),
                                     fuel_used_kg=_num(d.get("fuel_used_kg")), tyre_wear_pct=_num(d.get("tyre_wear_pct"))))
    except ValueError as exc:
        raise ValueError(f"A lap in the export is invalid: {exc}.") from None
    columns, rows = samples.get("columns"), samples.get("rows")
    base = ["lap_number", *BASE_SAMPLE_FIELDS]
    if columns not in (base, ["lap_number", *SAMPLE_FIELDS]) or not isinstance(rows, list):
        raise ValueError("The export's sample columns don't match a PurpleSector export.")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"The export has more than {MAX_IMPORT_ROWS:,} samples.")
    try:
        for r in rows:
            if not isinstance(r, list) or len(r) != len(columns) or int(_num(r[0], integer=True, optional=False)) not in seen:
                raise ValueError("a sample row has the wrong length or belongs to no lap")
            for v in r[1:len(base)]:
                _num(v, optional=False)
            for i in range(len(base), len(r)):  # positions are optional; older files may hold NaN
                r[i] = None if isinstance(r[i], float) and math.isnan(r[i]) else _num(r[i])
    except ValueError as exc:
        raise ValueError(f"The export's samples are invalid: {exc}.") from None
    return session, laps, columns, rows
