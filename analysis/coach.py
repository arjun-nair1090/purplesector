"""Deterministic, evidence-driven coaching rules.

Each rule is a pure function that returns a CoachEvent only when the data supports it,
otherwise None ("if there is insufficient evidence, say nothing"). The Coach applies
verbosity, minimum-confidence and cooldown/duplicate filters on top.
"""
from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from analysis.lap_comparison import compare_laps, samples_frame
from analysis.lap_tracker import CompletedLap
from analysis.statistics import brake_rise_times
from analysis.track_map import corners_in


class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Confidence(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Category(StrEnum):
    LAP_PERFORMANCE = "Lap performance"
    DRIVING_INPUTS = "Driving inputs"
    BRAKING = "Braking"
    THROTTLE = "Throttle"
    SESSION = "Session events"
    WEATHER = "Weather"
    RACE_CONTROL = "Race control"
    CORNERS = "Corners"
    PERSONAL_RECORD = "Personal records"
    SYSTEM = "System"


@dataclass(frozen=True)
class CoachEvent:
    key: str                 # dedupe key; same key within cooldown is suppressed
    category: Category
    message: str             # shown in the panel
    priority: Priority
    confidence: Confidence
    evidence: str            # what the conclusion is based on
    speech: str | None = None  # spoken form; defaults to message
    timestamp: float = field(default_factory=time.time)

    @property
    def spoken(self) -> str:
        return self.speech or self.message


def fmt_lap(ms: int | None) -> str:
    if not ms:
        return "—"
    m, rem = divmod(int(ms), 60_000)
    return f"{m}:{rem / 1000:06.3f}" if m else f"{rem / 1000:.3f}"


def _spoken_time(ms: int) -> str:
    m, rem = divmod(int(ms), 60_000)
    s = rem / 1000
    return f"{m} {s:.1f}" if m else f"{s:.1f} seconds"


@dataclass
class CoachConfig:
    verbosity: str = "normal"
    min_confidence: str = "medium"
    cooldown_s: float = 30.0
    sector_loss_threshold_s: float = 0.25
    segment_loss_threshold_s: float = 0.15
    brake_abrupt_ratio: float = 0.6
    brake_min_diff_s: float = 0.04
    min_reference_laps: int = 2

    @classmethod
    def from_settings(cls, s) -> CoachConfig:
        return cls(verbosity=s.coach_verbosity, min_confidence=s.coach_min_confidence, cooldown_s=s.coach_cooldown_s,
                   sector_loss_threshold_s=s.sector_loss_threshold_s,
                   segment_loss_threshold_s=s.segment_loss_threshold_s, brake_abrupt_ratio=s.brake_abrupt_ratio)


# ---------------- rules (pure) ----------------

def rule_lap_completed(lap: CompletedLap) -> CoachEvent | None:
    if lap.lap_time_ms <= 0:
        return None
    status = "" if lap.valid else " (invalid)"
    return CoachEvent(f"lap_done:{lap.lap_number}", Category.LAP_PERFORMANCE,
                      f"Lap {lap.lap_number}: {fmt_lap(lap.lap_time_ms)}{status}", Priority.MEDIUM, Confidence.HIGH,
                      "Game lap timing", speech=f"Lap {lap.lap_number}, {_spoken_time(lap.lap_time_ms)}"
                      + ("" if lap.valid else ", invalid"))


def rule_session_best(lap: CompletedLap, previous_best: CompletedLap | None) -> CoachEvent | None:
    """Only after the game has confirmed a valid lap time, and only if it beats an earlier valid lap."""
    if not lap.timed or previous_best is None or lap.lap_time_ms >= previous_best.lap_time_ms:
        return None
    gain = (previous_best.lap_time_ms - lap.lap_time_ms) / 1000
    return CoachEvent(f"pb:{lap.lap_number}", Category.PERSONAL_RECORD,
                      f"Session best: {fmt_lap(lap.lap_time_ms)} (−{gain:.3f} s)", Priority.HIGH,
                      Confidence.HIGH, f"Valid lap {lap.lap_number} vs previous best lap {previous_best.lap_number} "
                      "(game timing)", speech=f"Session best by {gain:.2f} seconds. Nice lap.")


def rule_alltime_best(lap: CompletedLap, alltime_ms: int | None, track: str | None) -> CoachEvent | None:
    """Beats the best valid lap PurpleSector has ever recorded on this track (same source)."""
    if not lap.timed or alltime_ms is None or lap.lap_time_ms >= alltime_ms:
        return None
    gain = (alltime_ms - lap.lap_time_ms) / 1000
    where = track or "this track"
    return CoachEvent(f"alltime_pb:{lap.lap_number}", Category.PERSONAL_RECORD,
                      f"Personal best at {where}: {fmt_lap(lap.lap_time_ms)} (−{gain:.3f} s)", Priority.HIGH,
                      Confidence.HIGH, f"Valid lap {lap.lap_number} vs your best recorded lap {fmt_lap(alltime_ms)} "
                      "(game timing)", speech=f"Personal best! {gain:.2f} seconds quicker than your best recorded lap here.")


def rule_sector_loss(lap: CompletedLap, best: CompletedLap | None, threshold_s: float) -> CoachEvent | None:
    """Slowest sector vs the best lap's sectors; both laps must be valid with all sector times."""
    if not lap.timed or best is None or best is lap or not all(lap.sectors) or not all(best.sectors):
        return None
    deltas = [(a - b) / 1000 for a, b in zip(lap.sectors, best.sectors, strict=True)]
    worst = max(range(3), key=lambda i: deltas[i])
    if deltas[worst] < threshold_s:
        return None
    return CoachEvent("sector_loss", Category.LAP_PERFORMANCE,
                      f"Sector {worst + 1} was {deltas[worst]:.2f} s slower than your best lap",
                      Priority.MEDIUM, Confidence.HIGH,
                      f"Game sector times: lap {lap.lap_number} S{worst + 1} {fmt_lap(lap.sectors[worst])} vs "
                      f"best lap {best.lap_number} {fmt_lap(best.sectors[worst])}",
                      speech=f"Sector {worst + 1} was {deltas[worst]:.1f} slower than your best.")


def rule_segment_loss(lap: CompletedLap, best: CompletedLap | None, threshold_s: float,
                      corners=None) -> CoachEvent | None:
    """Largest distance-aligned loss vs the best lap, with the measured input differences there.
    `corners` (from the track map) only adds a name to the location."""
    if (not lap.timed or best is None or best is lap or not lap.samples_complete or not best.samples_complete):
        return None
    cmp = compare_laps(samples_frame(lap.samples), samples_frame(best.samples), lap_time_ms=lap.lap_time_ms,
                       ref_time_ms=best.lap_time_ms)
    if not cmp.conclusive or not cmp.segments or cmp.segments[0].time_delta_s < threshold_s:
        return None
    seg = cmp.segments[0]
    obs = seg.observations()
    corner = corners_in(corners or [], seg.start_m, seg.end_m)
    where = f"{seg.start_m:.0f}–{seg.end_m:.0f} m" + (f" ({corner})" if corner else "")
    msg = f"Lost {seg.time_delta_s:.2f} s vs best lap at {where}"
    place = corner or f"around {int(round(seg.start_m, -2))} metres"
    speech = f"Lost {seg.time_delta_s:.1f} {'at ' + place if corner else place} versus your best lap."
    if obs:
        msg += ". Observed: " + "; ".join(obs).lower()
        speech += f" {obs[0]}."
    return CoachEvent("segment_loss", Category.DRIVING_INPUTS, msg, Priority.MEDIUM, Confidence.MEDIUM,
                      f"Distance-aligned telemetry vs lap {best.lap_number}; input differences are observations, "
                      "not proven causes" + ("; corner numbers are detected from your speed trace" if corner else ""),
                      speech=speech)


def rule_brake_abruptness(lap: CompletedLap, previous: list[CompletedLap], ratio: float, min_diff_s: float,
                          min_laps: int) -> CoachEvent | None:
    """Median brake rise time this lap vs the median over previous comparable laps."""
    refs = [p for p in previous if p.timed and p.samples_complete and p.samples]
    if not lap.samples_complete or len(refs) < min_laps:
        return None
    cur = brake_rise_times(lap.samples)
    ref_medians = [statistics.median(r) for r in (brake_rise_times(p.samples) for p in refs) if len(r) >= 3]
    if len(cur) < 3 or len(ref_medians) < min_laps:
        return None
    cur_m, ref_m = statistics.median(cur), statistics.mean(ref_medians)
    if cur_m > ref_m * ratio or ref_m - cur_m < min_diff_s:
        return None
    return CoachEvent("brake_abrupt", Category.BRAKING,
                      "Brake input was noticeably more abrupt than on your previous laps", Priority.MEDIUM,
                      Confidence.MEDIUM, f"Median brake rise {cur_m * 1000:.0f} ms vs {ref_m * 1000:.0f} ms "
                      f"over {len(ref_medians)} previous laps ({len(cur)} brake applications)",
                      speech="Brake input is unusually abrupt compared with your previous laps.")


def _say_s(x: float) -> str:
    return f"{'minus' if x < 0 else 'plus'} {abs(x):.1f}"


def radio_report(n: int, *, delta_s=None, delta_ref=None, gaps=None, fuel_margin_laps=None,
                 fuel_margin_source=None, last_lap_ms=None) -> CoachEvent:
    """The status call the driver asked for (wheel button or Radio check). Only facts that are available
    right now are read out; nothing is filled in."""
    parts, said = [], []
    if delta_s is not None:
        parts.append(f"Delta {fmt_signed(delta_s)} s vs {delta_ref}")
        said.append(f"Delta {_say_s(delta_s)}.")
    elif last_lap_ms:
        parts.append(f"Last lap {fmt_lap(last_lap_ms)}")
        said.append(f"Last lap {_spoken_time(last_lap_ms)}.")
    for key, label in (("ahead", "ahead"), ("behind", "behind")):
        g = (gaps or {}).get(key)
        if g is None or g.gap_s is None:
            continue
        trend = ""
        if g.change_per_lap_s is not None and abs(g.change_per_lap_s) >= 0.05:
            closing = g.change_per_lap_s < 0
            trend = f", {'closing' if closing else 'opening'} {abs(g.change_per_lap_s):.1f} a lap"
        parts.append(f"P{g.position} {g.name} {label} {g.gap_s:.1f} s{trend}")
        said.append(f"Gap {label} {g.gap_s:.1f}{trend}.")
    if fuel_margin_laps is not None:
        parts.append(f"Fuel {fmt_signed(fuel_margin_laps, 1)} laps ({fuel_margin_source})")
        said.append(f"Fuel {_say_s(fuel_margin_laps)} laps.")
    if not parts:
        return CoachEvent(f"radio:{n}", Category.SYSTEM, "Radio check: no live data yet", Priority.HIGH,
                          Confidence.HIGH, "Requested status report", speech="No data yet.")
    return CoachEvent(f"radio:{n}", Category.SESSION, "Radio: " + ". ".join(parts), Priority.HIGH, Confidence.HIGH,
                      "Requested status report from live telemetry", speech=" ".join(said))


def fmt_signed(x: float, places: int = 3) -> str:
    return f"{x:+.{places}f}".replace("-", "−")


# ---------------- coach (stateful filters) ----------------

_VERBOSITY_MIN_PRIORITY = {"quiet": Priority.HIGH, "normal": Priority.MEDIUM, "detailed": Priority.LOW}
_CONFIDENCE = {"low": Confidence.LOW, "medium": Confidence.MEDIUM, "high": Confidence.HIGH}


class Coach:
    def __init__(self, config: CoachConfig | None = None, clock=time.monotonic):
        self.config = config or CoachConfig()
        self._clock = clock
        self._last_emitted: dict[str, float] = {}
        self.events: deque[CoachEvent] = deque(maxlen=50)
        self.suppressed = 0

    def reset(self) -> None:
        self._last_emitted.clear()
        self.events.clear()

    def accept(self, event: CoachEvent | None) -> bool:
        """Apply verbosity, confidence and cooldown filters; record accepted events."""
        if event is None:
            return False
        c = self.config
        if event.priority < _VERBOSITY_MIN_PRIORITY.get(c.verbosity, Priority.MEDIUM):
            return False
        if event.confidence < _CONFIDENCE.get(c.min_confidence, Confidence.MEDIUM):
            return False
        now = self._clock()
        last = self._last_emitted.get(event.key)
        if last is not None and now - last < c.cooldown_s:
            self.suppressed += 1
            return False
        self._last_emitted[event.key] = now
        self.events.append(event)
        return True

    def on_lap_completed(self, lap: CompletedLap, tracker, alltime_ms: int | None = None,
                         track: str | None = None, corners=None) -> list[CoachEvent]:
        """`tracker.laps` already contains `lap` as its last entry. `alltime_ms` is the recorded best
        on this track from earlier sessions (None if there is no history)."""
        c = self.config
        previous = tracker.laps[:-1]
        prev_best = min((p for p in previous if p.timed), key=lambda p: p.lap_time_ms, default=None)
        if alltime_ms is not None and prev_best is not None:
            alltime_ms = min(alltime_ms, prev_best.lap_time_ms)  # must also beat earlier laps this session
        alltime = rule_alltime_best(lap, alltime_ms, track)
        # an all-time PB supersedes the session-best call
        candidates = [alltime or rule_session_best(lap, prev_best), rule_lap_completed(lap)]
        if prev_best is not None and lap is not prev_best:
            candidates += [
                rule_sector_loss(lap, prev_best, c.sector_loss_threshold_s),
                rule_segment_loss(lap, prev_best, c.segment_loss_threshold_s, corners),
            ]
        candidates.append(rule_brake_abruptness(lap, previous, c.brake_abrupt_ratio, c.brake_min_diff_s,
                                                c.min_reference_laps))
        # a new best lap makes "you lost time vs best" messages contradictory: drop them
        if candidates[0] is not None:
            candidates = [e for e in candidates if e is None or e.key not in ("sector_loss", "segment_loss")]
        return [e for e in candidates if self.accept(e)]

    def simple(self, key: str, category: Category, message: str, priority: Priority,
               evidence: str, speech: str | None = None) -> list[CoachEvent]:
        """Game-reported events (invalid lap, pit, session start/end, system) — high confidence by nature."""
        e = CoachEvent(key, category, message, priority, Confidence.HIGH, evidence, speech)
        return [e] if self.accept(e) else []
