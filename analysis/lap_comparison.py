"""Distance-aligned lap comparison.

Alignment method: both laps are resampled onto a common lap-distance grid (default
5 m) by linear interpolation over strictly increasing lap distance. Grid points that
fall inside a sampling gap wider than MAX_GAP_M are left as NaN rather than
interpolated. The running time delta at distance d is t_lap(d) - t_ref(d).

Two kinds of result are kept apart:
- *Measured*: lap and sector deltas come straight from game timing; the running
  delta comes from the aligned samples.
- *Observed differences*: input/speed differences inside a segment. These are
  correlations that are worth reviewing, not proof of what caused a time difference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
import pandas as pd

from analysis.lap_tracker import SAMPLE_FIELDS

STEP_M = 5.0
MAX_GAP_M = 40.0
SEGMENT_M = 200.0
MIN_SAMPLES = 100
MIN_COVERAGE = 0.95
MAX_NAN_FRACTION = 0.05
MAX_ALIGNMENT_DRIFT_S = 0.3
CHANNELS = ("lap_ms", "speed", "throttle", "brake", "steer", "gear")


def samples_frame(samples) -> pd.DataFrame:
    return pd.DataFrame(list(samples), columns=list(SAMPLE_FIELDS))


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["lap_distance"] >= 0].sort_values("session_time" if "session_time" in df else "lap_ms")
    dist = d["lap_distance"].to_numpy(float)
    if len(dist) == 0:
        return d
    prev_max = np.maximum.accumulate(np.concatenate(([-np.inf], dist[:-1])))
    return d[dist > prev_max]  # strictly increasing distance


def reference_curve(samples) -> tuple[np.ndarray, np.ndarray] | None:
    """(lap distance m, lap time ms) of a reference lap, for the live delta. None if too sparse."""
    if not samples:
        return None
    df = _clean(samples_frame(samples))
    if len(df) < MIN_SAMPLES:
        return None
    return df["lap_distance"].to_numpy(float), df["lap_ms"].to_numpy(float)


def delta_at(curve, distance_m: float, lap_ms: int) -> float | None:
    """Seconds up (+) or down (-) on the reference at this lap distance; None outside its coverage."""
    if curve is None or distance_m < 0:
        return None
    dist, ms = curve
    if distance_m < dist[0] or distance_m > dist[-1]:
        return None
    return (lap_ms - float(np.interp(distance_m, dist, ms))) / 1000.0


def align(df: pd.DataFrame, grid: np.ndarray, max_gap_m: float = MAX_GAP_M) -> dict[str, np.ndarray]:
    dist = df["lap_distance"].to_numpy(float)
    out = {}
    for col in CHANNELS:
        out[col] = np.interp(grid, dist, df[col].to_numpy(float), left=np.nan, right=np.nan)
    idx = np.searchsorted(dist, grid).clip(1, len(dist) - 1)
    in_gap = (dist[idx] - dist[idx - 1]) > max_gap_m
    for col in CHANNELS:
        out[col][in_gap] = np.nan
    return out


@dataclass
class Segment:
    start_m: float
    end_m: float
    time_delta_s: float                    # measured from aligned samples; + = slower than reference
    min_speed_lap: float | None = None
    min_speed_ref: float | None = None
    brake_onset_diff_m: float | None = None    # + = braked later than reference
    full_throttle_diff_m: float | None = None  # + = reached full throttle later than reference

    def observations(self) -> list[str]:
        obs = []
        if self.min_speed_lap is not None and self.min_speed_ref is not None:
            d = self.min_speed_lap - self.min_speed_ref
            if abs(d) >= 3:
                obs.append(f"Minimum speed {abs(d):.0f} km/h {'lower' if d < 0 else 'higher'} than reference")
        if self.brake_onset_diff_m is not None and abs(self.brake_onset_diff_m) >= 10:
            obs.append(f"Braking started {abs(self.brake_onset_diff_m):.0f} m "
                       f"{'later' if self.brake_onset_diff_m > 0 else 'earlier'} than reference")
        if self.full_throttle_diff_m is not None and abs(self.full_throttle_diff_m) >= 10:
            obs.append(f"Full throttle {abs(self.full_throttle_diff_m):.0f} m "
                       f"{'later' if self.full_throttle_diff_m > 0 else 'earlier'} than reference")
        return obs


@dataclass
class LapComparison:
    conclusive: bool                       # running delta / segments are trustworthy
    reasons: list[str] = field(default_factory=list)
    lap_time_delta_s: float | None = None  # game timing
    sector_deltas_s: tuple = (None, None, None)  # game timing
    frame: pd.DataFrame | None = None      # distance-aligned channels for charts
    segments: list[Segment] = field(default_factory=list)  # sorted: most time lost first


def _first_at_or_above(dist, values, threshold):
    hits = np.nonzero(values >= threshold)[0]
    return float(dist[hits[0]]) if len(hits) else None


def _segment(grid, a, r, i0, i1) -> Segment | None:
    sl = slice(i0, i1 + 1)
    da, dr = a["lap_ms"][sl], r["lap_ms"][sl]
    if np.isnan(da[[0, -1]]).any() or np.isnan(dr[[0, -1]]).any():
        return None
    seg = Segment(float(grid[i0]), float(grid[i1]), ((da[-1] - da[0]) - (dr[-1] - dr[0])) / 1000.0)
    sa, sr = a["speed"][sl], r["speed"][sl]
    if not np.isnan(sa).all() and not np.isnan(sr).all():
        seg.min_speed_lap, seg.min_speed_ref = float(np.nanmin(sa)), float(np.nanmin(sr))
    g = grid[sl]
    ba = _first_at_or_above(g, np.nan_to_num(a["brake"][sl]), 20)
    br = _first_at_or_above(g, np.nan_to_num(r["brake"][sl]), 20)
    if ba is not None and br is not None:
        seg.brake_onset_diff_m = ba - br
    if seg.min_speed_lap is not None:
        ia, ir = int(np.nanargmin(sa)), int(np.nanargmin(sr))
        ta = _first_at_or_above(g[ia:], np.nan_to_num(a["throttle"][sl][ia:]), 90)
        tr = _first_at_or_above(g[ir:], np.nan_to_num(r["throttle"][sl][ir:]), 90)
        if ta is not None and tr is not None:
            seg.full_throttle_diff_m = ta - tr
    return seg


def compare_laps(lap_df: pd.DataFrame, ref_df: pd.DataFrame, *, lap_time_ms: int | None = None,
                 ref_time_ms: int | None = None, lap_sectors=(None, None, None), ref_sectors=(None, None, None),
                 step_m: float = STEP_M, segment_m: float = SEGMENT_M) -> LapComparison:
    res = LapComparison(conclusive=False)
    if lap_time_ms and ref_time_ms:
        res.lap_time_delta_s = (lap_time_ms - ref_time_ms) / 1000.0
    res.sector_deltas_s = tuple((a - b) / 1000.0 if a and b else None for a, b in zip(lap_sectors, ref_sectors, strict=True))

    a_df, r_df = _clean(lap_df), _clean(ref_df)
    if len(a_df) < MIN_SAMPLES or len(r_df) < MIN_SAMPLES:
        res.reasons.append("Not enough recorded samples on one of the laps for a distance-aligned comparison.")
        return res
    a_end, r_end = a_df["lap_distance"].max(), r_df["lap_distance"].max()
    start = max(a_df["lap_distance"].min(), r_df["lap_distance"].min())
    end = min(a_end, r_end)
    if end - start < MIN_COVERAGE * max(a_end, r_end):
        res.reasons.append("The two laps do not cover the same distance (incomplete recording or different track).")
        return res
    grid = np.arange(np.ceil(start / step_m) * step_m, end, step_m)
    a, r = align(a_df, grid), align(r_df, grid)
    delta = (a["lap_ms"] - r["lap_ms"]) / 1000.0
    nan_frac = float(np.isnan(delta).mean())
    res.frame = pd.DataFrame({
        "distance_m": grid, "delta_s": delta,
        **{f"{c}_lap": a[c] for c in CHANNELS if c != "lap_ms"},
        **{f"{c}_ref": r[c] for c in CHANNELS if c != "lap_ms"},
    })
    if nan_frac > MAX_NAN_FRACTION:
        res.reasons.append(f"{nan_frac:.0%} of the lap falls inside telemetry gaps; running delta is unreliable.")
        return res
    if res.lap_time_delta_s is not None:
        # The aligned delta at the end of the overlap should agree with game timing.
        valid = delta[~np.isnan(delta)]
        drift = abs(valid[-1] - res.lap_time_delta_s) if len(valid) else np.inf
        if drift > MAX_ALIGNMENT_DRIFT_S:
            res.reasons.append(f"Aligned delta disagrees with game lap timing by {drift:.2f} s; "
                               "segment analysis withheld.")
            return res
    # segment boundaries on round distances (0, 200, 400 m ...) plus the ends of the overlap
    marks = np.nonzero(np.isclose(np.mod(grid, segment_m), 0) | np.isclose(np.mod(grid, segment_m), segment_m))[0]
    bounds = sorted({0, len(grid) - 1, *marks.tolist()})
    for i0, i1 in pairwise(bounds):
        if grid[i1] - grid[i0] >= segment_m / 4:  # skip slivers at the very start/end
            seg = _segment(grid, a, r, i0, i1)
            if seg:
                res.segments.append(seg)
    res.segments.sort(key=lambda s: s.time_delta_s, reverse=True)
    res.conclusive = True
    return res
