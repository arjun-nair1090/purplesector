import json

import pandas as pd
from conftest import wait_until

from analysis.lap_tracker import CompletedLap
from storage.database import connect, init_db
from storage.session_repository import SessionRepository
from storage.telemetry_writer import TelemetryWriter


def lap(n, ms=90000, valid=True, samples=3):
    rows = [(0.1 * i, 100 * i, 10.0 * i, 200.0, 100.0, 0.0, 0.0, 7, 11000) for i in range(samples)]
    return CompletedLap(n, ms, 30000, 30000, ms - 60000, valid=valid, samples_complete=True, pit_lap=False,
                        out_lap=False, samples=rows, max_speed_kph=200.0)


def test_schema_created_and_versioned(tmp_path):
    db = tmp_path / "x.db"
    assert init_db(db) == 0
    with connect(db) as c:
        assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sessions", "laps", "samples", "meta", "events"} <= tables


def test_v1_database_upgrades_in_place(tmp_path):
    """A v1 database (before positions, fuel/tyre use and imports) keeps its laps and gains the new columns."""
    from storage.database import SCHEMA
    db = tmp_path / "old.db"
    with connect(db) as c:
        c.executescript(SCHEMA)
        c.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        c.execute("INSERT INTO sessions (session_uid, source, track_name, started_at, status) "
                  "VALUES ('1', 'live', 'Spa', '2026-01-01T10:00:00', 'complete')")
        c.execute("INSERT INTO laps (session_id, lap_number, lap_time_ms, valid, samples_complete, pit_lap, out_lap) "
                  "VALUES (1, 1, 106000, 1, 1, 0, 0)")
    init_db(db)
    init_db(db)  # idempotent
    repo = SessionRepository(db)
    s = repo.list_sessions().iloc[0]
    assert s.imported == 0 and s.best_lap_ms == 106000
    assert repo.laps(1).fuel_used_kg.isna().all()
    sid = repo.create_session(2, "live", "Spa")
    repo.save_lap(sid, lap(1), [(0.0, 0, 0.0, 200.0, 100.0, 0.0, 0.0, 7, 11000, 1.0, 2.0, 3.0)])
    assert repo.lap_samples(int(repo.laps(sid).id[0])).pos_z.tolist() == [3.0]


