"""Measured fuel and tyre use per lap, and projections from those measurements.

Each lap's use is the game's own fuel and wear readings at one line crossing minus
the previous one, so the first lap you join is never measured. A rise in fuel or a
drop in wear (flashback, new tyres) makes that lap's figure None rather than wrong.
"""
from __future__ import annotations

from statistics import mean

AVG_LAPS = 3  # projections use your most recent measured laps


class LapResources:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._fuel: float | None = None
        self._wear: tuple | None = None

    def line_crossed(self, fuel_kg: float | None, wear_pct: tuple | None) -> tuple[float | None, float | None]:
        """(fuel used kg, wear added % on the most-worn tyre) for the lap just finished."""
        fuel_used = wear_added = None
        if self._fuel is not None and fuel_kg is not None and fuel_kg <= self._fuel:
            fuel_used = round(self._fuel - fuel_kg, 3)
        if self._wear is not None and wear_pct is not None:
            diffs = [now - before for now, before in zip(wear_pct, self._wear, strict=True)]
            if min(diffs) >= -0.05:  # any tyre less worn than before = tyres changed
                wear_added = round(max(diffs), 2)
        self._fuel, self._wear = fuel_kg, wear_pct
        return fuel_used, wear_added


def _recent(laps, attr) -> list[float]:
    vals = [getattr(lap, attr) for lap in laps if getattr(lap, attr) is not None and not lap.pit_lap]
    return vals[-AVG_LAPS:]


def race_laps_remaining(snap) -> float | None:
    """Laps left in a race including the rest of the current one, from the game's lap count and distance."""
    if not snap.total_laps or snap.lap_number is None:
        return None
    frac = 0.0
    if snap.track_length_m and snap.lap_distance_m and snap.lap_distance_m > 0:
        frac = min(snap.lap_distance_m / snap.track_length_m, 1.0)
    return max(snap.total_laps - snap.lap_number + 1 - frac, 0.0)


def projection(laps, snap) -> dict:
    """Averages over the most recent measured laps, and what they imply. Every value may be None."""
    burn = _recent(laps, "fuel_used_kg")
    wear = _recent(laps, "tyre_wear_pct")
    avg_burn = mean(burn) if burn else None
    out = {"fuel_per_lap_kg": avg_burn, "fuel_laps_measured": len(burn),
           "wear_per_lap_pct": mean(wear) if wear else None, "wear_laps_measured": len(wear),
           "fuel_laps_left": None, "fuel_margin_laps": None, "race_laps_left": race_laps_remaining(snap)}
    if avg_burn and snap.fuel_kg is not None:
        out["fuel_laps_left"] = snap.fuel_kg / avg_burn
        if out["race_laps_left"] is not None:
            out["fuel_margin_laps"] = out["fuel_laps_left"] - out["race_laps_left"]
    return out
