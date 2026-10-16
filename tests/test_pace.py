"""Live delta, theoretical best / consistency, and all-time personal best."""
import pytest
from conftest import FakeEngine, wait_until

from analysis.coach import Confidence, Priority, rule_alltime_best
from analysis.lap_comparison import delta_at, reference_curve
from analysis.lap_tracker import CompletedLap, LapTracker
from analysis.statistics import consistency_s, theoretical_best_ms
from config.settings import Settings
from demo.simulator import DemoSimulator
from storage.database import init_db
from storage.session_repository import SessionRepository


def samples(length=3000.0, speed=60.0, hz=60):
    """Lap-tracker tuples at constant speed: lap_ms = distance / speed."""
    out, t, d = [], 0.0, 0.0
    while d < length:
        out.append((t, int(t * 1000), d, speed * 3.6, 100.0, 0.0, 0.0, 6, 10000))
        t += 1 / hz
        d += speed / hz
    return out


def mk(n, ms, sectors=(30000, 30000, 30000), *, valid=True, pit=False):
    return CompletedLap(n, ms, *sectors, valid=valid, samples_complete=True, pit_lap=pit, out_lap=False)


# ---------------- live delta ----------------

def test_delta_against_reference_at_same_distance():
    curve = reference_curve(samples())
    # at 1200 m the reference took 20.0 s; we're at 21.0 s -> 1.0 s down
    assert delta_at(curve, 1200.0, 21000) == pytest.approx(1.0, abs=0.02)
    assert delta_at(curve, 1200.0, 19500) == pytest.approx(-0.5, abs=0.02)


def test_delta_unavailable_outside_reference_or_before_line():
    curve = reference_curve(samples(length=2000))
    assert delta_at(curve, -12.0, 100) is None        # before crossing the line
    assert delta_at(curve, 2500.0, 40000) is None     # beyond what the reference covers
    assert delta_at(None, 500.0, 9000) is None


def test_reference_needs_enough_clean_samples():
    assert reference_curve(samples()[:20]) is None
    assert reference_curve([]) is None


# ---------------- theoretical best / consistency ----------------

def test_theoretical_best_sums_best_valid_sectors():
    laps = [mk(1, 91000, (30000, 31000, 30000)), mk(2, 90500, (30500, 30000, 30000)),
            mk(3, 89000, (29000, 30000, 30000), valid=False),  # invalid laps never count
            mk(4, 92000, (30200, 31000, 29800), pit=True)]     # nor pit laps
    assert theoretical_best_ms(laps) == 30000 + 30000 + 30000


def test_theoretical_best_needs_all_three_sectors():
    assert theoretical_best_ms([mk(1, 90000, (30000, None, None))]) is None
    assert theoretical_best_ms([]) is None


def test_consistency_is_sample_std_dev_of_valid_laps():
    laps = [mk(1, 90000), mk(2, 91000), mk(3, 92000), mk(4, 80000, valid=False)]
    assert consistency_s(laps) == pytest.approx(1.0)
    assert consistency_s(laps[:2]) is None  # needs at least 3 valid laps


# ---------------- all-time personal best ----------------

def test_alltime_best_rule():
    assert rule_alltime_best(mk(5, 85900), None, "Spa") is None             # no recorded history
    assert rule_alltime_best(mk(5, 86100), 86000, "Spa") is None            # slower
    assert rule_alltime_best(mk(5, 85900, valid=False), 86000, "Spa") is None
    e = rule_alltime_best(mk(5, 85900), 86000, "Spa")
    assert e.priority == Priority.HIGH and e.confidence == Confidence.HIGH
    assert "Spa" in e.message and "0.100" in e.message


class _Tracker:
    def __init__(self, *laps):
        self.laps = list(laps)


def test_no_history_means_session_best_not_alltime():
    from analysis.coach import Coach, CoachConfig
    tr = _Tracker(mk(1, 90000), mk(2, 89000))
    keys = [e.key for e in Coach(CoachConfig(cooldown_s=0)).on_lap_completed(tr.laps[-1], tr, None, "Spa")]
    assert "pb:2" in keys and not any(k.startswith("alltime_pb") for k in keys)