def test_save_load_and_recovery(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    sid = repo.create_session(123, "live")
    repo.update_session(sid, track_name="Monza", track_id=11, session_type="Race")
    repo.save_lap(sid, lap(1), lap(1).samples)
    repo.save_lap(sid, lap(2, 89000), lap(2).samples)
    repo.save_lap(sid, lap(2, 88000), lap(2, samples=5).samples)  # re-run lap 2 replaces, never duplicates
    laps = repo.laps(sid)
    assert laps.lap_number.tolist() == [1, 2] and laps.lap_time_ms.tolist() == [90000, 88000]
    lap2 = int(laps.id.iloc[1])
    assert len(repo.lap_samples(lap2)) == 5
    s = repo.list_sessions().iloc[0]
    assert s.best_lap_ms == 88000 and s.status == "recording" and s.track_name == "Monza"
    empty = repo.create_session(124, "live")  # crashed before its first lap
    # app "crashes": next startup marks them interrupted and drops the empty one
    assert init_db(db) == 2
    assert repo.get_session(sid)["status"] == "interrupted" and repo.get_session(empty) is None
    assert repo.create_session(123, "live") != sid


def test_reference_laps_exclude_demo_and_invalid(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    live, demo = repo.create_session(1, "live", "Spa"), repo.create_session(2, "demo", "Spa")
    repo.save_lap(live, lap(1, 100000), [])
    repo.save_lap(live, lap(2, 99000, valid=False), [])
    repo.save_lap(demo, lap(1, 50000), [])
    refs = repo.reference_laps("Spa")
    assert refs.lap_time_ms.tolist() == [100000]
    assert repo.reference_laps("Spa", include_demo=True).lap_time_ms.tolist() == [50000, 100000]


def test_export_and_delete(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    sid = repo.create_session(5, "demo", "Demo Ring (synthetic)")
    repo.save_lap(sid, lap(1), lap(1).samples)
    csv_path = repo.export_csv(sid, tmp_path / "exports")
    df = pd.read_csv(csv_path)
    assert len(df) == 3 and "lap_distance" in df and "demo" in csv_path.name
    doc = json.loads(repo.export_json(sid, tmp_path / "exports").read_text(encoding="utf-8"))
    assert doc["session"]["source"] == "demo" and doc["laps"][0]["lap_time_ms"] == 90000
    assert len(doc["samples"]["rows"]) == 3
    assert repo.delete_session(sid)
    with connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0  # cascade
        assert c.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 0


def test_writer_thread_persists_and_flushes(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    w = TelemetryWriter(repo)
    w.start()
    w.start_session("k", 77, "live")
    lp = lap(1)
    w.save_lap("k", lp)
    lp.samples = []  # tracker trimming after enqueue must not affect what is written
    w.end_session("k")
    w.stop()
    assert w.laps_written == 1 and w.last_error is None
    s = repo.list_sessions().iloc[0]
    assert s.status == "complete" and s.laps == 1
    assert len(repo.lap_samples(int(repo.laps(int(s.id)).id[0]))) == 3


def test_writer_survives_db_errors(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    w = TelemetryWriter(SessionRepository(db))
    w.start()
    w.save_lap("unknown-recording", lap(1))
    assert wait_until(lambda: w.last_error is not None)
    w.start_session("k", 1, "live")
    w.save_lap("k", lap(1))
    w.end_session("k")
    w.start_session("empty", 2, "live")  # ended before any lap completed: not kept
    w.end_session("empty")
    w.stop()
    sessions = SessionRepository(db).list_sessions()
    assert len(sessions) == 1 and sessions.iloc[0].status == "complete"


def test_v2_database_gains_setups_tyres_and_events(tmp_path):
    from storage.database import MIGRATIONS, SCHEMA
    db = tmp_path / "v2.db"
    with connect(db) as c:
        c.executescript(SCHEMA)
        for stmt in filter(str.strip, MIGRATIONS[2].split(";")):
            c.execute(stmt)
        c.execute("INSERT INTO meta VALUES ('schema_version', '2')")
        c.execute("INSERT INTO sessions (session_uid, source, track_name, started_at, status) "
                  "VALUES ('1', 'live', 'Spa', '2026-01-01T10:00:00', 'complete')")
        c.execute("INSERT INTO laps (session_id, lap_number, lap_time_ms, valid, samples_complete, pit_lap, out_lap) "
                  "VALUES (1, 1, 106000, 1, 1, 0, 0)")
    init_db(db)
    repo = SessionRepository(db)
    old = repo.laps(1).iloc[0]
    assert old.lap_time_ms == 106000 and pd.isna(old.tyre_age_laps) and repo.setup(int(old.id)) is None
    assert repo.events(1).empty


def test_lap_setup_tyres_and_events_round_trip(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    sid = repo.create_session(1, "live", "Monza")
    lp = lap(3)
    lp.setup = {"front_wing": 3, "rear_wing": 2, "brake_bias": 55, "front_left_tyre_pressure": 22.5}
    lp.tyre_age_laps, lp.tyre_compound = 7, "Soft"
    repo.save_lap(sid, lp, lp.samples)
    row = repo.laps(sid).iloc[0]
    assert row.tyre_age_laps == 7 and row.tyre_compound == "Soft"
    assert repo.setup(int(row.id)) == lp.setup
    repo.add_event(sid, 812.5, 3, "PENA", "+5 s penalty for VERSTAPPEN: corner cutting, gained time")
    ev = repo.events(sid)
    assert ev.code.tolist() == ["PENA"] and ev.lap_number.tolist() == [3] and "VERSTAPPEN" in ev.message[0]


def test_writer_records_events(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    w = TelemetryWriter(repo)
    w.start()
    w.start_session("k", 1, "live", "Spa")
    w.save_lap("k", lap(1))
    w.add_event("k", 10.0, 1, "SCAR", "Safety car deployed")
    w.end_session("k")
    w.stop()
    sid = int(repo.list_sessions().id[0])
    assert repo.events(sid).message.tolist() == ["Safety car deployed"]


def test_export_import_keeps_tyre_life(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    sid = repo.create_session(1, "live", "Monza")
    lp = lap(1)
    lp.tyre_age_laps, lp.tyre_compound = 4, "Medium"
    repo.save_lap(sid, lp, lp.samples)
    doc = json.loads(repo.export_json(sid, tmp_path).read_text(encoding="utf-8"))
    new = repo.import_session(doc, "Friend")
    row = repo.laps(new).iloc[0]
    assert row.tyre_age_laps == 4 and row.tyre_compound == "Medium"
    doc["laps"][0]["tyre_compound"] = "x" * 500
    try:
        repo.import_session(doc, "Friend")
        raise AssertionError("an oversized compound name must be refused")
    except ValueError as exc:
        assert "invalid" in str(exc)
