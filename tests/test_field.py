"""Field timing: best laps and sectors of every car from the game's session history."""
import pytest

from analysis.field import FieldTiming
from analysis.gaps import GapTracker
from telemetry.packet_models import LapData, LapHistoryData, SessionHistory, SessionHistoryHeader


def hist(car, laps, best=0, s1=0, s2=0, s3=0):
    return SessionHistory(SessionHistoryHeader(car, len(laps), 1, best, s1, s2, s3),
                          tuple(LapHistoryData(*lap) for lap in laps))


def cars(*positions, deltas=None):
    out = [LapData(car_position=p, result_status=2 if p else 0, total_distance=1000.0 - 10 * p,
                   delta_to_car_in_front_ms=(deltas or {}).get(i)) for i, p in enumerate(positions)]
    return tuple(out + [LapData()] * (22 - len(out)))


def field():
    f = FieldTiming()
    f.on_history(hist(0, [(91_000, 30_000, 31_000, 30_000, 15), (90_400, 29_900, 30_600, 29_900, 15)], 2, 2, 2, 1))
    f.on_history(hist(1, [(89_900, 29_500, 30_700, 29_700, 15)], 1, 1, 1, 1))
    f.on_history(hist(2, [(95_000, 31_000, 33_000, 31_000, 14)], 0, 1, 1, 1))  # lap invalid: no best lap
    return f


def test_best_lap_and_sectors_come_from_the_games_lap_numbers():
    f = field()
    assert f.best(0).lap_ms == 90_400 and f.best(0).sectors == (29_900, 30_600, 30_000)
    assert f.best(2).lap_ms is None and f.best(2).sectors == (31_000, 33_000, 31_000)
    assert f.best(5) is None


def test_field_best_and_your_deltas():
    f = field()
    rows = f.versus(player=0, names={0: "YOU", 1: "LECLERC", 2: "SAINZ"})
    assert [(r.label, r.field_ms, r.holder, r.yours_ms) for r in rows] == [
        ("S1", 29_500, "LECLERC", 29_900), ("S2", 30_600, "You", 30_600), ("S3", 29_700, "LECLERC", 30_000),
        ("Lap", 89_900, "LECLERC", 90_400), ("Ideal", 89_800, None, 90_500)]
    assert rows[0].delta_s == pytest.approx(0.4) and rows[1].delta_s == 0.0


def test_tower_in_qualifying_gaps_to_the_fastest_lap():
    f = field()
    rows = f.tower(cars(2, 1, 3), names={0: "YOU", 1: "LECLERC"}, player=0, race=False)
    assert [(r.position, r.name, r.is_player) for r in rows] == [(1, "LECLERC", False), (2, "YOU", True),
                                                                 (3, "Car 3", False)]
    assert rows[0].gap_s is None and rows[1].gap_s == pytest.approx(0.5) and rows[2].gap_s is None
    assert rows[0].purple_lap and not rows[1].purple_lap
    assert rows[1].purple_sectors == (False, True, False)


def test_tower_in_a_race_prefers_the_games_interval_then_measured():
    f = field()
    rows = f.tower(cars(2, 1, 3, deltas={0: 1_234}), names={}, player=0, race=True)
    assert rows[1].gap_s == pytest.approx(1.234) and rows[1].gap_source == "game"
    assert rows[0].gap_s is None
    gt = GapTracker()
    for k in range(240):  # car 1 leads car 2 by 2 s at 50 m/s
        t = k / 60
        lap = [LapData(car_position=2, result_status=0), LapData(car_position=1, total_distance=500 + 50 * t,
                                                                  result_status=2),
               LapData(car_position=3, total_distance=400 + 50 * t, result_status=2)]
        gt.on_lap_data(t, tuple(lap + [LapData()] * 19))
    rows = f.tower(gt._cars, names={}, player=0, race=True, gaps=gt)
    assert [r.position for r in rows] == [1, 3] and rows[1].gap_s == pytest.approx(2.0, abs=0.03)
    assert rows[1].gap_source == "measured"


def test_reset_forgets_the_field():
    f = field()
    f.reset()
    assert f.best(0) is None and f.versus(0, {}) == []


def test_finished_cars_stay_in_the_tower():
    f = field()
    lap = list(cars(2, 1, 3))
    lap[1] = LapData(car_position=1, result_status=3, total_distance=990.0)  # took the flag
    rows = f.tower(tuple(lap), names={}, player=0, race=True)
    assert [r.position for r in rows] == [1, 2, 3]
