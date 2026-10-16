"""Track outline from recorded world positions, and corners detected from your own speed trace.

The game sends world X/Y/Z per car, but the F1 22 spec doesn't say which axis is
vertical, so the outline uses the two axes with the largest spread (a circuit is far
wider than it is tall). Corner numbers are PurpleSector's own: speed minima along the
lap, in lap-distance order. They follow your braking points and may not match a
circuit's official turn numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analysis.lap_comparison import _clean

STEP_M = 5.0
APEX_WINDOW_M = 150.0      # an apex is the slowest point within this distance either side
MIN_SPEED_DROP_KPH = 12.0  # ... and at least this much slower than the fastest point in the run-up
RUN_UP_M = 400.0


@dataclass(frozen=True)
class Corner:
    number: int
    apex_m: float
    min_speed_kph: float

    @property
    def name(self) -> str:
        return f"Corner {self.number}"


def detect_corners(df: pd.DataFrame) -> list[Corner]:
    """Speed minima along the lap from one lap's samples (needs lap_distance and speed)."""
    d = _clean(df)
    if len(d) < 50:
        return []
    dist = d["lap_distance"].to_numpy(float)
    grid = np.arange(np.ceil(dist[0] / STEP_M) * STEP_M, dist[-1], STEP_M)
    if len(grid) < 10:
        return []
    raw = np.interp(grid, dist, d["speed"].to_numpy(float))
    v = np.convolve(raw, np.ones(5) / 5, mode="same")  # 25 m smoothing to find apexes; speeds reported raw
    w, run = int(APEX_WINDOW_M / STEP_M), int(RUN_UP_M / STEP_M)
    corners: list[Corner] = []
    for i in range(2, len(v) - 2):  # the convolution edges are unreliable
        lo, hi = max(0, i - w), min(len(v), i + w + 1)
        if v[i] > v[lo:hi].min() or v[max(0, i - run):i + 1].max() - v[i] < MIN_SPEED_DROP_KPH:
            continue
        if corners and grid[i] - corners[-1].apex_m < APEX_WINDOW_M:
            continue  # flat bottom: keep the first point
        corners.append(Corner(len(corners) + 1, float(grid[i]), float(raw[i - 2:i + 3].min())))
    return corners


def corners_in(corners: list[Corner], start_m: float, end_m: float) -> str | None:
    """'Corner 4' / 'Corners 4–5' for apexes inside a lap-distance range, else None."""
    hit = [c.number for c in corners if start_m <= c.apex_m < end_m]
    if not hit:
        return None
    return f"Corner {hit[0]}" if len(hit) == 1 else f"Corners {hit[0]}–{hit[-1]}"


def plane_axes(xyz: np.ndarray) -> tuple[int, int]:
    """Indices of the two world axes with the largest spread."""
    spread = np.nanstd(xyz, axis=0)
    a, b = sorted(np.argsort(spread)[-2:].tolist())
    return a, b


@dataclass
class TrackMap:
    axes: tuple[int, int]
    u: np.ndarray            # outline on the chosen plane, every STEP_M of lap distance
    v: np.ndarray
    distance_m: np.ndarray
    corners: list[Corner]

    @classmethod
    def from_samples(cls, df: pd.DataFrame) -> TrackMap | None:
        """Outline from one recorded lap. None if the lap has no positions (recorded before v2, or no
        motion packets) or too few of them."""
        if "pos_x" not in df or df["pos_x"].isna().mean() > 0.05:
            return None
        d = _clean(df.dropna(subset=["pos_x", "pos_y", "pos_z"]))
        if len(d) < 50:
            return None
        xyz = d[["pos_x", "pos_y", "pos_z"]].to_numpy(float)
        axes = plane_axes(xyz)
        dist = d["lap_distance"].to_numpy(float)
        grid = np.arange(np.ceil(dist[0] / STEP_M) * STEP_M, dist[-1], STEP_M)
        u = np.interp(grid, dist, xyz[:, axes[0]])
        v = np.interp(grid, dist, xyz[:, axes[1]])
        return cls(axes, u, v, grid, detect_corners(df))

    def project(self, position) -> tuple[float, float]:
        return position[self.axes[0]], position[self.axes[1]]

    def point_at(self, distance_m: float) -> tuple[float, float]:
        return (float(np.interp(distance_m, self.distance_m, self.u)),
                float(np.interp(distance_m, self.distance_m, self.v)))
