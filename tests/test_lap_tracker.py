from analysis.lap_tracker import LapEventKind, LapTracker
from telemetry.packet_models import CarTelemetryData, LapData


def lap(num, cur_ms, *, last=0, s1=0, s2=0, dist=0.0, invalid=0, pit=0, status=1):
    return LapData(last_lap_time_ms=last, current_lap_time_ms=cur_ms, sector1_time_ms=s1, sector2_time_ms=s2,
                   lap_distance=dist, current_lap_num=num, current_lap_invalid=invalid, pit_status=pit,
                   driver_status=status)


def drive(tr, num, start_ms, end_ms, *, t0=0.0, length=3000.0, s1=None, s2=None, invalid_from=None, pit=0):
    """Feed lap data + telemetry at 60 Hz from start_ms to end_ms of lap `num`."""
    events, t = [], t0
    total = end_ms
    for ms in range(start_ms, end_ms, 16):
        a = s1 if s1 and ms >= s1 else 0
        b = s2 if s2 and ms >= s1 + s2 else 0
        inv = int(invalid_from is not None and ms >= invalid_from)
        events += tr.on_lap_data(lap(num, ms, s1=a, s2=b, dist=length * ms / total, invalid=inv, pit=pit))
        tr.on_telemetry(t, CarTelemetryData(speed=200, throttle=1.0))
        t += 0.016
    return events, t


def test_lap_boundary_sectors_and_game_time():
    tr = LapTracker()
    ev, t = drive(tr, 1, 0, 90000, s1=30000, s2=31000)
    ev += tr.on_lap_data(lap(2, 20, last=90012))
    done = [e for e in ev if e.kind == LapEventKind.LAP_COMPLETED]
    assert len(done) == 1
    lp = done[0].lap
    assert lp.lap_number == 1 and lp.lap_time_ms == 90012  # game value, not our sample clock
    assert lp.sectors == (30000, 31000, 29012)
    assert lp.valid and lp.samples_complete and lp.timed
    assert tr.best_lap() is lp


def test_invalid_lap_is_not_best():
    tr = LapTracker()
    ev, t = drive(tr, 1, 0, 90000, invalid_from=40000)
    assert any(e.kind == LapEventKind.LAP_INVALIDATED for e in ev)
    tr.on_lap_data(lap(2, 20, last=89000))
    assert not tr.laps[0].valid and tr.best_lap() is None


def test_started_mid_lap_marks_samples_incomplete_but_keeps_game_time():
    tr = LapTracker()
    drive(tr, 3, 50000, 90000)
    tr.on_lap_data(lap(4, 10, last=90000))
    assert tr.laps[0].lap_time_ms == 90000 and not tr.laps[0].samples_complete


def test_telemetry_gap_marks_incomplete():
    tr = LapTracker()
    drive(tr, 1, 0, 40000)
    tr.on_telemetry(100.0, CarTelemetryData())  # 1+ s jump in game time
    tr.on_lap_data(lap(2, 10, last=90000))
    assert not tr.laps[0].samples_complete


def test_flashback_within_lap_trims_samples():
    tr = LapTracker()
    ev, t = drive(tr, 1, 0, 50000)
    n_before = len(tr._cur.samples)
    ev = tr.on_lap_data(lap(1, 30000, dist=1000))
    assert any(e.kind == LapEventKind.FLASHBACK for e in ev)
    assert all(s[1] <= 30000 for s in tr._cur.samples) and len(tr._cur.samples) < n_before


def test_flashback_across_line_discards_lap():
    tr = LapTracker()
    drive(tr, 1, 0, 90000)
    tr.on_lap_data(lap(2, 100, last=90000))
    ev = tr.on_lap_data(lap(1, 89000))
    assert any(e.kind == LapEventKind.FLASHBACK for e in ev)
    assert tr.current_lap_number == 1


def test_pit_lap_flagged_and_events():
    tr = LapTracker()
    tr.on_lap_data(lap(1, 0))
    ev = tr.on_lap_data(lap(1, 100, pit=1))
    ev += tr.on_lap_data(lap(1, 200, pit=0))
    kinds = [e.kind for e in ev]
    assert LapEventKind.PIT_ENTRY in kinds and LapEventKind.PIT_EXIT in kinds
    tr.on_lap_data(lap(2, 10, last=100000))
    assert tr.laps[0].pit_lap and not tr.laps[0].timed


def test_personal_best_and_best_sectors():
    tr = LapTracker()
    t = 0.0
    times = [(91000, (30000, 31000)), (90000, (30500, 30000)), (92000, (29800, 31000))]
    for i, (lt, (a, b)) in enumerate(times, start=1):
        _, t = drive(tr, i, 0, lt, t0=t, s1=a, s2=b)
        tr.on_lap_data(lap(i + 1, 10, last=lt))
    assert tr.best_lap().lap_number == 2
    assert tr.best_sectors() == (29800, 30000, min(91000 - 61000, 90000 - 60500, 92000 - 60800))


def test_memory_bounded_sample_retention():
    tr = LapTracker(keep_recent_samples=2)
    t = 0.0
    for i in range(1, 6):
        _, t = drive(tr, i, 0, 20000, t0=t)
        tr.on_lap_data(lap(i + 1, 10, last=20000 + i))  # lap 1 fastest
    kept = [bool(lp.samples) for lp in tr.laps]
    assert kept == [True, False, False, True, True]  # best + 2 most recent
