"""Deterministic synthetic telemetry for demo mode and tests. Never presented as F1 22 data.

A fictional 4.3 km "Demo Ring" is described as straights and corners. A speed profile
is built with braking/acceleration limits; throttle, brake, steering, gear and RPM are
derived from it, so the signals change coherently. Each lap applies small, seeded
driver variations (corner speeds, braking strength), so laps differ repeatably.

The demo is a 30-lap race against three fictional AI cars running the unvaried
profile at fixed pace factors, so race gaps, the track map and positions have data.

The simulator emits the same decoded Packet objects as the UDP decoder, so demo data
flows through exactly the same state/analysis path. Packets are tagged "demo" by the
service and never mixed with live data.
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

import numpy as np

from telemetry.packet_models import (
    MAX_CARS,
    CarDamageData,
    CarMotionData,
    CarSetupData,
    CarStatusData,
    CarTelemetryData,
    EventData,
    FastestLapDetails,
    LapData,
    LapHistoryData,
    Packet,
    PacketHeader,
    PacketId,
    ParticipantData,
    PenaltyDetails,
    SessionData,
    SessionHistory,
    SessionHistoryHeader,
    WeatherForecastSample,
)

TRACK_NAME = "Demo Ring (synthetic)"
DEMO_UID_BASE = 0xDE40_0000_0000
LOOPBACK_TRACK_NAME = "Loopback test (synthetic)"


def is_synthetic_uid(uid: int) -> bool:
    """Session UIDs in this range come from the simulator (demo mode or scripts/diagnostics.py send)."""
    return uid >> 32 == DEMO_UID_BASE >> 32
DT = 1 / 60
# (length m, apex km/h or None for straight, direction -1 left / +1 right)
LAYOUT = [
    (820, None, 0), (140, 95, 1), (300, None, 0), (220, 185, -1), (180, 150, 1), (520, None, 0),
    (160, 80, -1), (260, None, 0), (240, 230, 1), (200, 130, -1), (420, None, 0), (150, 110, 1),
    (180, 170, -1), (510, None, 0),
]
TRACK_LENGTH = sum(seg[0] for seg in LAYOUT)
GEAR_TOP_KPH = (0, 95, 130, 165, 200, 235, 268, 298, 340)  # upper speed of gears 1..8
DECEL = 38.0           # m/s^2 at full brake
TOP_SPEED_MS = 94.0
SCENARIOS = ("standard", "quick_lap", "interruption")
INTERRUPT_AT_S, INTERRUPT_FOR_S = 25.0, 6.0
RACE_LAPS = 30
# heading change per corner (degrees, + = right), solved offline so the loop closes without crossing itself
TURN_DEG = (135.3, -20.4, 79.5, -73.1, 161.2, -40.2, 125.7, -8.0)
# fictional AI cars: (name, race number, start offset from the player in metres, pace factor)
RIVALS = (("M. DUVAL", 7, 250.0, 1.003), ("T. OKAFOR", 21, 60.0, 0.99), ("L. BRANDT", 33, -60.0, 0.988))
DEMO_TEAMS = (8, 1, 2, 3)  # game team ids for the player and the three rivals, so the tower shows team colours
PIT_WINDOW = (14, 19, 4)  # ideal lap, latest lap, rejoin position (synthetic)
FORECAST_OFFSETS = (0, 5, 10, 15, 30, 45, 60)  # minutes ahead, as the game sends them


def demo_weather(t_s: float) -> tuple[int, int]:
    """(weather code, rain %) of the synthetic race at game time t: cloud, then overcast, rain from minute 30."""
    return (1, 5) if t_s < 900 else ((2, 25) if t_s < 1800 else (3, 60))


DEMO_SETUP = dict(front_wing=6, rear_wing=4, on_throttle=70, off_throttle=55, front_camber=-2.5, rear_camber=-1.0,
                  front_toe=0.05, rear_toe=0.2, front_suspension=5, rear_suspension=3, front_anti_roll_bar=5,
                  rear_anti_roll_bar=3, front_suspension_height=4, rear_suspension_height=6, brake_pressure=100,
                  brake_bias=56, rear_left_tyre_pressure=21.5, rear_right_tyre_pressure=21.5,
                  front_left_tyre_pressure=23.0, front_right_tyre_pressure=23.0, ballast=6, fuel_load=53.0)


def track_geometry() -> np.ndarray:
    """Per-metre (x, y, z) world positions of the Demo Ring; y is a gentle synthetic elevation."""
    heading, turns, h = 0.0, iter(TURN_DEG), []
    for length, apex, _ in LAYOUT:
        if apex is None:
            h += [heading] * length
            continue
        w = np.sin(np.linspace(0, np.pi, length))
        for step in np.radians(next(turns)) * w / w.sum():
            heading += step
            h.append(heading)
    h = np.array(h)
    x, z = np.cumsum(np.cos(h)), np.cumsum(np.sin(h))
    s = np.arange(TRACK_LENGTH) / TRACK_LENGTH
    x, z = x - x[-1] * s, z - z[-1] * s  # spread the few metres of closure error over the lap
    y = 6 * np.sin(2 * np.pi * s)
    return np.stack([x - x.mean(), y, z - z.mean()], 1)


GEOMETRY = track_geometry()


def position_at(d: float) -> tuple[float, float, float]:
    x, y, z = GEOMETRY[int(d) % TRACK_LENGTH]
    return float(x), float(y), float(z)


class _Rival:
    def __init__(self, offset_m: float, pace: float):
        self.total = offset_m
        self.pace = pace
        self.laps: list[tuple] = []        # (lap ms, S1, S2, S3, valid flags), as the game's history has them
        self._marks: list[float] | None = None  # times at the line, then at each sector end, this lap

    def step(self, v_prof, t: float) -> None:
        before = self.total
        self.total += float(v_prof[int(self.total) % TRACK_LENGTH]) * self.pace * DT
        if self.total < 0:
            return
        if before < 0 or self.total // TRACK_LENGTH > before // TRACK_LENGTH:  # crossed the line
            if self._marks is not None and len(self._marks) == 3:
                s1, s2 = self._marks[1] - self._marks[0], self._marks[2] - self._marks[1]
                ms = [round(x * 1000) for x in (s1, s2, t - self._marks[2])]
                self.laps.append((sum(ms), *ms, 0x0F))
            self._marks = [t]
        elif self._marks is not None:
            third = TRACK_LENGTH / 3
            if (self.total % TRACK_LENGTH) // third > (before % TRACK_LENGTH) // third:
                self._marks.append(t)


def history_record(car_idx: int, laps) -> SessionHistory:
    """The game's session-history record for one car: its laps and the lap numbers of its bests."""
    laps = laps[-100:]

    def best(i, bit):
        ok = [(lap[i], n) for n, lap in enumerate(laps, 1) if lap[4] & bit and lap[i]]
        return min(ok)[1] if ok else 0
    head = SessionHistoryHeader(car_idx, len(laps), 1, best(0, 1), best(1, 2), best(2, 4), best(3, 8))
    return SessionHistory(head, tuple(LapHistoryData(*lap) for lap in laps))


