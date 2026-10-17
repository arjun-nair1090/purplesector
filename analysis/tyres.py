"""Tyre life: lap time against tyre age, per stint, from your own laps.

The game reports each lap's tyre age and compound at the line. A new stint starts when
the compound changes or the age goes down. The slope is a least-squares line through the
stint's timed laps, so it includes everything else that changed lap to lap, above all the
car getting lighter as fuel burns off. It is not tyre wear alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_FIT_LAPS = 3


@dataclass
class Stint:
    compound: str | None
    laps: list = field(default_factory=list)
    slope_s_per_lap: float | None = None  # + = slower as the tyres age
    fit_laps: int = 0


def stints(laps) -> list[Stint]:
    out: list[Stint] = []
    for lap in laps:
        if lap.tyre_age_laps is None:
            continue
        cur = out[-1] if out else None
        if cur is None or lap.tyre_compound != cur.compound or lap.tyre_age_laps < cur.laps[-1].tyre_age_laps:
            cur = Stint(lap.tyre_compound)
            out.append(cur)
        cur.laps.append(lap)
    for s in out:
        timed = [lap for lap in s.laps if lap.timed]
        ages = [lap.tyre_age_laps for lap in timed]
        if len(timed) >= MIN_FIT_LAPS and len(set(ages)) >= 2:
            s.slope_s_per_lap = float(np.polyfit(ages, [lap.lap_time_ms / 1000 for lap in timed], 1)[0])
            s.fit_laps = len(timed)
    return out
