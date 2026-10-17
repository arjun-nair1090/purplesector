"""Lap boundaries, sector times and per-lap telemetry samples for the player car.

Lap times always come from the game (m_lastLapTimeInMS at the lap-number change);
nothing here is estimated. A lap's samples are flagged incomplete if tracking started
mid-lap, telemetry had a gap, or a flashback rewound past recorded samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from analysis.statistics import best_sectors
from config.defaults import MAX_LAP_SAMPLES

BASE_SAMPLE_FIELDS = ("session_time", "lap_ms", "lap_distance", "speed", "throttle", "brake", "steer", "gear", "rpm")
POSITION_FIELDS = ("pos_x", "pos_y", "pos_z")  # world position from the motion packet; None until one arrives
SAMPLE_FIELDS = BASE_SAMPLE_FIELDS + POSITION_FIELDS
MAX_SAMPLE_GAP_S = 0.5


@dataclass
class CompletedLap:
    lap_number: int
    lap_time_ms: int
    sector1_ms: int | None
    sector2_ms: int | None
    sector3_ms: int | None
    valid: bool                 # game's lap-invalid flag was clear at the line
    samples_complete: bool      # whole lap recorded without gaps
    pit_lap: bool
    out_lap: bool
    samples: list = field(default_factory=list, repr=False)  # tuples in SAMPLE_FIELDS order (positions optional)
    max_speed_kph: float | None = None
    fuel_used_kg: float | None = None    # fuel reading at the line vs the previous line crossing
    tyre_wear_pct: float | None = None   # wear added this lap on the most-worn tyre
    tyre_age_laps: int | None = None     # game's tyre age at the line
    tyre_compound: str | None = None     # visual compound (Soft, Medium, ...) at the line
    setup: dict | None = None            # car setup at the line (game's car-setups packet)

    @property
    def timed(self) -> bool:
        """Eligible as a performance reference: valid, not an in/out lap."""
        return self.valid and not self.pit_lap and not self.out_lap and self.lap_time_ms > 0

    @property
    def sectors(self) -> tuple:
        return self.sector1_ms, self.sector2_ms, self.sector3_ms


class LapEventKind(Enum):
    LAP_COMPLETED = auto()
    LAP_INVALIDATED = auto()
    PIT_ENTRY = auto()
    PIT_EXIT = auto()
    FLASHBACK = auto()


@dataclass
class LapEvent:
    kind: LapEventKind
    lap_number: int
    lap: CompletedLap | None = None
    crossed_line: bool = False  # FLASHBACK only: rewound back into an earlier lap


class _CurrentLap:
    def __init__(self, lap_number: int, started_mid_lap: bool):
        self.lap_number = lap_number
        self.samples: list[tuple] = []
        self.complete = not started_mid_lap
        self.pit = False
        self.out_lap = False
        self.last = None  # last LapData seen for this lap


class LapTracker:
    def __init__(self, keep_recent_samples: int = 6):
        self.keep_recent_samples = keep_recent_samples
        self.reset()

    def reset(self) -> None:
        self.laps: list[CompletedLap] = []
        self._cur: _CurrentLap | None = None
        self._last_lap_data = None
        self._position: tuple = (None, None, None)

    def on_motion(self, player_motion) -> None:
        self._position = player_motion.position

    # -- queries --
    @property
    def current_lap_number(self) -> int | None:
        return self._cur.lap_number if self._cur else None

    @property
    def current_lap_off_pace(self) -> bool:
        """The lap in progress went through the pit lane or is an out lap."""
        return bool(self._cur and (self._cur.pit or self._cur.out_lap))

    @property
    def current_samples(self) -> list:
        return self._cur.samples if self._cur else []

    def best_lap(self, exclude: CompletedLap | None = None) -> CompletedLap | None:
        timed = [lap for lap in self.laps if lap.timed and lap is not exclude]
        return min(timed, key=lambda lap: lap.lap_time_ms, default=None)

    def best_sectors(self) -> tuple:
        """Best individual sector times among valid timed laps (session-best sectors)."""
        return best_sectors(self.laps)

    # -- input --
    def on_lap_data(self, lap) -> list[LapEvent]:
        events: list[LapEvent] = []
        cur, prev = self._cur, self._last_lap_data
        if cur is None:
            self._cur = _CurrentLap(lap.current_lap_num, self._mid_lap(lap))
        elif lap.current_lap_num > cur.lap_number:
            events.append(self._complete(cur, lap))
            # skipping a lap number means we missed telemetry: the new lap can't be complete either
            self._cur = _CurrentLap(lap.current_lap_num, lap.current_lap_num != cur.lap_number + 1)
        elif lap.current_lap_num < cur.lap_number:
            # flashback / restart back across the line: the lap in progress is gone, and so is every lap
            # the rewind undid (it is driven again and reported again)
            self._cur = _CurrentLap(lap.current_lap_num, True)
            self.laps = [done for done in self.laps if done.lap_number < lap.current_lap_num]
            events.append(LapEvent(LapEventKind.FLASHBACK, lap.current_lap_num, crossed_line=True))
        elif prev and lap.current_lap_time_ms + 100 < prev.current_lap_time_ms:
            # flashback within the lap: drop samples recorded after the rewind point
            cur.samples = [s for s in cur.samples if s[1] <= lap.current_lap_time_ms]
            events.append(LapEvent(LapEventKind.FLASHBACK, cur.lap_number))

        cur = self._cur
        if prev and cur.last and not prev.current_lap_invalid and lap.current_lap_invalid:
            events.append(LapEvent(LapEventKind.LAP_INVALIDATED, cur.lap_number))
        if prev:
            if prev.pit_status == 0 and lap.pit_status != 0:
                events.append(LapEvent(LapEventKind.PIT_ENTRY, cur.lap_number))
            elif prev.pit_status != 0 and lap.pit_status == 0:
                events.append(LapEvent(LapEventKind.PIT_EXIT, cur.lap_number))
        cur.pit |= lap.pit_status != 0
        cur.out_lap |= lap.driver_status == 3
        cur.last = lap
        self._last_lap_data = lap
        return events

    def on_telemetry(self, session_time: float, car) -> None:
        cur, lap = self._cur, self._last_lap_data
        if cur is None or lap is None:
            return
        if cur.samples and session_time - cur.samples[-1][0] > MAX_SAMPLE_GAP_S:
            cur.complete = False
        if len(cur.samples) >= MAX_LAP_SAMPLES:
            cur.complete = False
            return
        cur.samples.append((session_time, lap.current_lap_time_ms, lap.lap_distance, car.speed,
                            round(car.throttle * 100, 1), round(car.brake * 100, 1), car.steer,
                            car.gear, car.engine_rpm, *self._position))

    # -- internals --
    @staticmethod
    def _mid_lap(lap) -> bool:
        return lap.current_lap_time_ms > 1000

    def _complete(self, cur: _CurrentLap, new_lap) -> LapEvent:
        last = cur.last
        s1 = last.sector1_time_ms or None if last else None
        s2 = last.sector2_time_ms or None if last else None
        total = new_lap.last_lap_time_ms
        s3 = total - s1 - s2 if s1 and s2 and total > s1 + s2 else None
        samples = cur.samples
        complete = cur.complete and len(samples) > 60 and bool(samples) and samples[0][1] < 1000
        lap = CompletedLap(
            lap_number=cur.lap_number, lap_time_ms=total, sector1_ms=s1, sector2_ms=s2, sector3_ms=s3,
            valid=bool(last) and not last.current_lap_invalid and total > 0,
            samples_complete=complete, pit_lap=cur.pit, out_lap=cur.out_lap, samples=samples,
            max_speed_kph=max((s[3] for s in samples), default=None),
        )
        self.laps.append(lap)
        self._trim_samples()
        return LapEvent(LapEventKind.LAP_COMPLETED, lap.lap_number, lap)

    def _trim_samples(self) -> None:
        """Bound memory: keep samples only for the best lap and the most recent few laps."""
        best = self.best_lap()
        for lap in self.laps[:-self.keep_recent_samples]:
            if lap is not best:
                lap.samples = []
