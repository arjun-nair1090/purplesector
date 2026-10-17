"""Live corner calls: after each detected corner, what this lap lost there against the reference."""
import numpy as np
import pytest

from analysis.corner_feedback import CornerCoach, corner_windows
from analysis.lap_comparison import samples_frame
from analysis.track_map import Corner


def make_lap(dips, length=3000.0, hz=60, brake_shift_m=0.0):
    """Samples of one lap: 250 km/h with a gaussian speed dip per (apex m, km/h lost, half-width m)."""
    def speed_at(d):
        return 250.0 - sum(loss * np.exp(-((d - apex) / width) ** 2) for apex, loss, width in dips)
    rows, d, t = [], 0.0, 0.0
    while d < length:
        v = speed_at(d)
        nxt = speed_at(d + 5)
        braking = nxt < v - 0.05 and any(apex - 140 + brake_shift_m <= d <= apex for apex, _, _ in dips)
        rows.append((t, int(t * 1000), d, v, 0.0 if braking else 100.0, 80.0 if braking else 0.0, 0.0, 6, 10000))
        d += v / 3.6 / hz
        t += 1 / hz
    return rows


CORNERS = [Corner(1, 800.0, 160.0), Corner(2, 1700.0, 120.0)]
REF = make_lap([(800, 90, 60), (1700, 130, 60)])


def feed(coach, lap_number, rows):
    out = []
    for k in range(1, len(rows) + 1, 6):
        r = coach.on_progress(lap_number, rows[:k])
        if r is not None:
            out.append(r)
    return out


def test_windows_do_not_overlap_and_cover_braking_to_exit():
    w = corner_windows([Corner(1, 800.0, 100.0), Corner(2, 950.0, 90.0), Corner(3, 2000.0, 80.0)])
    assert [(c.number, s, e) for c, s, e in w] == [(1, 600.0, 875.0), (2, 875.0, 1050.0), (3, 1800.0, 2100.0)]


def test_a_slower_corner_is_called_once_with_what_was_different():
    coach = CornerCoach(threshold_s=0.1)
    coach.set_reference(CORNERS, samples_frame(REF), "Session best")
    lap = make_lap([(800, 90, 60), (1700, 150, 70)])  # corner 2: 20 km/h slower and longer
    results = feed(coach, 5, lap)
    assert [r.corner.number for r in results] == [2]
    r = results[0]
    assert r.segment.time_delta_s > 0.1 and r.segment.min_speed_lap < r.segment.min_speed_ref - 15
    e = r.event()
    assert e.key == "corner:2" and e.message.startswith("Corner 2: lost") and "vs session best" in e.message
    assert e.speech.startswith("Corner 2, lost") and "Minimum speed 20 lower." in e.speech
    assert "observations" in e.evidence
    assert coach.on_progress(5, lap) is None  # once per lap
    assert len(feed(coach, 6, lap)) == 1    # and again next lap


def test_matching_the_reference_says_nothing_and_no_reference_says_nothing():
    coach = CornerCoach(threshold_s=0.1)
    assert feed(coach, 1, REF) == []
    coach.set_reference(CORNERS, samples_frame(REF), "Session best")
    assert feed(coach, 1, REF) == []


def test_braking_earlier_is_reported():
    coach = CornerCoach(threshold_s=0.0)
    coach.set_reference(CORNERS, samples_frame(REF), "PB (recorded)")
    early = make_lap([(800, 90, 60), (1700, 130, 90)], brake_shift_m=-60)
    (r, *_) = [x for x in feed(coach, 3, early) if x.corner.number == 2]
    assert r.segment.brake_onset_diff_m is not None and r.segment.brake_onset_diff_m < -10
    assert "Braked" in r.event().speech and "earlier" in r.event().speech


def test_a_lap_joined_mid_way_skips_corners_already_passed():
    coach = CornerCoach(threshold_s=0.0)
    coach.set_reference(CORNERS, samples_frame(REF), "Session best")
    slow = make_lap([(800, 120, 80), (1700, 160, 80)])
    late = [r for r in slow if r[2] >= 1000]  # telemetry started after corner 1
    assert [r.corner.number for r in feed(coach, 2, late)] == [2]


