"""Field timing: every car's best lap and sectors, and a timing tower.

The game sends a session-history packet for each car in turn, with its laps and the lap
numbers of its best lap and best sectors (the game decides which laps count). Gaps in
the tower: in races, the interval to the car ahead, the game's own figure where it sends
one (F1 23 on) and otherwise measured from both cars' distance traces (analysis/gaps.py);
in other sessions, how far each best lap is from the fastest.
"""
from __future__ import annotations

from dataclasses import dataclass

from telemetry.packet_models import RESULT_ACTIVE, RESULT_FINISHED


@dataclass(frozen=True)
class CarBest:
    lap_ms: int | None
    sectors: tuple  # best S1, S2, S3 in ms, each None if the game has none


@dataclass(frozen=True)
class VersusRow:
    label: str            # S1, S2, S3, Lap, Ideal
    field_ms: int | None  # fastest in the session
    holder: str | None
    yours_ms: int | None

    @property
    def delta_s(self) -> float | None:
        return None if self.field_ms is None or self.yours_ms is None else (self.yours_ms - self.field_ms) / 1000


@dataclass(frozen=True)
class TowerRow:
    car_index: int
    position: int
    name: str
    is_player: bool
    best_lap_ms: int | None
    sectors: tuple
    gap_s: float | None
    gap_source: str | None      # "game", "measured", "best lap" or None
    purple_lap: bool
    purple_sectors: tuple


def _at(laps, n: int, attr: str) -> int | None:
    return getattr(laps[n - 1], attr) or None if 1 <= n <= len(laps) else None


class FieldTiming:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._best: dict[int, CarBest] = {}

    def on_history(self, h) -> None:
        hd, laps = h.header, h.laps
        self._best[hd.car_idx] = CarBest(
            _at(laps, hd.best_lap_time_lap_num, "lap_time_ms"),
            tuple(_at(laps, n, f"sector{i}_time_ms") for i, n in
                  ((1, hd.best_sector1_lap_num), (2, hd.best_sector2_lap_num), (3, hd.best_sector3_lap_num))))

    def best(self, car: int) -> CarBest | None:
        return self._best.get(car)

    def _fastest(self) -> tuple[list, tuple | None]:
        """[(ms, car) per sector], (ms, car) for the lap."""
        sectors = [min(((b.sectors[i], c) for c, b in self._best.items() if b.sectors[i]), default=None)
                   for i in range(3)]
        lap = min(((b.lap_ms, c) for c, b in self._best.items() if b.lap_ms), default=None)
        return sectors, lap

    def versus(self, player: int, names: dict) -> list[VersusRow]:
        if not self._best:
            return []
        sectors, lap = self._fastest()
        mine = self._best.get(player, CarBest(None, (None, None, None)))

        def who(c):
            return "You" if c == player else names.get(c) or f"Car {c + 1}"
        rows = [VersusRow(f"S{i + 1}", s[0] if s else None, who(s[1]) if s else None, mine.sectors[i])
                for i, s in enumerate(sectors)]
        rows.append(VersusRow("Lap", lap[0] if lap else None, who(lap[1]) if lap else None, mine.lap_ms))
        ideal = sum(s[0] for s in sectors) if all(sectors) else None
        rows.append(VersusRow("Ideal", ideal, None, sum(mine.sectors) if all(mine.sectors) else None))
        return rows

    def tower(self, lap_cars, names: dict, player: int, race: bool, gaps=None) -> list[TowerRow]:
        if not lap_cars:
            return []
        # finished cars (status 3) keep their place once they take the flag
        active = sorted((c.car_position, i) for i, c in enumerate(lap_cars)
                        if c.result_status in (RESULT_ACTIVE, RESULT_FINISHED) and c.car_position)
        sectors, lap = self._fastest()
        rows, ahead = [], None
        for pos, i in active:
            b = self._best.get(i, CarBest(None, (None, None, None)))
            gap = source = None
            if race and ahead is not None:
                game = lap_cars[i].delta_to_car_in_front_ms
                if game is not None:
                    gap, source = game / 1000, "game"
                elif gaps is not None and (g := gaps.gap(ahead, i)) is not None:
                    gap, source = g, "measured"
            elif not race and lap and b.lap_ms and i != lap[1]:
                gap, source = (b.lap_ms - lap[0]) / 1000, "best lap"
            rows.append(TowerRow(i, pos, names.get(i) or f"Car {i + 1}", i == player, b.lap_ms, b.sectors, gap,
                                 source, bool(lap and lap[1] == i),
                                 tuple(bool(s and s[1] == i) for s in sectors)))
            ahead = i
        return rows
