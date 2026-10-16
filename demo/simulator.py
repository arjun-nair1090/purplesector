"""Deterministic synthetic telemetry for demo mode and tests. Never presented as F1 22 data.

A fictional 4.3 km "Demo Ring" is described as straights and corners. A speed profile
is built with braking/acceleration limits; throttle, brake, steering, gear and RPM are
derived from it, so the signals change coherently. Each lap applies small, seeded
driver variations (corner speeds, braking strength), so laps differ repeatably.

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
    CarDamageData,
    CarStatusData,
    CarTelemetryData,
    EventData,
    LapData,
    Packet,
    PacketHeader,
    PacketId,
    ParticipantData,
    SessionData,
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
        self.t = 0.0
        self.frame = 0
        self.lap = 1
        self.lap_ms = 0.0
        self.d = 0.0
        self.last_lap_ms = 0
        self.s1 = self.s2 = 0
        self.invalid = False
        self.fuel = 40.0
        self.wear = 0.0
        self._profile = build_profile(self._seed())
        self._pending_start = True
        if scenario == "quick_lap":
            # begin at 85% of lap 1 so a lap completes within ~15 s
            v = self._profile[0]
            start = int(TRACK_LENGTH * 0.85)
            self.d = float(start)
            self.lap_ms = float(np.sum(1.0 / v[:start]) * 1000)
            self.s1, self.s2 = int(np.sum(1 / v[:TRACK_LENGTH // 3]) * 1000), int(
                np.sum(1 / v[TRACK_LENGTH // 3:2 * TRACK_LENGTH // 3]) * 1000)

    def _seed(self) -> int:
        return 1000 + self.lap

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
        if self.lap % 4 == 0 and self.d > 0.6 * TRACK_LENGTH:
            self.invalid = True  # scripted track-limits infringement every 4th lap
        if self.d >= TRACK_LENGTH:
            over_s = (self.d - TRACK_LENGTH) / v
            self.last_lap_ms = int(self.lap_ms - over_s * 1000)
            self.lap += 1
            self.d -= TRACK_LENGTH
            self.lap_ms = over_s * 1000
            self.s1 = self.s2 = 0
            self.invalid = False
            self._profile = build_profile(self._seed())
        self.fuel = max(0.0, self.fuel - v * DT * 0.0004)
        self.wear += v * DT * 0.00002

        if self.interrupted:
            return []
        out = []
        if self._pending_start:
            self._pending_start = False
            out.append(Packet(self._hdr(PacketId.EVENT), PacketId.EVENT, EventData("SSTA")))
            out.append(Packet(self._hdr(PacketId.PARTICIPANTS), PacketId.PARTICIPANTS,
                              ParticipantData(name=b"DEMO DRIVER", race_number=99)))
        out.append(Packet(self._hdr(PacketId.CAR_TELEMETRY), PacketId.CAR_TELEMETRY, self._telemetry(i, v)))
        out.append(Packet(self._hdr(PacketId.LAP_DATA), PacketId.LAP_DATA, self._lap_data()))
        if self.frame % 30 == 1:
            out.append(Packet(self._hdr(PacketId.SESSION), PacketId.SESSION, SessionData(
                weather=1, track_temperature=31, air_temperature=24, total_laps=0, track_length=TRACK_LENGTH,
                session_type=1, track_id=-1)))
            out.append(Packet(self._hdr(PacketId.CAR_STATUS), PacketId.CAR_STATUS, CarStatusData(
                fuel_in_tank=self.fuel, fuel_capacity=110.0, fuel_remaining_laps=self.fuel / 1.7, max_rpm=12000,
                idle_rpm=4000, max_gears=8, drs_allowed=1, actual_tyre_compound=18, visual_tyre_compound=17,
                tyres_age_laps=self.lap - 1, vehicle_fia_flags=0, ers_store_energy=3.2e6)))
        if self.frame % 60 == 1:
            w = round(self.wear, 2)
            out.append(Packet(self._hdr(PacketId.CAR_DAMAGE), PacketId.CAR_DAMAGE,
                              CarDamageData(tyres_wear=(w * 1.1, w * 1.15, w * 0.9, w))))
        return out

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

    def _lap_data(self) -> LapData:
        return LapData(
            last_lap_time_ms=self.last_lap_ms, current_lap_time_ms=int(self.lap_ms), sector1_time_ms=self.s1,
            sector2_time_ms=self.s2, lap_distance=float(self.d), total_distance=float((self.lap - 1) * TRACK_LENGTH
                                                                                     + self.d),
            car_position=1, current_lap_num=self.lap, sector=0 if not self.s1 else (1 if not self.s2 else 2),
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