def _accel(v):
    return np.maximum(13.0 * (1 - v / TOP_SPEED_MS), 0.4)


def build_profile(lap_seed: int | None):
    """Per-metre arrays (speed m/s, steer, brake 0..1, throttle 0..1) for one lap."""
    rng = random.Random(lap_seed) if lap_seed is not None else None
    vmax, steer = [], []
    for length, apex, direction in LAYOUT:
        if apex is None:
            vmax += [TOP_SPEED_MS] * length
            steer += [0.0] * length
            continue
        a = apex + (rng.uniform(-6, 2.5) if rng else 0)
        vmax += [a / 3.6] * length
        mag = float(np.clip(45 / a, 0.08, 0.7))
        steer += list(direction * mag * np.sin(np.linspace(0, np.pi, length)))
    v = np.array(vmax * 2, float)  # two laps so the profile wraps around the start line
    decel = DECEL * (rng.uniform(0.9, 1.0) if rng else 1.0)
    for i in range(len(v) - 2, -1, -1):
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * decel))
    for i in range(1, len(v)):
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * _accel(v[i - 1])))
    v = v[TRACK_LENGTH:]
    acc = np.diff(np.append(v, v[0]) ** 2) / 2  # m/s^2 over 1 m
    brake = np.convolve(np.clip(-acc / decel, 0, 1), np.ones(9) / 9, mode="same")
    brake = np.where(brake > 0.03, np.clip(brake * 1.1, 0, 1), 0.0)
    throttle = np.where(acc > 0.3, np.clip(acc / _accel(v) + 0.1, 0, 1), 0.35)  # 35%: steady-state cornering
    throttle[v >= TOP_SPEED_MS - 0.5] = 1.0
    throttle[brake > 0] = 0.0
    return v, np.array(steer), brake, throttle


