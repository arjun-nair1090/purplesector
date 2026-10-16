"""Race gaps to the cars directly ahead of and behind the player.

F1 22's lap data has no gap field, so gaps are measured from each car's own trace of
(total distance, session time): the gap from a leader to a follower is how long ago
the leader passed the follower's current total distance. Nothing is extrapolated; if
the leader was never seen at that distance (just joined, or trace trimmed), the gap is
None. The per-lap change is the difference between gaps sampled at the player's
consecutive line crossings.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

from telemetry.packet_models import MAX_CARS, RESULT_ACTIVE

STEP_M = 5.0        # trace resolution
KEEP_S = 240.0      # covers a lapped car's gap on the longest tracks
RACE_SESSIONS = ("Race", "Race 2", "Race 3")


@dataclass(frozen=True)
class CarGap:
    car_index: int
    name: str
    position: int
    gap_s: float | None           # always positive: seconds between the two cars
    change_per_lap_s: float | None  # + = the gap grew over the player's last lap, - = it shrank


class GapTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._traces: dict[int, tuple[list[float], list[float]]] = {}
        self._cars = None
        self._now: float | None = None
        self._names: dict[int, str] = {}
        self._line_gaps: dict[int, float] = {}    # gap to each car at the player's latest line crossing
        self._lap_change: dict[int, float] = {}   # change between the last two line crossings

    def set_names(self, participants) -> None:
        self._names = {}
        for i, p in enumerate(participants or ()):
            name = p.name.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
            self._names[i] = name or (f"Car #{p.race_number}" if p.race_number else f"Car {i + 1}")

    def on_lap_data(self, session_time: float, cars) -> None:
        if self._now is not None and session_time < self._now:
            self._rewind(session_time)  # flashback: forget everything after the new time
        self._now, self._cars = session_time, cars
        for i, c in enumerate(cars):
            if c.result_status != RESULT_ACTIVE:
                continue
            dist, times = self._traces.setdefault(i, ([], []))
            d = c.total_distance
            if dist and d < dist[-1]:
                k = bisect.bisect_right(dist, d)
                del dist[k:], times[k:]
            if not dist or d - dist[-1] >= STEP_M:
                dist.append(d)
                times.append(session_time)
            if times and session_time - times[0] > KEEP_S:
                k = bisect.bisect_left(times, session_time - KEEP_S)
                del dist[:k], times[:k]

    def _rewind(self, session_time: float) -> None:
        for dist, times in self._traces.values():
            k = bisect.bisect_right(times, session_time)
            del dist[k:], times[k:]
        self._line_gaps.clear()
        self._lap_change.clear()

    def gap(self, leader: int, follower: int) -> float | None:
        """Seconds since `leader` was where `follower` is now."""
        if self._cars is None or leader not in self._traces:
            return None
        dist, times = self._traces[leader]
        d = self._cars[follower].total_distance
        if not dist or d < dist[0] or d > dist[-1]:
            return None
        k = bisect.bisect_left(dist, d)
        if dist[k] == d or k == 0:
            t = times[k]
        else:
            f = (d - dist[k - 1]) / (dist[k] - dist[k - 1])
            t = times[k - 1] + f * (times[k] - times[k - 1])
        return self._now - t

    def neighbours(self, player: int) -> tuple[int | None, int | None]:
        """Car indices one race position ahead and behind the player (active cars only)."""
        if self._cars is None or player >= MAX_CARS:
            return None, None
        me = self._cars[player].car_position
        by_pos = {c.car_position: i for i, c in enumerate(self._cars)
                  if c.result_status == RESULT_ACTIVE and c.car_position and i != player}
        return by_pos.get(me - 1), by_pos.get(me + 1)

    def _pair_gap(self, player: int, other: int, ahead: bool) -> float | None:
        return self.gap(other, player) if ahead else self.gap(player, other)

    def on_player_lap(self, player: int) -> None:
        """Call when the player crosses the line: samples the gaps for the per-lap trend."""
        ahead, behind = self.neighbours(player)
        new = {}
        for idx, is_ahead in ((ahead, True), (behind, False)):
            if idx is None:
                continue
            g = self._pair_gap(player, idx, is_ahead)
            if g is None:
                continue
            new[idx] = g
            if idx in self._line_gaps:
                self._lap_change[idx] = g - self._line_gaps[idx]
            else:
                self._lap_change.pop(idx, None)
        self._line_gaps = new

    def view(self, player: int) -> dict:
        ahead, behind = self.neighbours(player)
        out = {}
        for key, idx, is_ahead in (("ahead", ahead, True), ("behind", behind, False)):
            if idx is None:
                out[key] = None
                continue
            out[key] = CarGap(idx, self._names.get(idx, f"Car {idx + 1}"), self._cars[idx].car_position,
                              self._pair_gap(player, idx, is_ahead), self._lap_change.get(idx))
        return out
