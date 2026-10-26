"""Live corner calls: after each detected corner, the time this lap lost there against the
delta reference, and what was measurably different.

Each corner gets a window from its braking zone to its exit (200 m before the apex to
100 m after, never overlapping a neighbour's). When the car leaves a window, both laps
are aligned on lap distance inside it (analysis/lap_comparison.py) and compared. Only
losses above the threshold are called, at most a few per lap, and each corner once per
lap. The input differences are observations, not proven causes.
"""
from __future__ import annotations

from dataclasses import dataclass

from analysis.coach import Category, CoachEvent, Confidence, Priority
from analysis.lap_comparison import Segment, samples_frame, segment_between
from analysis.radio_speech import say_gap

BEFORE_M, AFTER_M = 200.0, 100.0
MIN_LOSS_S = 0.01  # below this, alignment noise


def corner_windows(corners) -> list[tuple]:
    """(corner, start m, end m) per corner."""
    out = []
    for k, c in enumerate(corners):
        start, end = c.apex_m - BEFORE_M, c.apex_m + AFTER_M
        if k:
            start = max(start, (corners[k - 1].apex_m + c.apex_m) / 2)
        if k + 1 < len(corners):
            end = min(end, (c.apex_m + corners[k + 1].apex_m) / 2)
        out.append((c, start, end))
    return out


@dataclass(frozen=True)
class CornerResult:
    corner: object
    segment: Segment
    ref_label: str

    def event(self) -> CoachEvent:
        s, n = self.segment, self.corner.number
        ref = "your recorded PB" if self.ref_label.startswith("PB") else self.ref_label.lower()
        said = []
        if s.min_speed_lap is not None and s.min_speed_ref is not None and abs(s.min_speed_lap - s.min_speed_ref) >= 3:
            dv = s.min_speed_lap - s.min_speed_ref
            said.append(f"Minimum speed {abs(dv):.0f} {'lower' if dv < 0 else 'higher'}.")
        if s.brake_onset_diff_m is not None and abs(s.brake_onset_diff_m) >= 10:
            said.append(f"Braked {abs(s.brake_onset_diff_m):.0f} metres "
                        f"{'later' if s.brake_onset_diff_m > 0 else 'earlier'}.")
        if s.full_throttle_diff_m is not None and abs(s.full_throttle_diff_m) >= 10:
            said.append(f"Full throttle {abs(s.full_throttle_diff_m):.0f} metres "
                        f"{'later' if s.full_throttle_diff_m > 0 else 'earlier'}.")
        obs = s.observations()
        msg = f"Corner {n}: lost {s.time_delta_s:.2f} s vs {ref}" + (
            ". Observed: " + "; ".join(obs).lower() if obs else "")
        return CoachEvent(f"corner:{n}", Category.CORNERS, msg, Priority.MEDIUM, Confidence.MEDIUM,
                          f"Distance-aligned telemetry vs {ref}, {s.start_m:.0f}–{s.end_m:.0f} m; corner numbers are "
                          "detected from your speed trace; input differences are observations, not proven causes",
                          speech=f"Corner {n}, lost {say_gap(s.time_delta_s)}." + (f" {said[0]}" if said else ""))


class CornerCoach:
    def __init__(self, threshold_s: float = 0.15, max_calls_per_lap: int = 3):
        self.threshold_s = threshold_s
        self.max_calls_per_lap = max_calls_per_lap
        self.clear_reference()
        self.reset()

    def reset(self) -> None:
        """Forget the lap in progress (a new session)."""
        self._lap: int | None = None
        self._done: set[int] = set()
        self._calls = 0
        self._last_d: float | None = None

    def set_reference(self, corners, ref_df, label: str) -> None:
        self._windows = corner_windows(corners or [])
        self._ref_parts = [ref_df[(ref_df.lap_distance >= s - 20) & (ref_df.lap_distance <= e + 20)]
                           for _, s, e in self._windows]
        self.ref_label = label

    def clear_reference(self) -> None:
        self._windows: list[tuple] = []
        self._ref_parts: list = []
        self.ref_label: str | None = None

    def on_progress(self, lap_number: int, samples) -> CornerResult | None:
        """Call with the lap-in-progress samples after each telemetry sample; returns a call when one is due."""
        if not self._windows or not samples:
            return None
        d = samples[-1][2]
        if lap_number != self._lap:
            first = samples[0][2]
            self._lap, self._calls = lap_number, 0
            # corners passed before telemetry for this lap began can't be judged
            self._done = {k for k, (_, s, _) in enumerate(self._windows) if s < first - 1}
        elif self._last_d is not None and d < self._last_d - 50:  # flashback within the lap
            self._done = {k for k in self._done if self._windows[k][2] <= d}
        self._last_d = d
        due = [k for k, (_, _, e) in enumerate(self._windows) if e <= d and k not in self._done]
        if not due:
            return None
        self._done.update(due)
        if self._calls >= self.max_calls_per_lap:
            return None
        k = due[-1]
        corner, start, end = self._windows[k]
        part = [r for r in samples if start - 20 <= r[2] <= end + 20]
        seg = segment_between(samples_frame(part), self._ref_parts[k], start, end) if len(part) > 5 else None
        if seg is None or seg.time_delta_s < max(self.threshold_s, MIN_LOSS_S):
            return None
        self._calls += 1
        return CornerResult(corner, seg, self.ref_label)