def test_alltime_must_beat_history_and_this_session():
    from analysis.coach import Coach, CoachConfig
    # history 86000, this session already did 85950: 85960 beats history but is not a new best at all
    tr = _Tracker(mk(1, 85950), mk(2, 85960))
    keys = [e.key for e in Coach(CoachConfig(cooldown_s=0)).on_lap_completed(tr.laps[-1], tr, 86000, "Spa")]
    assert not any(k.startswith(("alltime_pb", "pb:")) for k in keys)
    tr = _Tracker(mk(1, 85950), mk(2, 85900))
    keys = [e.key for e in Coach(CoachConfig(cooldown_s=0)).on_lap_completed(tr.laps[-1], tr, 86000, "Spa")]
    assert "alltime_pb:2" in keys and "pb:2" not in keys


def test_session_reference_without_history_is_labelled_session_best(tmp_path, monkeypatch):
    import service as svc
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path)), engine_factory=FakeEngine)
    sim = DemoSimulator()
    while sim.lap < 2 or sim.lap_ms < 5000:
        for p in sim.step():
            app.ingest(p, "demo")
    view = app.laps_view()
    assert view["delta_ref"] == "Session best" and view["delta_s"] is not None
    assert view["alltime_best_ms"] is None  # nothing recorded before this session
    app.shutdown()


def _lap(n, ms, valid=True, complete=True):
    return CompletedLap(n, ms, 30000, 30000, ms - 60000, valid=valid, samples_complete=complete, pit_lap=False,
                        out_lap=False, samples=samples())


def test_repository_best_lap_per_track_and_source(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = SessionRepository(db)
    a = repo.create_session(1, "live", "Spa")
    repo.save_lap(a, _lap(1, 106000), [])
    repo.save_lap(a, _lap(2, 105000, valid=False), [])
    b = repo.create_session(2, "live", "Monza")
    repo.save_lap(b, _lap(1, 80000), [])
    d = repo.create_session(3, "demo", "Spa")
    repo.save_lap(d, _lap(1, 90000), [])
    assert repo.best_lap_ms("Spa", "live") == 106000
    assert repo.best_lap_ms("Spa", "demo") == 90000
    assert repo.best_lap_ms("Imola", "live") is None


def test_service_uses_recorded_best_as_reference(tmp_path, monkeypatch):
    """A recorded demo PB on the track is loaded at session start and drives the live delta."""
    import service as svc
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path)), engine_factory=FakeEngine)
    # record one full demo lap as history
    sim = DemoSimulator()
    prior = []
    while sim.lap < 2:
        prior.extend(sim.step())
    prior.extend(sim.step())  # the first frame of lap 2 carries lap 1's time
    tr = LapTracker()
    for p in prior:
        if p.packet_id.name == "LAP_DATA":
            for ev in tr.on_lap_data(p.data):
                if ev.lap:
                    sid = app.repo.create_session(sim.session_uid, "demo", svc.DEMO_TRACK_NAME)
                    app.repo.save_lap(sid, ev.lap, ev.lap.samples)
        elif p.packet_id.name == "CAR_TELEMETRY":
            tr.on_telemetry(p.header.session_time, p.data)
    recorded = app.repo.best_lap_ms(svc.DEMO_TRACK_NAME, "demo")
    assert recorded is not None

    # a fresh demo session on the same track
    sim2 = DemoSimulator(run_index=1)
    for _ in range(60 * 20):
        for p in sim2.step():
            app.ingest(p, "demo")
    assert wait_until(lambda: app.laps_view()["alltime_best_ms"] == recorded)
    for _ in range(60):
        for p in sim2.step():
            app.ingest(p, "demo")
    view = app.laps_view()
    assert view["delta_s"] is not None and abs(view["delta_s"]) < 5
    assert view["delta_ref"] == "PB (recorded)"
    app.shutdown()


# ---------------- dashboard rendering ----------------

def test_delta_badge_signs_and_states():
    from ui import components as c
    up = c.delta_badge(-0.214, "Session best")
    assert "−0.214" in up and "gain" in up and "Session best" in up
    down = c.delta_badge(0.5, "PB (recorded)")
    assert "+0.500" in down and "loss" in down
    none = c.delta_badge(None, None)
    assert "n/a" in none and "gain" not in none and "loss" not in none


def test_delta_badge_tie_is_neutral():
    from ui import components as c
    tie = c.delta_badge(0.0002, "PB (recorded)")
    assert "even" in tie and "gain" not in tie and "loss" not in tie and "0.000" in tie
