"""Statistics computed only from samples actually received."""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

FULL_THROTTLE_PCT = 98.0
BRAKING_PCT = 5.0


def lap_input_stats(df: pd.DataFrame) -> dict:
    """Per-lap stats from samples (fixed-rate sampling, so sample share == time share)."""
    if df.empty:
        return {}
    return {
        "samples": int(len(df)),
        "max_speed_kph": float(df["speed"].max()),
        "mean_speed_kph": float(df["speed"].mean()),
        "full_throttle_pct": float((df["throttle"] >= FULL_THROTTLE_PCT).mean() * 100),
        "braking_pct": float((df["brake"] >= BRAKING_PCT).mean() * 100),
    }


def brake_rise_times(samples, start_pct: float = 10.0, peak_share: float = 0.8) -> list[float]:
    """For each brake application, seconds from crossing `start_pct` to `peak_share` of that
    application's peak pressure. Lower = more abrupt. Samples are lap-tracker tuples."""
    if not samples:
        return []
    t = np.array([s[0] for s in samples], float)
    b = np.array([s[5] for s in samples], float)
    rises = []
    i, n = 1, len(b)
    while i < n:
        if b[i - 1] < start_pct <= b[i]:
            j = i
            while j < n and b[j] >= start_pct:
                j += 1
            peak = b[i:j].max()
            k = i + int(np.argmax(b[i:j] >= peak * peak_share))
            if peak >= 40:  # ignore light brushes of the pedal
                rises.append(float(t[k] - t[i - 1]))
            i = j
        i += 1
    return rises


def best_sectors(laps) -> tuple:
    """Best time per sector across valid, non-pit laps (session-best sectors)."""
    timed = [lap for lap in laps if lap.timed]
    return tuple(min((lap.sectors[i] for lap in timed if lap.sectors[i]), default=None) for i in range(3))


def theoretical_best_ms(laps) -> int | None:
    """Ideal lap: the sum of your own best sectors. Needs all three sector times."""
    sectors = best_sectors(laps)
    return sum(sectors) if all(sectors) else None


def consistency_s(laps) -> float | None:
    """Sample standard deviation of valid lap times, in seconds (needs 3+ laps)."""
    times = [lap.lap_time_ms for lap in laps if lap.timed]
    return float(np.std(times, ddof=1)) / 1000.0 if len(times) >= 3 else None


@dataclass
class SessionStats:
    """Running counters for the session in progress (measured from received telemetry)."""
    started_wall: float | None = None
    samples: int = 0
    full_throttle: int = 0
    braking: int = 0
    speed_sum: float = 0.0
    peak_speed_kph: int | None = None

    def reset(self) -> None:
        self.__init__()

    def add(self, speed: int, throttle_pct: float, brake_pct: float) -> None:
        if self.started_wall is None:
            self.started_wall = time.time()
        self.samples += 1
        self.full_throttle += throttle_pct >= FULL_THROTTLE_PCT
        self.braking += brake_pct >= BRAKING_PCT
        self.speed_sum += speed
        self.peak_speed_kph = speed if self.peak_speed_kph is None else max(self.peak_speed_kph, speed)

    def summary(self) -> dict:
        if not self.samples:
            return {}
        return {
            "duration_s": time.time() - self.started_wall,
            "mean_speed_kph": self.speed_sum / self.samples,
            "peak_speed_kph": self.peak_speed_kph,
            "full_throttle_pct": 100 * self.full_throttle / self.samples,
            "braking_pct": 100 * self.braking / self.samples,
            "samples": self.samples,
        }
