"""Thread-safe latest-value store: the normalized telemetry model the UI reads.

Every field is None until the game (or the demo simulator, labelled source="demo")
actually provides it. Units are in the field names.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace

from config.defaults import TELEMETRY_RATE_HZ
from telemetry.packet_models import (
    ACTUAL_COMPOUNDS,
    SESSION_TYPES,
    TRACKS,
    VISUAL_COMPOUNDS,
    WEATHER,
    Packet,
    PacketId,
)

WHEELS = ("RL", "RR", "FL", "FR")  # F1 22 wheel-array order


@dataclass
class Snapshot:
    source: str = "live"                  # "live" (F1 22 UDP) or "demo" (synthetic)
    session_uid: int | None = None
    game: str | None = None               # "F1 24", from the packet format (live only)
    session_time_s: float | None = None
    # car telemetry
    speed_kph: int | None = None
    throttle_pct: float | None = None
    brake_pct: float | None = None
    steer_input: float | None = None      # -1 full left .. +1 full right (input, not wheel angle)
    clutch_pct: int | None = None
    gear: int | None = None               # -1 R, 0 N
    rpm: int | None = None
    rev_lights_pct: int | None = None
    drs_active: bool | None = None
    engine_temp_c: int | None = None
    brake_temps_c: tuple | None = None    # RL, RR, FL, FR
    tyre_surface_temps_c: tuple | None = None
    tyre_inner_temps_c: tuple | None = None
    tyre_pressures_psi: tuple | None = None
    suggested_gear: int | None = None
    # lap data
    lap_number: int | None = None
    current_lap_ms: int | None = None
    last_lap_ms: int | None = None
    sector: int | None = None             # 1..3
    sector1_ms: int | None = None
    sector2_ms: int | None = None
    lap_distance_m: float | None = None
    total_distance_m: float | None = None
    position: int | None = None
    lap_invalid: bool | None = None
    pit_status: int | None = None
    driver_status: int | None = None
    penalties_s: int | None = None
    # session
    session_type: str | None = None
    track_id: int | None = None
    track_name: str | None = None
    track_length_m: int | None = None
    total_laps: int | None = None
    weather: str | None = None
    weather_code: int | None = None
    forecast: tuple = ()                  # game's forecast for this session, nearest first
    forecast_accuracy: int | None = None  # 0 perfect, 1 approximate
    track_temp_c: int | None = None
    air_temp_c: int | None = None
    game_paused: bool | None = None
    safety_car_status: int | None = None
    pit_window_ideal_lap: int | None = None   # game's strategy; None when the game sends 0
    pit_window_latest_lap: int | None = None
    pit_rejoin_position: int | None = None
    # car status
    fuel_kg: float | None = None
    fuel_capacity_kg: float | None = None
    fuel_laps_remaining: float | None = None  # game's estimate
    max_rpm: int | None = None
    drs_allowed: bool | None = None
    tyre_compound: str | None = None
    tyre_visual: str | None = None
    tyre_age_laps: int | None = None
    ers_store_mj: float | None = None
    fia_flag: int | None = None
    # damage
    tyre_wear_pct: tuple | None = None
    wing_damage_pct: tuple | None = None  # front-left, front-right, rear
    floor_damage_pct: int | None = None
    diffuser_damage_pct: int | None = None
    sidepod_damage_pct: int | None = None
    gearbox_damage_pct: int | None = None
    engine_damage_pct: int | None = None
    # participants / history
    player_name: str | None = None
    best_lap_ms_game: int | None = None   # from the game's session-history packet
    setup: dict | None = None             # player's car setup (car-setups packet), field -> value
    updated_at: dict = field(default_factory=dict)  # packet name -> time.time()


class TelemetryState:
    def __init__(self, history_window_s: int = 30):
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self.set_history_window(history_window_s)

    def set_history_window(self, seconds: int) -> None:
        with self._lock:
            self.history_window_s = seconds
            old = list(getattr(self, "_history", ()))
            # 50% headroom; the window itself is enforced by session time in history().
            self._history: deque[tuple] = deque(old, maxlen=int(seconds * TELEMETRY_RATE_HZ * 1.5))

    def reset(self, source: str) -> None:
        with self._lock:
            self._snap = Snapshot(source=source)
            self._history.clear()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return replace(self._snap, updated_at=dict(self._snap.updated_at))

    def history(self) -> list[tuple]:
        """(session_time_s, speed_kph, throttle_pct, brake_pct, steer_input, gear, rpm) within the window."""
        with self._lock:
            if not self._history:
                return []
            cutoff = self._history[-1][0] - self.history_window_s
            return [r for r in self._history if r[0] >= cutoff]

    def apply(self, packet: Packet) -> None:
        d = packet.data
        with self._lock:
            s = self._snap
            s.session_uid = packet.header.session_uid
            if s.source == "live":
                s.game = f"F1 {packet.header.packet_format % 100}"
            s.session_time_s = packet.header.session_time
            s.updated_at[packet.packet_id.name] = time.time()
            pid = packet.packet_id
            if pid == PacketId.CAR_TELEMETRY:
                s.speed_kph = d.speed
                s.throttle_pct = round(d.throttle * 100, 1)
                s.brake_pct = round(d.brake * 100, 1)
                s.steer_input = d.steer
                s.clutch_pct = d.clutch
                s.gear = d.gear
                s.rpm = d.engine_rpm
                s.rev_lights_pct = d.rev_lights_percent
                s.drs_active = bool(d.drs)
                s.engine_temp_c = d.engine_temperature
                s.brake_temps_c = d.brakes_temperature
                s.tyre_surface_temps_c = d.tyres_surface_temperature
                s.tyre_inner_temps_c = d.tyres_inner_temperature
                s.tyre_pressures_psi = tuple(round(p, 1) for p in d.tyres_pressure)
                self._history.append((packet.header.session_time, d.speed, s.throttle_pct, s.brake_pct,
                                      d.steer, d.gear, d.engine_rpm))
            elif pid == PacketId.LAP_DATA:
                s.lap_number = d.current_lap_num
                s.current_lap_ms = d.current_lap_time_ms
                s.last_lap_ms = d.last_lap_time_ms or None
                s.sector = d.sector + 1
                s.sector1_ms = d.sector1_time_ms or None
                s.sector2_ms = d.sector2_time_ms or None
                s.lap_distance_m = d.lap_distance
                s.total_distance_m = d.total_distance
                s.position = d.car_position or None
                s.lap_invalid = bool(d.current_lap_invalid)
                s.pit_status = d.pit_status
                s.driver_status = d.driver_status
                s.penalties_s = d.penalties
            elif pid == PacketId.SESSION:
                s.session_type = d.session_type_name or SESSION_TYPES.get(d.session_type, f"Type {d.session_type}")
                s.track_id = d.track_id
                if s.source == "live":
                    s.track_name = TRACKS.get(d.track_id, "Unknown track")
                s.track_length_m = d.track_length or None
                s.total_laps = d.total_laps or None
                s.weather = WEATHER.get(d.weather)
                s.weather_code = d.weather
                s.forecast = tuple(sorted((f for f in d.forecast if f.session_type == d.session_type),
                                          key=lambda f: f.time_offset_min))
                s.forecast_accuracy = d.forecast_accuracy
                s.track_temp_c = d.track_temperature
                s.air_temp_c = d.air_temperature
                s.game_paused = bool(d.game_paused)
                s.safety_car_status = d.safety_car_status
                s.pit_window_ideal_lap = d.pit_window_ideal_lap or None
                s.pit_window_latest_lap = d.pit_window_latest_lap or None
                s.pit_rejoin_position = d.pit_rejoin_position or None
            elif pid == PacketId.CAR_STATUS:
                s.fuel_kg = round(d.fuel_in_tank, 2)
                s.fuel_capacity_kg = round(d.fuel_capacity, 1)
                s.fuel_laps_remaining = round(d.fuel_remaining_laps, 2)
                s.max_rpm = d.max_rpm or None
                s.drs_allowed = bool(d.drs_allowed)
                c = d.actual_tyre_compound  # a code newer than our table is named, not dropped
                s.tyre_compound = ACTUAL_COMPOUNDS.get(c, f"Compound {c}") if c else None
                s.tyre_visual = VISUAL_COMPOUNDS.get(d.visual_tyre_compound)
                s.tyre_age_laps = d.tyres_age_laps
                s.ers_store_mj = round(d.ers_store_energy / 1e6, 2)
                s.fia_flag = d.vehicle_fia_flags
            elif pid == PacketId.CAR_DAMAGE:
                s.tyre_wear_pct = tuple(round(w, 1) for w in d.tyres_wear)
                s.wing_damage_pct = (d.front_left_wing_damage, d.front_right_wing_damage, d.rear_wing_damage)
                s.floor_damage_pct = d.floor_damage
                s.diffuser_damage_pct = d.diffuser_damage
                s.sidepod_damage_pct = d.sidepod_damage
                s.gearbox_damage_pct = d.gear_box_damage
                s.engine_damage_pct = d.engine_damage
            elif pid == PacketId.CAR_SETUPS:
                s.setup = {f: round(v, 3) if isinstance(v, float) else v
                           for f in d.__dataclass_fields__ if (v := getattr(d, f)) is not None}
            elif pid == PacketId.PARTICIPANTS:
                s.player_name = d.name.split(b"\0", 1)[0].decode("utf-8", "replace") or None
            elif pid == PacketId.SESSION_HISTORY:
                h = d.header
                if 1 <= h.best_lap_time_lap_num <= len(d.laps):
                    s.best_lap_ms_game = d.laps[h.best_lap_time_lap_num - 1].lap_time_ms or None

    def set_track_name(self, name: str) -> None:
        with self._lock:
            self._snap.track_name = name