def gear_for(kph: float) -> int:
    for g in range(1, 9):
        if kph <= GEAR_TOP_KPH[g]:
            return g
    return 8


class DemoSimulator:
    def __init__(self, scenario: str = "standard", run_index: int = 0):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario}")
        self.scenario = scenario
        self.session_uid = DEMO_UID_BASE + run_index
        self.run_index = run_index
        self._start_race()

    def _start_race(self) -> None:
        self.t = 0.0
        self.frame = 0
        self.lap = 1
        self.lap_ms = 0.0
        self.d = 0.0
        self.last_lap_ms = 0
        self.s1 = self.s2 = 0
        self.invalid = False
        self.fuel = 53.0
        self.wear = 0.0
        self.history: list[tuple] = []  # the player's laps as the game's session history reports them
        self._events: list[EventData] = []  # race control messages due this frame
        self._fastest_ms: int | None = None
        self._profile = build_profile(self._seed())
        self._rival_profile = build_profile(None)[0]
        self._pending_start = True
        if self.scenario == "quick_lap":
            # begin at 85% of lap 1 so a lap completes within ~15 s
            v = self._profile[0]
            start = int(TRACK_LENGTH * 0.85)
            self.d = float(start)
            self.lap_ms = float(np.sum(1.0 / v[:start]) * 1000)
            self.s1, self.s2 = int(np.sum(1 / v[:TRACK_LENGTH // 3]) * 1000), int(
                np.sum(1 / v[TRACK_LENGTH // 3:2 * TRACK_LENGTH // 3]) * 1000)
        self.rivals = [_Rival(self.d + off, pace) for _, _, off, pace in RIVALS]

    @property
    def total_distance(self) -> float:
        return (self.lap - 1) * TRACK_LENGTH + self.d

    def _seed(self) -> int:
        return 1000 + self.lap + 7919 * self.run_index  # each demo run drives its own laps

    @property
    def interrupted(self) -> bool:
        return self.scenario == "interruption" and INTERRUPT_AT_S <= self.t % 120 < INTERRUPT_AT_S + INTERRUPT_FOR_S

    def _hdr(self, pid: PacketId) -> PacketHeader:
        return PacketHeader(packet_format=2022, game_major_version=0, game_minor_version=0, packet_version=1,
                            packet_id=int(pid), session_uid=self.session_uid, session_time=self.t,
                            frame_identifier=self.frame, player_car_index=0, secondary_player_car_index=255)

    def step(self) -> list[Packet]:
        """Advance 1/60 s. Returns the packets the 'game' would send this frame (none while interrupted)."""
        v_prof, steer_prof, brake_prof, thr_prof = self._profile
        i = int(self.d) % TRACK_LENGTH
        v = float(v_prof[i])
        self.t += DT
        self.frame += 1
        self.d += v * DT
        self.lap_ms += DT * 1000
        if not self.s1 and self.d >= TRACK_LENGTH / 3:
            self.s1 = int(self.lap_ms)
        if self.s1 and not self.s2 and self.d >= 2 * TRACK_LENGTH / 3:
            self.s2 = int(self.lap_ms) - self.s1
        if self.lap > RACE_LAPS:  # chequered flag: end the session and start the next race, as the game would
            finished = [Packet(self._hdr(PacketId.EVENT), PacketId.EVENT, EventData(code)) for code in ("CHQF", "SEND")]
            self.session_uid += 0x1_0000  # stays in the synthetic range
            self.scenario = "standard" if self.scenario == "quick_lap" else self.scenario
            self._start_race()
            return finished
        if self.lap % 4 == 0 and self.d > 0.6 * TRACK_LENGTH:
            self.invalid = True  # scripted track-limits infringement every 4th lap
        if self.d >= TRACK_LENGTH:
            over_s = (self.d - TRACK_LENGTH) / v
            self.last_lap_ms = int(self.lap_ms - over_s * 1000)
            s3 = self.last_lap_ms - self.s1 - self.s2
            self.history.append((self.last_lap_ms, self.s1, self.s2, s3, 0x0E if self.invalid else 0x0F))
            if not self.invalid:
                self._fastest_lap(0, self.last_lap_ms)
            self.lap += 1
            if self.lap == 2:
                self._events.append(EventData("DRSE"))
            elif self.lap == 3:  # a scripted penalty for a rival, so race control has something to show
                self._events.append(EventData("PENA", PenaltyDetails(4, 7, 2, 255, 5, 3, 0)))
            self.d -= TRACK_LENGTH
            self.lap_ms = over_s * 1000
            self.s1 = self.s2 = 0
            self.invalid = False
            self._profile = build_profile(self._seed())
        self.fuel = max(0.0, self.fuel - v * DT * 0.0004)
        self.wear += v * DT * 0.00035
        for car, r in enumerate(self.rivals, start=1):
            done = len(r.laps)
            r.step(self._rival_profile, self.t)
            if len(r.laps) > done:
                self._fastest_lap(car, r.laps[-1][0])

        if self.interrupted:
            return []
        out = []
        if self._pending_start:
            out.append(Packet(self._hdr(PacketId.EVENT), PacketId.EVENT, EventData("SSTA")))
        out += [Packet(self._hdr(PacketId.EVENT), PacketId.EVENT, e) for e in self._events]
        self._events = []
        if self._pending_start or self.frame % 300 == 1:  # the game repeats participants every 5 s
            self._pending_start = False
            people = [ParticipantData(name=b"DEMO DRIVER", race_number=99, team_id=DEMO_TEAMS[0])] + [
                ParticipantData(ai_controlled=1, name=name.encode(), race_number=num, team_id=team)
                for (name, num, _, _), team in zip(RIVALS, DEMO_TEAMS[1:], strict=True)]
            people += [ParticipantData()] * (MAX_CARS - len(people))
            out.append(Packet(self._hdr(PacketId.PARTICIPANTS), PacketId.PARTICIPANTS, people[0], tuple(people),
                              len(RIVALS) + 1))
        out.append(Packet(self._hdr(PacketId.CAR_TELEMETRY), PacketId.CAR_TELEMETRY, self._telemetry(i, v)))
        motion = [CarMotionData(*position_at(self.d))] + [CarMotionData(*position_at(r.total)) for r in self.rivals]
        motion += [CarMotionData()] * (MAX_CARS - len(motion))
        out.append(Packet(self._hdr(PacketId.MOTION), PacketId.MOTION, motion[0], tuple(motion)))
        cars = self._all_lap_data()
        out.append(Packet(self._hdr(PacketId.LAP_DATA), PacketId.LAP_DATA, cars[0], cars))
        if self.frame % 30 == 1:
            ideal, latest, rejoin = PIT_WINDOW
            ahead = ((m, *demo_weather(self.t + m * 60)) for m in FORECAST_OFFSETS)
            forecast = tuple(WeatherForecastSample(10, m, w, 31, 2, 24, 2, rain) for m, w, rain in ahead)
            out.append(Packet(self._hdr(PacketId.SESSION), PacketId.SESSION, SessionData(
                weather=demo_weather(self.t)[0], track_temperature=31, air_temperature=24, total_laps=RACE_LAPS,
                track_length=TRACK_LENGTH, session_type=10, track_id=-1, pit_window_ideal_lap=ideal,
                pit_window_latest_lap=latest, pit_rejoin_position=rejoin, forecast=forecast, forecast_accuracy=1)))
            laps_left = RACE_LAPS - self.lap + 1 - self.d / TRACK_LENGTH
            out.append(Packet(self._hdr(PacketId.CAR_STATUS), PacketId.CAR_STATUS, CarStatusData(
                fuel_in_tank=self.fuel, fuel_capacity=110.0, fuel_remaining_laps=self.fuel / 1.72 - laps_left,
                max_rpm=12000,
                idle_rpm=4000, max_gears=8, drs_allowed=1, actual_tyre_compound=18, visual_tyre_compound=17,
                tyres_age_laps=self.lap - 1, vehicle_fia_flags=0, ers_store_energy=3.2e6)))
        if self.frame % 15 == 7:  # the game cycles session history through the cars
            idx = (self.frame // 15) % (len(self.rivals) + 1)
            hist = history_record(idx, self.history if idx == 0 else self.rivals[idx - 1].laps)
            out.append(Packet(self._hdr(PacketId.SESSION_HISTORY), PacketId.SESSION_HISTORY, hist))
        if self.frame % 60 == 31:  # the game sends setups twice a second
            out.append(Packet(self._hdr(PacketId.CAR_SETUPS), PacketId.CAR_SETUPS, CarSetupData(**DEMO_SETUP)))
        if self.frame % 60 == 1:
            w = round(self.wear, 2)
            out.append(Packet(self._hdr(PacketId.CAR_DAMAGE), PacketId.CAR_DAMAGE,
                              CarDamageData(tyres_wear=(w * 1.1, w * 1.15, w * 0.9, w))))
        return out

    def _fastest_lap(self, car: int, ms: int) -> None:
        if self._fastest_ms is None or ms < self._fastest_ms:
            self._fastest_ms = ms
            self._events.append(EventData("FTLP", FastestLapDetails(car, ms / 1000)))

    def _telemetry(self, i: int, v: float) -> CarTelemetryData:
        _, steer_prof, brake_prof, thr_prof = self._profile
        kph = v * 3.6
        gear = gear_for(kph)
        lo, hi = GEAR_TOP_KPH[gear - 1] * 0.8, GEAR_TOP_KPH[gear]
        rpm = int(np.clip(5500 + 6300 * (kph - lo) / max(hi - lo, 1), 4000, 11950))
        brake, steer = float(brake_prof[i]), float(steer_prof[i])
        straight_long = self._on_long_straight(i)
        tyre = int(88 + 10 * abs(steer) + 6 * brake)
        return CarTelemetryData(
            speed=int(kph), throttle=float(thr_prof[i]), steer=steer, brake=brake, gear=gear, engine_rpm=rpm,
            drs=int(straight_long and self.lap > 1), rev_lights_percent=int(np.clip((rpm - 9000) / 30, 0, 100)),
            brakes_temperature=tuple(int(420 + 500 * brake) for _ in range(4)),
            tyres_surface_temperature=(tyre - 2, tyre - 1, tyre + 1, tyre + 2),
            tyres_inner_temperature=(97, 97, 99, 99), engine_temperature=108,
            tyres_pressure=(21.6, 21.6, 23.2, 23.2))

    def _on_long_straight(self, i: int) -> bool:
        pos = 0
        for length, apex, _ in LAYOUT:
            if pos <= i < pos + length:
                return apex is None and length >= 500 and pos + 150 <= i < pos + length - 120
            pos += length
        return False

    def _all_lap_data(self) -> tuple:
        totals = [self.total_distance] + [r.total for r in self.rivals]
        order = sorted(range(len(totals)), key=lambda i: -totals[i])
        pos = {idx: p + 1 for p, idx in enumerate(order)}
        cars = [self._lap_data(pos[0])]
        for i, r in enumerate(self.rivals, start=1):
            lap_d = r.total % TRACK_LENGTH if r.total >= 0 else r.total
            cars.append(LapData(lap_distance=float(lap_d), total_distance=float(r.total), car_position=pos[i],
                                current_lap_num=max(1, int(r.total // TRACK_LENGTH) + 1), driver_status=4,
                                result_status=2))
        return tuple(cars + [LapData()] * (MAX_CARS - len(cars)))

    def _lap_data(self, position: int = 1) -> LapData:
        return LapData(
            last_lap_time_ms=self.last_lap_ms, current_lap_time_ms=int(self.lap_ms), sector1_time_ms=self.s1,
            sector2_time_ms=self.s2, lap_distance=float(self.d), total_distance=float(self.total_distance),
            car_position=position, current_lap_num=self.lap, sector=0 if not self.s1 else (1 if not self.s2 else 2),
            current_lap_invalid=int(self.invalid), driver_status=1 if self.lap > 1 or self.scenario != "quick_lap"
            else 4, result_status=2)


class DemoRunner:
    """Runs a DemoSimulator on a background thread at real time x `speed`."""

    def __init__(self, on_packet: Callable[[Packet], None], scenario: str = "standard", speed: float = 1.0,
                 run_index: int = 0):
        self.sim = DemoSimulator(scenario, run_index)
        self.on_packet = on_packet
        self.speed = speed
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="demo-sim", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        owed, last = 0.0, time.perf_counter()
        while not self._stop.is_set():
            now = time.perf_counter()
            owed = min(owed + (now - last) * self.speed / DT, 60)  # cap catch-up after a stall
            last = now
            while owed >= 1:
                owed -= 1
                for p in self.sim.step():
                    self.on_packet(p)
            self._stop.wait(DT / 2)
