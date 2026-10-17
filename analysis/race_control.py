"""Race control: what the game announces in its event packets, in words, with driver names.

Every entry comes from an event packet or from the session's safety-car status. Codes
PurpleSector has no name for are shown by number rather than guessed. Lap-invalidation
penalties are left out (the lap call already reports them), and overtakes and contact
are logged only when you are one of the cars. Entries that concern you, and safety cars
and red flags, carry a spoken form.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from analysis.coach import fmt_lap
from telemetry.packet_models import INFRINGEMENTS, PENALTY_TYPES

_SILENT_PENALTIES = {3, *range(10, 16)}  # reminders and lap invalidations
SC_NAMES = {1: "Safety car", 2: "Virtual safety car", 3: "Formation lap"}


@dataclass(frozen=True)
class RaceControlEntry:
    session_time: float
    lap: int | None
    code: str
    message: str
    involves_player: bool = False
    speech: str | None = None


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


class RaceControl:
    def __init__(self, keep: int = 200):
        self.entries: deque[RaceControlEntry] = deque(maxlen=keep)
        self.reset()

    def reset(self) -> None:
        self.entries.clear()
        self._sc: int | None = None
        self._sc_changes: list[tuple[float, int]] = []  # (session time, status) at each change, for rewinds

    def rewind(self, session_time: float) -> None:
        """A flashback to `session_time`: what was announced after it didn't happen (the game re-sends it if
        it happens again)."""
        kept = [e for e in self.entries if e.session_time <= session_time]
        self.entries.clear()
        self.entries.extend(kept)
        self._sc_changes = [c for c in self._sc_changes if c[0] <= session_time]
        self._sc = self._sc_changes[-1][1] if self._sc_changes else None

    def _add(self, *args, **kw) -> RaceControlEntry:
        e = RaceControlEntry(*args, **kw)
        self.entries.append(e)
        return e

    def on_event(self, ev, *, session_time: float, lap: int | None, names: dict, player: int) -> RaceControlEntry | None:
        d, code = ev.details, ev.code

        def name(i):
            return "You" if i == player else names.get(i) or f"Car {i + 1}"

        def add(message, involves=False, speech=None, on_lap=None):
            return self._add(session_time, on_lap or lap, code, message, involves, speech)

        if code == "PENA" and d is not None:
            if d.penalty_type in _SILENT_PENALTIES:
                return None
            kind = (PENALTY_TYPES[d.penalty_type] if d.penalty_type < len(PENALTY_TYPES)
                    else f"Penalty type {d.penalty_type}")
            if d.penalty_type == 4 and d.time != 255:
                what, said = f"{d.time} s time penalty", f"{d.time} second time penalty."
            elif d.penalty_type == 5:
                what, said = "warning", "Warning."
            else:
                what, said = _lower_first(kind), kind + "."
            why = (_lower_first(INFRINGEMENTS[d.infringement_type]) if d.infringement_type < len(INFRINGEMENTS)
                   else f"infringement {d.infringement_type}")
            mine = d.vehicle_idx == player
            return add(f"{name(d.vehicle_idx)}: {what}, {why}", mine, f"{said} {why[:1].upper() + why[1:]}." if mine
                       else None, on_lap=d.lap_num)
        if code == "FTLP" and d is not None:
            return add(f"Fastest lap: {name(d.vehicle_idx)} {fmt_lap(round(d.lap_time * 1000))}",
                       d.vehicle_idx == player)
        if code == "RTMT" and d is not None:
            return add(f"{name(d.vehicle_idx)} retired", d.vehicle_idx == player)
        if code == "RCWN" and d is not None:
            return add(f"{name(d.vehicle_idx)} wins", d.vehicle_idx == player)
        if code in ("DTSV", "SGSV") and d is not None:
            served = "a drive-through" if code == "DTSV" else "a stop-go"
            return add(f"{name(d.vehicle_idx)} served {served}", d.vehicle_idx == player)
        if code == "OVTK" and d is not None and player in (d.overtaking_vehicle_idx, d.being_overtaken_vehicle_idx):
            if d.overtaking_vehicle_idx == player:
                return add(f"You passed {name(d.being_overtaken_vehicle_idx)}", True)
            return add(f"{name(d.overtaking_vehicle_idx)} passed you", True)
        if code == "COLL" and d is not None and player in (d.vehicle1_idx, d.vehicle2_idx):
            other = d.vehicle2_idx if d.vehicle1_idx == player else d.vehicle1_idx
            return add(f"Contact with {name(other)}", True)
        if code == "SCAR" and d is not None and d.event_type == 1 and d.safety_car_type in (1, 2):
            text = f"{SC_NAMES[d.safety_car_type]} in this lap"
            return add(text, speech=text + ".")
        simple = {"RDFL": ("Red flag", "Red flag."), "CHQF": ("Chequered flag", None), "LGOT": ("Lights out", None),
                  "DRSE": ("DRS enabled", None), "DRSD": ("DRS disabled", None)}
        if code in simple:
            return add(*simple[code][:1], speech=simple[code][1])
        return None

    def on_safety_car(self, status: int | None, session_time: float, lap: int | None) -> RaceControlEntry | None:
        """Session packet safety-car status (every year has it): an entry on each change after the first reading."""
        prev, self._sc = self._sc, status
        if status is not None and status != prev:
            self._sc_changes.append((session_time, status))
        if prev is None or status is None or status == prev:
            return None
        if status in (1, 2):
            name = SC_NAMES[status]
            speech = "Safety car, safety car." if status == 1 else "Virtual safety car deployed."
            return self._add(session_time, lap, "SC", f"{name} deployed", speech=speech)
        if status == 0 and prev in (1, 2):
            return self._add(session_time, lap, "SC", f"{SC_NAMES[prev]} period over",
                             speech=f"{SC_NAMES[prev]} ending.")
        return None
