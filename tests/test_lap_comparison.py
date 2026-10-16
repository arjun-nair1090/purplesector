import numpy as np
import pandas as pd
import pytest

from analysis.lap_comparison import align, compare_laps


def make_lap(length=3000.0, speed_fn=lambda d: 60.0, hz=60, brake_at=None):
    """Integrate a speed profile (m/s as a function of distance) into lap-tracker style samples."""
    rows, d, t = [], 0.0, 0.0
    while d < length:
        v = speed_fn(d)
        brake = 100.0 if brake_at and brake_at[0] <= d < brake_at[1] else 0.0
        rows.append((t, int(t * 1000), d, v * 3.6, 0.0 if brake else 100.0, brake, 0.0, 6, 10000))
        t += 1 / hz
        d += v / hz
    return pd.DataFrame(rows, columns=["session_time", "lap_ms", "lap_distance", "speed", "throttle", "brake",
                                       "steer", "gear", "rpm"]), int(t * 1000)


def test_identical_laps_have_zero_delta():
    df, ms = make_lap()
    cmp = compare_laps(df, df.copy(), lap_time_ms=ms, ref_time_ms=ms)
    assert cmp.conclusive
    assert np.nanmax(np.abs(cmp.frame["delta_s"])) < 1e-9
    assert all(abs(s.time_delta_s) < 1e-9 for s in cmp.segments)


def test_slow_section_is_located():
    ref, ref_ms = make_lap()
    slow, slow_ms = make_lap(speed_fn=lambda d: 40.0 if 1200 <= d < 1400 else 60.0)
    cmp = compare_laps(slow, ref, lap_time_ms=slow_ms, ref_time_ms=ref_ms)
    assert cmp.conclusive
    worst = cmp.segments[0]
    assert (worst.start_m, worst.end_m) == (1200, 1400)  # segments sit on round 200 m boundaries
    # 200 m at 40 vs 60 m/s = 5.0 - 3.333 s
    assert worst.time_delta_s == pytest.approx(5 - 200 / 60, abs=0.05)
    assert worst.min_speed_lap < worst.min_speed_ref
    assert cmp.frame["delta_s"].iloc[-1] == pytest.approx(cmp.lap_time_delta_s, abs=0.05)


def test_brake_onset_difference_reported():
    ref, ms = make_lap(brake_at=(1500, 1550))
    lap, _ = make_lap(brake_at=(1470, 1550))
    cmp = compare_laps(lap, ref, lap_time_ms=ms, ref_time_ms=ms)
    seg = next(s for s in cmp.segments if s.start_m <= 1480 < s.end_m)
    assert seg.brake_onset_diff_m == pytest.approx(-30, abs=6)
    assert any("earlier" in o for o in seg.observations())


def test_gap_is_not_interpolated():
    df, _ = make_lap()
    gapped = df[(df.lap_distance < 1000) | (df.lap_distance > 1100)]
    grid = np.arange(0, 2900, 5.0)
    a = align(gapped.sort_values("lap_ms"), grid)
    inside = (grid > 1010) & (grid < 1090)
    assert np.isnan(a["speed"][inside]).all()
    assert not np.isnan(a["speed"][grid < 900]).any()


def test_large_gap_makes_comparison_inconclusive():
    df, ms = make_lap()
    gapped = df[(df.lap_distance < 1000) | (df.lap_distance > 1400)]
    cmp = compare_laps(gapped, df, lap_time_ms=ms, ref_time_ms=ms)
    assert not cmp.conclusive and "gaps" in cmp.reasons[0]


def test_partial_coverage_is_inconclusive():
    df, ms = make_lap()
    cmp = compare_laps(df[df.lap_distance < 2000], df, lap_time_ms=ms, ref_time_ms=ms)
    assert not cmp.conclusive and cmp.segments == []


def test_alignment_disagreeing_with_game_timing_withholds_segments():
    df, ms = make_lap()
    cmp = compare_laps(df, df.copy(), lap_time_ms=ms + 2000, ref_time_ms=ms)
    assert not cmp.conclusive and "disagrees" in cmp.reasons[0]


def test_sector_deltas_from_game_timing():
    df, ms = make_lap()
    cmp = compare_laps(df, df, lap_time_ms=90500, ref_time_ms=90000, lap_sectors=(30100, 30200, 30200),
                       ref_sectors=(30000, 30000, 30000))
    assert cmp.lap_time_delta_s == pytest.approx(0.5)
    assert cmp.sector_deltas_s == pytest.approx((0.1, 0.2, 0.2))
    assert compare_laps(df, df, lap_sectors=(1, None, 1), ref_sectors=(1, 1, 1)).sector_deltas_s[1] is None


def test_too_few_samples():
    df, ms = make_lap()
    assert not compare_laps(df.head(20), df).conclusive