def test_a_flashback_within_the_lap_reopens_the_corners_ahead():
    coach = CornerCoach(threshold_s=0.0)
    coach.set_reference(CORNERS, samples_frame(REF), "Session best")
    slow = make_lap([(800, 120, 80), (1700, 90, 60)])
    assert [r.corner.number for r in feed(coach, 4, slow[: next(i for i, r in enumerate(slow) if r[2] > 1000)])] == [1]
    rewound = [r for r in slow if r[2] < 500]
    assert coach.on_progress(4, rewound) is None
    assert [r.corner.number for r in feed(coach, 4, slow)] == [1]


def test_too_many_losses_are_capped_per_lap():
    corners = [Corner(i, 300.0 * i, 100.0) for i in range(1, 9)]
    ref = make_lap([(300.0 * i, 80, 40) for i in range(1, 9)])
    coach = CornerCoach(threshold_s=0.0, max_calls_per_lap=3)
    coach.set_reference(corners, samples_frame(ref), "Session best")
    slow = make_lap([(300.0 * i, 110, 50) for i in range(1, 9)])
    assert len(feed(coach, 1, slow)) == 3


def test_settings_switch_corner_calls():
    from config.settings import Settings
    s = Settings()
    assert s.corner_calls is True and s.corner_loss_threshold_s == pytest.approx(0.15)
    assert "corner_loss_threshold_s" in Settings(corner_loss_threshold_s=0).validate()


def test_demo_laps_get_corner_calls_and_the_switch_turns_them_off(tmp_path, monkeypatch):
    from conftest import FakeEngine

    import service as svc
    from config.settings import Settings
    from demo.simulator import DemoSimulator
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    for enabled in (True, False):
        app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path), corner_calls=enabled),
                             engine_factory=FakeEngine)
        sim = DemoSimulator("standard")
        while sim.lap < 4:
            for p in sim.step():
                app.ingest(p, "demo")
        calls = [e for e in app.coach_events() if e.key.startswith("corner:")]
        assert bool(calls) is enabled
        assert all(e.message.startswith("Corner ") and "vs session best" in e.message for e in calls)
        app.shutdown()


def test_reset_forgets_the_last_sessions_lap():
    coach = CornerCoach(threshold_s=0.0, max_calls_per_lap=1)
    coach.set_reference(CORNERS, samples_frame(REF), "Session best")
    slow = make_lap([(800, 120, 80), (1700, 160, 80)])
    assert len(feed(coach, 2, slow)) == 1        # budget spent on lap 2
    coach.reset()
    coach.set_reference(CORNERS, samples_frame(REF), "Session best")
    assert len(feed(coach, 2, slow)) == 1        # a new session's lap 2 starts afresh


def test_no_corner_calls_on_pit_or_out_laps_or_behind_a_safety_car(tmp_path, monkeypatch):
    from conftest import FakeEngine

    import service as svc
    from analysis.lap_tracker import LapTracker
    from config.settings import Settings
    from demo.simulator import DemoSimulator
    from telemetry.state import TelemetryState
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    for patch_off_pace in (True, False):
        with monkeypatch.context() as m:
            if patch_off_pace:
                m.setattr(LapTracker, "current_lap_off_pace", property(lambda self: True))
            else:  # every lap behind a safety car
                real = TelemetryState.snapshot
                m.setattr(TelemetryState, "snapshot", lambda self, real=real: setattr_sc(real(self)))
            app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path)), engine_factory=FakeEngine)
            sim = DemoSimulator("standard")
            while sim.lap < 4:
                for p in sim.step():
                    app.ingest(p, "demo")
            assert not [e for e in app.coach_events() if e.key.startswith("corner:")]
            app.shutdown()


def setattr_sc(snap):
    snap.safety_car_status = 1
    return snap
