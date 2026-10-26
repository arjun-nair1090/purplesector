"""The calls a race engineer makes without being asked, from what the game reports during a race.

At the line: laps to go, a car within a second either side, the gaps every few laps, tyre
wear passing 50 and 70 percent, the fuel margin going short (and recovering), and the
game's pit window opening. Between the lines: places gained or lost once they've settled,
blue and yellow flags, and fresh wing damage. At the flag: where you finished.

Every call comes from game data or gaps measured from it; nothing is a guess. The line
calls are gathered into one message per lap, the way an engineer bundles them, rather
than a string of separate ones.
"""
from __future__ import annotations

from analysis.coach import Category, CoachEvent, Confidence, Priority, gap_trend, worst_tyre
from analysis.radio_speech import pick, say_gap, say_name, say_signed, with_name

BLUE, YELLOW = 2, 3          # the game's FIA flag codes
WITHIN_S = 1.0               # a car this close at the line is a battle (and in DRS range where it's enabled)
FUEL_SHORT_LAPS = -0.2       # below this margin the fuel won't reach the flag at your measured burn


class RaceRadio:
    def __init__(self, settle_s: float = 3.0, flag_cooldown_s: float = 30.0, wing_step_pct: int = 15,
                 wear_steps: tuple = (50, 70)):
        self.settle_s, self.flag_cooldown_s = settle_s, flag_cooldown_s
        self.wing_step_pct, self.wear_steps = wing_step_pct, wear_steps
        self.reset()

    def reset(self) -> None:
        self._pos = self._pos_said = self._pos_since = None
        self._flag = None
        self._flag_said: dict[int, float] = {}
        self._wings: list | None = None
        self._wear_said = 0
        self._fuel_short = False
        self._pit_said: set[str] = set()
        self._battle_said: dict[tuple, int] = {}  # (side, car) -> lap last called

    # ---------------- between the lines ----------------
    def on_progress(self, snap) -> list[CoachEvent]:
        t = snap.session_time_s
        if t is None:
            return []
        out = [self._position(snap, t), self._flag_call(snap.fia_flag, t)]
        out += self._damage(snap.wing_damage_pct)
        return [e for e in out if e is not None]

    def _position(self, snap, t: float) -> CoachEvent | None:
        p = snap.position
        if not p or snap.pit_status:  # places shuffle while you're in the pit lane: call it once you're out
            return None
        if p != self._pos:
            self._pos, self._pos_since = p, t
        if t - self._pos_since < self.settle_s:
            return None
        if self._pos_said is None:  # the first settled position is where you are, not a change
            self._pos_said = p
            return None
        was, self._pos_said = self._pos_said, p
        if p == was:
            return None
        n = abs(p - was)
        if p < was:
            speech = (pick(f"Nice move, P{p}.", f"Good job, that's P{p}.", f"Up to P{p}.") if n == 1
                      else f"Up {n} places, P{p}.")
        else:
            speech = pick(f"Lost the place, P{p}.", f"Down to P{p}.") if n == 1 else f"Down {n} places, P{p}."
        return CoachEvent(f"position:{t:.0f}", Category.RACE, f"P{was} → P{p}",
                          Priority.MEDIUM, Confidence.HIGH, f"Game race position, held for {self.settle_s:.0f} s",
                          speech=speech)

    def _flag_call(self, flag: int | None, t: float) -> CoachEvent | None:
        if flag == self._flag:
            return None
        self._flag = flag
        if flag not in (BLUE, YELLOW) or t - self._flag_said.get(flag, -1e9) < self.flag_cooldown_s:
            return None
        self._flag_said[flag] = t
        if flag == BLUE:
            msg, speech = "Blue flag", pick("Blue flag. Let the car behind through.", "Blue flags, you're being lapped.")
        else:
            msg, speech = "Yellow flag", pick("Yellow flag, yellow flag. Careful.", "Yellow flag in this sector.")
        return CoachEvent(f"flag:{flag}:{t:.0f}", Category.RACE_CONTROL, msg, Priority.HIGH, Confidence.HIGH,
                          "Game FIA flag for your car", speech=speech)

    def _damage(self, wings: tuple | None) -> list[CoachEvent]:
        if not wings:
            return []
        if self._wings is None:  # damage that was there when we joined isn't news
            self._wings = list(wings)
            return []
        out = []
        for i, (now, said) in enumerate(zip(wings, self._wings, strict=True)):
            if now - said < self.wing_step_pct:
                continue
            self._wings[i] = now
            part = ("Front wing, left side", "Front wing, right side", "Rear wing")[i]
            out.append(CoachEvent(f"damage:{i}:{now}", Category.RACE, f"{part} damage {now}%", Priority.HIGH,
                                  Confidence.HIGH, "Game car damage packet",
                                  speech=f"{part.replace(',', '')} damage, {now} percent."))
        return out

    # ---------------- at the line ----------------
    def on_line(self, completed: int, snap, gaps: dict, fuel_margin: float | None, every: int,
                name: str | None = None) -> CoachEvent | None:
        """One message for the lap just completed, or None when there's nothing worth saying."""
        total = snap.total_laps
        left = total - completed if total else None
        if left == 0:
            return self.on_finish(snap.position, name)
        said, shown, high = [], [], False

        if left == 1:
            said.append(pick("Last lap.", "Final lap, bring it home."))
            shown.append("Final lap")
            high = True
        elif left == 5 or (left == 10 and total >= 20):
            said.append(f"{left} laps to go.")
            shown.append(f"{left} laps to go")

        routine = every > 0 and completed % every == 0
        if routine and snap.position:
            said.append(f"P{snap.position}.")
            shown.append(f"P{snap.position}")
        for side in ("ahead", "behind"):
            g = gaps.get(side)
            if g is None or g.gap_s is None:
                continue
            who = say_name(g.name)
            if g.gap_s < WITHIN_S:
                last = self._battle_said.get((side, g.car_index))
                if last is not None and completed - last < 2:
                    continue  # still the same fight: not every lap
                self._battle_said[(side, g.car_index)] = completed
                said.append(f"{who} {say_gap(g.gap_s)} {side}, within a second"
                            + (", and closing." if side == "behind" and (g.change_per_lap_s or 0) < -0.05 else "."))
                shown.append(f"{g.name} {g.gap_s:.1f} s {side}")
            elif routine:
                said.append(f"Gap {side} {g.gap_s:.1f} to {who}{gap_trend(g.change_per_lap_s)}.")
                shown.append(f"{g.name} {g.gap_s:.1f} s {side}")

        worst = worst_tyre(snap.tyre_wear_pct)
        if worst is not None:
            crossed = [s for s in self.wear_steps if worst[1] >= s > self._wear_said]
            if crossed:
                self._wear_said = crossed[-1]
                said.append(f"Tyres at {worst[1]:.0f} percent, {worst[0]} the worst.")
                shown.append(f"Tyre wear {worst[1]:.0f}% ({worst[0]})")

        if fuel_margin is not None:
            if fuel_margin < FUEL_SHORT_LAPS and not self._fuel_short:
                self._fuel_short, high = True, True
                said.append(f"Fuel is {say_signed(fuel_margin)} laps. We need to save.")
                shown.append(f"Fuel {fuel_margin:+.1f} laps: short")
            elif fuel_margin >= 0 and self._fuel_short:
                self._fuel_short = False
                said.append("Fuel's back on target.")
                shown.append("Fuel back on target")

        new_lap = completed + 1
        for key, lap, text in (("ideal", snap.pit_window_ideal_lap, "Pit window's open, ideal lap is this one."),
                               ("latest", snap.pit_window_latest_lap, "Last lap of the pit window.")):
            if lap and new_lap == lap and key not in self._pit_said:
                self._pit_said.add(key)
                said.append(text)
                shown.append(text.rstrip("."))

        if not said:
            return None
        return CoachEvent(f"race_line:{completed}", Category.RACE, f"Lap {completed}: " + "; ".join(shown),
                          Priority.HIGH if high else Priority.MEDIUM, Confidence.HIGH,
                          "Game lap data, race position, fuel, tyre wear and pit window; gaps measured from each "
                          "car's distance trace", speech=" ".join(said))

    def on_finish(self, position: int | None, name: str | None = None) -> CoachEvent:
        where = f" P{position}." if position else ""
        speech = with_name(pick(f"Chequered flag.{where} Good job.", f"That's the flag.{where} Well driven."), name)
        return CoachEvent("finish", Category.RACE, f"Finished{' P' + str(position) if position else ''}",
                          Priority.HIGH, Confidence.HIGH, "Game race position at the flag", speech=speech)
