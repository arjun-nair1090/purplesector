"""Decoded record models for the F1 UDP telemetry formats, with F1 22's 2022 layouts.

Layouts follow the official "F1 22 UDP Specification": little-endian, packed structs,
22 cars per array. Each record dataclass carries its 2022 binary layout in field
metadata, so the decoder and the test/diagnostic packet builder share one definition.
telemetry/formats.py derives the F1 23, F1 24 and F1 25 layouts from these; fields
after the wire fields are filled by the decoder (or only exist in newer formats).
Tests assert the resulting sizes against each spec's published totals.

Wheel arrays in F1 22 are ordered RL, RR, FL, FR.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field, fields
from enum import IntEnum
from functools import cache


def _f(fmt: str, count: int = 1):
    default = b"" if fmt == "s" else (0 if count == 1 else (0,) * count)
    return field(default=default, metadata={"fmt": fmt, "count": count})


@cache
def _binary_fields(cls) -> tuple:
    """Fields that are part of the wire layout (must precede any non-wire fields)."""
    return tuple(fl for fl in fields(cls) if "fmt" in fl.metadata)


@cache
def record_struct(cls) -> struct.Struct:
    parts = []
    for fl in _binary_fields(cls):
        fmt, n = fl.metadata["fmt"], fl.metadata["count"]
        parts.append(f"{n}{fmt}" if n > 1 or fmt == "s" else fmt)
    return struct.Struct("<" + "".join(parts))


def unpack_record(cls, data: bytes, offset: int = 0):
    values = record_struct(cls).unpack_from(data, offset)
    out, i = [], 0
    for fl in _binary_fields(cls):
        n = fl.metadata["count"]
        if n == 1 or fl.metadata["fmt"] == "s":
            out.append(values[i])
            i += 1
        else:
            out.append(tuple(values[i:i + n]))
            i += n
    return cls(*out)


def pack_record(obj) -> bytes:
    flat = []
    for fl in _binary_fields(type(obj)):
        v = getattr(obj, fl.name)
        if fl.metadata["count"] > 1 and fl.metadata["fmt"] != "s":
            flat.extend(v)
        else:
            flat.append(v)
    return record_struct(type(obj)).pack(*flat)


class PacketId(IntEnum):
    MOTION = 0
    SESSION = 1
    LAP_DATA = 2
    EVENT = 3
    PARTICIPANTS = 4
    CAR_SETUPS = 5
    CAR_TELEMETRY = 6
    CAR_STATUS = 7
    FINAL_CLASSIFICATION = 8
    LOBBY_INFO = 9
    CAR_DAMAGE = 10
    SESSION_HISTORY = 11
    TYRE_SETS = 12        # F1 23 on
    MOTION_EX = 13        # F1 23 on
    TIME_TRIAL = 14       # F1 24 on
    LAP_POSITIONS = 15    # F1 25 on


# Total packet sizes (bytes) published in the F1 22 specification (other years: telemetry/formats.py).
PACKET_SIZES = {
    PacketId.MOTION: 1464,
    PacketId.SESSION: 632,
    PacketId.LAP_DATA: 972,
    PacketId.EVENT: 40,
    PacketId.PARTICIPANTS: 1257,
    PacketId.CAR_SETUPS: 1102,
    PacketId.CAR_TELEMETRY: 1347,
    PacketId.CAR_STATUS: 1058,
    PacketId.FINAL_CLASSIFICATION: 1015,
    PacketId.LOBBY_INFO: 1191,
    PacketId.CAR_DAMAGE: 948,
    PacketId.SESSION_HISTORY: 1155,
}
MAX_CARS = 22


@dataclass(slots=True, frozen=True)
class PacketHeader:
    packet_format: int = _f("H")          # 2022
    game_major_version: int = _f("B")
    game_minor_version: int = _f("B")
    packet_version: int = _f("B")
    packet_id: int = _f("B")
    session_uid: int = _f("Q")
    session_time: float = _f("f")         # seconds
    frame_identifier: int = _f("I")
    player_car_index: int = _f("B")
    secondary_player_car_index: int = _f("B")  # 255 if no second player
    game_year: int = 0                    # F1 23 on (e.g. 24)
    overall_frame_identifier: int = 0     # F1 23 on; doesn't go back after a flashback


@dataclass(slots=True, frozen=True)
class CarMotionData:
    """Per-car motion record. The spec does not say which world axis is vertical; the track map picks
    the two axes with the largest spread instead of assuming one."""
    world_position_x: float = _f("f")     # metres
    world_position_y: float = _f("f")
    world_position_z: float = _f("f")
    world_velocity: tuple = _f("f", 3)
    world_forward_dir: tuple = _f("h", 3)  # normalised * 32767
    world_right_dir: tuple = _f("h", 3)
    g_force_lateral: float = _f("f")
    g_force_longitudinal: float = _f("f")
    g_force_vertical: float = _f("f")
    yaw: float = _f("f")                  # radians
    pitch: float = _f("f")
    roll: float = _f("f")

    @property
    def position(self) -> tuple:
        return self.world_position_x, self.world_position_y, self.world_position_z


@dataclass(slots=True, frozen=True)
class CarTelemetryData:
    speed: int = _f("H")                  # km/h
    throttle: float = _f("f")             # 0.0 .. 1.0
    steer: float = _f("f")                # -1.0 (full left) .. 1.0 (full right); input, not wheel angle
    brake: float = _f("f")                # 0.0 .. 1.0
    clutch: int = _f("B")                 # 0 .. 100
    gear: int = _f("b")                   # -1 R, 0 N, 1..8
    engine_rpm: int = _f("H")
    drs: int = _f("B")                    # 0 off, 1 on
    rev_lights_percent: int = _f("B")
    rev_lights_bit_value: int = _f("H")
    brakes_temperature: tuple = _f("H", 4)        # °C
    tyres_surface_temperature: tuple = _f("B", 4)  # °C
    tyres_inner_temperature: tuple = _f("B", 4)    # °C
    engine_temperature: int = _f("H")              # °C
    tyres_pressure: tuple = _f("f", 4)             # PSI
    surface_type: tuple = _f("B", 4)


@dataclass(slots=True, frozen=True)
class CarTelemetryTrailer:
    mfd_panel_index: int = _f("B")
    mfd_panel_index_secondary_player: int = _f("B")
    suggested_gear: int = _f("b")         # 0 if no suggestion


@dataclass(slots=True, frozen=True)
class LapData:
    last_lap_time_ms: int = _f("I")
    current_lap_time_ms: int = _f("I")
    sector1_time_ms: int = _f("H")        # 0 until sector 1 is complete this lap
    sector2_time_ms: int = _f("H")
    lap_distance: float = _f("f")         # metres; negative before crossing the line
    total_distance: float = _f("f")
    safety_car_delta: float = _f("f")
    car_position: int = _f("B")
    current_lap_num: int = _f("B")
    pit_status: int = _f("B")             # 0 none, 1 pitting, 2 in pit area
    num_pit_stops: int = _f("B")
    sector: int = _f("B")                 # 0 = sector 1, 1 = sector 2, 2 = sector 3
    current_lap_invalid: int = _f("B")    # 0 valid, 1 invalid
    penalties: int = _f("B")              # seconds
    warnings: int = _f("B")
    num_unserved_drive_through_pens: int = _f("B")
    num_unserved_stop_go_pens: int = _f("B")
    grid_position: int = _f("B")
    driver_status: int = _f("B")          # 0 garage, 1 flying lap, 2 in lap, 3 out lap, 4 on track
    result_status: int = _f("B")
    pit_lane_timer_active: int = _f("B")
    pit_lane_time_in_lane_ms: int = _f("H")
    pit_stop_timer_ms: int = _f("H")
    pit_stop_should_serve_pen: int = _f("B")
    delta_to_car_in_front_ms: int | None = None   # game-reported, F1 23 on; None when not sent
    delta_to_race_leader_ms: int | None = None


@dataclass(slots=True, frozen=True)
class SessionData:
    """Leading fixed fields of the session packet (bytes 24..42)."""
    weather: int = _f("B")
    track_temperature: int = _f("b")      # °C
    air_temperature: int = _f("b")        # °C
    total_laps: int = _f("B")
    track_length: int = _f("H")           # metres
    session_type: int = _f("B")
    track_id: int = _f("b")               # -1 unknown
    formula: int = _f("B")
    session_time_left: int = _f("H")      # seconds
    session_duration: int = _f("H")
    pit_speed_limit: int = _f("B")
    game_paused: int = _f("B")
    is_spectating: int = _f("B")
    spectator_car_index: int = _f("B")
    sli_pro_native_support: int = _f("B")
    num_marshal_zones: int = _f("B")
    # decoded separately, from fixed offsets further into the packet
    safety_car_status: int = 0
    pit_window_ideal_lap: int = 0         # game's strategy for the player; 0 = not provided
    pit_window_latest_lap: int = 0
    pit_rejoin_position: int = 0
    session_type_name: str | None = None  # session_type looked up in that year's table (numbering changed in F1 24)
    forecast: tuple = ()                  # WeatherForecastSample, as many as the game says it sent
    forecast_accuracy: int | None = None  # 0 perfect, 1 approximate


@dataclass(slots=True, frozen=True)
class WeatherForecastSample:
    session_type: int = _f("B")           # same numbering as SessionData.session_type of that year
    time_offset_min: int = _f("B")        # minutes from now
    weather: int = _f("B")
    track_temperature: int = _f("b")
    track_temperature_change: int = _f("b")  # 0 up, 1 down, 2 no change
    air_temperature: int = _f("b")
    air_temperature_change: int = _f("b")
    rain_percentage: int = _f("B")


# Byte offset of m_safetyCarStatus from the packet start: header + 19 fixed bytes + 21*5 marshal zones.
SESSION_SAFETY_CAR_OFFSET = 24 + 19 + 21 * 5
# m_pitStopWindowIdealLap: after safety car, network game, forecast count, 56*8 forecast samples,
# forecast accuracy, AI difficulty and three uint32 link identifiers. Followed by latest lap, rejoin position.
SESSION_PIT_WINDOW_OFFSET = SESSION_SAFETY_CAR_OFFSET + 3 + 56 * 8 + 2 + 12  # 613


@dataclass(slots=True, frozen=True)
class CarSetupData:
    front_wing: int = _f("B")
    rear_wing: int = _f("B")
    on_throttle: int = _f("B")            # differential, %
    off_throttle: int = _f("B")
    front_camber: float = _f("f")
    rear_camber: float = _f("f")
    front_toe: float = _f("f")
    rear_toe: float = _f("f")
    front_suspension: int = _f("B")
    rear_suspension: int = _f("B")
    front_anti_roll_bar: int = _f("B")
    rear_anti_roll_bar: int = _f("B")
    front_suspension_height: int = _f("B")
    rear_suspension_height: int = _f("B")
    brake_pressure: int = _f("B")         # %
    brake_bias: int = _f("B")             # %
    rear_left_tyre_pressure: float = _f("f")   # PSI
    rear_right_tyre_pressure: float = _f("f")
    front_left_tyre_pressure: float = _f("f")
    front_right_tyre_pressure: float = _f("f")
    ballast: int = _f("B")
    fuel_load: float = _f("f")            # kg
    engine_braking: int | None = None     # %, F1 24 on


@dataclass(slots=True, frozen=True)
class ParticipantData:
    ai_controlled: int = _f("B")
    driver_id: int = _f("B")
    network_id: int = _f("B")
    team_id: int = _f("B")
    my_team: int = _f("B")
    race_number: int = _f("B")
    nationality: int = _f("B")
    name: bytes = _f("s", 48)             # UTF-8, null terminated
    your_telemetry: int = _f("B")


@dataclass(slots=True, frozen=True)
class CarStatusData:
    traction_control: int = _f("B")
    anti_lock_brakes: int = _f("B")
    fuel_mix: int = _f("B")
    front_brake_bias: int = _f("B")
    pit_limiter_status: int = _f("B")
    fuel_in_tank: float = _f("f")         # kg
    fuel_capacity: float = _f("f")        # kg
    fuel_remaining_laps: float = _f("f")  # game's own estimate
    max_rpm: int = _f("H")
    idle_rpm: int = _f("H")
    max_gears: int = _f("B")
    drs_allowed: int = _f("B")
    drs_activation_distance: int = _f("H")
    actual_tyre_compound: int = _f("B")
    visual_tyre_compound: int = _f("B")
    tyres_age_laps: int = _f("B")
    vehicle_fia_flags: int = _f("b")      # -1 invalid, 0 none, 1 green, 2 blue, 3 yellow, 4 red
    ers_store_energy: float = _f("f")     # joules
    ers_deploy_mode: int = _f("B")
    ers_harvested_this_lap_mguk: float = _f("f")
    ers_harvested_this_lap_mguh: float = _f("f")
    ers_deployed_this_lap: float = _f("f")
    network_paused: int = _f("B")


@dataclass(slots=True, frozen=True)
class CarDamageData:
    tyres_wear: tuple = _f("f", 4)        # percent
    tyres_damage: tuple = _f("B", 4)
    brakes_damage: tuple = _f("B", 4)
    front_left_wing_damage: int = _f("B")
    front_right_wing_damage: int = _f("B")
    rear_wing_damage: int = _f("B")
    floor_damage: int = _f("B")
    diffuser_damage: int = _f("B")
    sidepod_damage: int = _f("B")
    drs_fault: int = _f("B")
    ers_fault: int = _f("B")
    gear_box_damage: int = _f("B")
    engine_damage: int = _f("B")
    engine_mguh_wear: int = _f("B")
    engine_es_wear: int = _f("B")
    engine_ce_wear: int = _f("B")
    engine_ice_wear: int = _f("B")
    engine_mguk_wear: int = _f("B")
    engine_tc_wear: int = _f("B")
    engine_blown: int = _f("B")
    engine_seized: int = _f("B")


@dataclass(slots=True, frozen=True)
class SessionHistoryHeader:
    car_idx: int = _f("B")
    num_laps: int = _f("B")
    num_tyre_stints: int = _f("B")
    best_lap_time_lap_num: int = _f("B")
    best_sector1_lap_num: int = _f("B")
    best_sector2_lap_num: int = _f("B")
    best_sector3_lap_num: int = _f("B")


@dataclass(slots=True, frozen=True)
class LapHistoryData:
    lap_time_ms: int = _f("I")
    sector1_time_ms: int = _f("H")
    sector2_time_ms: int = _f("H")
    sector3_time_ms: int = _f("H")
    lap_valid_bit_flags: int = _f("B")    # 0x01 lap, 0x02 S1, 0x04 S2, 0x08 S3 valid


@dataclass(slots=True, frozen=True)
class SessionHistory:
    header: SessionHistoryHeader
    laps: tuple                           # LapHistoryData for laps 1..num_laps


@dataclass(slots=True, frozen=True)
class FastestLapDetails:
    vehicle_idx: int = _f("B")
    lap_time: float = _f("f")             # seconds


@dataclass(slots=True, frozen=True)
class FlashbackDetails:
    flashback_frame_identifier: int = _f("I")
    flashback_session_time: float = _f("f")


@dataclass(slots=True, frozen=True)
class ButtonDetails:
    button_status: int = _f("I")          # bit flags of the buttons held; see udp_action_bit()


@dataclass(slots=True, frozen=True)
class PenaltyDetails:
    penalty_type: int = _f("B")           # PENALTY_TYPES
    infringement_type: int = _f("B")      # INFRINGEMENTS
    vehicle_idx: int = _f("B")
    other_vehicle_idx: int = _f("B")      # 255 when nobody else was involved
    time: int = _f("B")                   # seconds; 255 when not a time penalty
    lap_num: int = _f("B")
    places_gained: int = _f("B")


@dataclass(slots=True, frozen=True)
class VehicleDetails:
    """RTMT, TMPT, RCWN, DTSV, SGSV: one car index (F1 25 appends a reason or stop time, not read)."""
    vehicle_idx: int = _f("B")


@dataclass(slots=True, frozen=True)
class OvertakeDetails:                    # OVTK, F1 23 on
    overtaking_vehicle_idx: int = _f("B")
    being_overtaken_vehicle_idx: int = _f("B")


@dataclass(slots=True, frozen=True)
class SafetyCarDetails:                   # SCAR, F1 24 on
    safety_car_type: int = _f("B")        # 0 none, 1 full, 2 virtual, 3 formation lap
    event_type: int = _f("B")             # 0 deployed, 1 returning, 2 returned, 3 resume race


@dataclass(slots=True, frozen=True)
class CollisionDetails:                   # COLL, F1 24 on
    vehicle1_idx: int = _f("B")
    vehicle2_idx: int = _f("B")


EVENT_DETAILS = {"FTLP": FastestLapDetails, "FLBK": FlashbackDetails, "BUTN": ButtonDetails,
                 "PENA": PenaltyDetails, "RTMT": VehicleDetails, "TMPT": VehicleDetails, "RCWN": VehicleDetails,
                 "DTSV": VehicleDetails, "SGSV": VehicleDetails, "OVTK": OvertakeDetails,
                 "SCAR": SafetyCarDetails, "COLL": CollisionDetails}


def udp_action_bit(n: int) -> int:
    """Bit for the game's assignable "UDP Action n" button (1..12): 0x00100000 .. 0x80000000."""
    return 0x0010_0000 << (n - 1)


@dataclass(slots=True, frozen=True)
class EventData:
    code: str                             # 4-char event string code, e.g. "SSTA"
    details: object = None


HISTORY_MAX_LAPS = 100
SESSION_HISTORY_LAPS_OFFSET = 24 + 7


@dataclass(slots=True, frozen=True)
class Packet:
    """A validated, decoded packet. `data` holds the player-car record (or event/history).
    For lap data, motion and participants, `cars` holds all 22 records (index = car index)."""
    header: PacketHeader
    packet_id: PacketId
    data: object
    cars: tuple | None = None
    num_active_cars: int | None = None    # participants packet only


# ---- Lookup tables (F1 22 appendices) ----

SESSION_TYPES = {
    0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
    5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short Qualifying",
    9: "One-Shot Qualifying", 10: "Race", 11: "Race 2", 12: "Race 3", 13: "Time Trial",
}
# F1 24 and F1 25 renumbered sessions to add the sprint shootout
SESSION_TYPES_2024 = {
    **{k: v for k, v in SESSION_TYPES.items() if k <= 9}, 10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
    12: "Sprint Shootout 3", 13: "Short Sprint Shootout", 14: "One-Shot Sprint Shootout", 15: "Race",
    16: "Race 2", 17: "Race 3", 18: "Time Trial",
}
TRACKS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Sakhir (Bahrain)", 4: "Catalunya",
    5: "Monaco", 6: "Montreal", 7: "Silverstone", 8: "Hockenheim", 9: "Hungaroring", 10: "Spa",
    11: "Monza", 12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Texas", 16: "Brazil",
    17: "Austria", 18: "Sochi", 19: "Mexico", 20: "Baku (Azerbaijan)", 21: "Sakhir Short",
    22: "Silverstone Short", 23: "Texas Short", 24: "Suzuka Short", 25: "Hanoi",
    26: "Zandvoort", 27: "Imola", 28: "Portimão", 29: "Jeddah", 30: "Miami", 31: "Las Vegas", 32: "Losail",
    39: "Silverstone (Reverse)", 40: "Austria (Reverse)", 41: "Zandvoort (Reverse)",
}
WEATHER = {0: "Clear", 1: "Light cloud", 2: "Overcast", 3: "Light rain", 4: "Heavy rain", 5: "Storm"}
ACTUAL_COMPOUNDS = {
    16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 7: "Inter", 8: "Wet",
    9: "Dry (classic)", 10: "Wet (classic)", 11: "Super soft (F2)", 12: "Soft (F2)",
    13: "Medium (F2)", 14: "Hard (F2)", 15: "Wet (F2)",
}
VISUAL_COMPOUNDS = {16: "Soft", 17: "Medium", 18: "Hard", 7: "Inter", 8: "Wet",
                    15: "Wet", 19: "Super soft", 20: "Soft", 21: "Medium", 22: "Hard"}
RESULT_ACTIVE = 2  # lap data m_resultStatus: 0 invalid, 1 inactive, 2 active, 3 finished, 4+ out
RESULT_FINISHED = 3
PIT_STATUS = {0: "On track", 1: "Pitting", 2: "In pit area"}
DRIVER_STATUS = {0: "In garage", 1: "Flying lap", 2: "In lap", 3: "Out lap", 4: "On track"}
SAFETY_CAR = {0: "None", 1: "Safety car", 2: "Virtual safety car", 3: "Formation lap"}
FIA_FLAGS = {-1: "Unknown", 0: "None", 1: "Green", 2: "Blue", 3: "Yellow", 4: "Red"}
EVENT_CODES = {
    "SSTA": "Session started", "SEND": "Session ended", "FTLP": "Fastest lap",
    "RTMT": "Retirement", "DRSE": "DRS enabled", "DRSD": "DRS disabled",
    "TMPT": "Team mate in pits", "CHQF": "Chequered flag", "RCWN": "Race winner",
    "PENA": "Penalty issued", "SPTP": "Speed trap", "STLG": "Start lights",
    "LGOT": "Lights out", "DTSV": "Drive-through served", "SGSV": "Stop-go served",
    "FLBK": "Flashback", "BUTN": "Button status", "RDFL": "Red flag", "OVTK": "Overtake",
    "SCAR": "Safety car", "COLL": "Collision",
}
PENALTY_TYPES = (
    "Drive-through", "Stop-go", "Grid penalty", "Penalty reminder", "Time penalty", "Warning", "Disqualified",
    "Removed from formation lap", "Parked too long timer", "Tyre regulations", "This lap invalidated",
    "This and next lap invalidated", "This lap invalidated without reason",
    "This and next lap invalidated without reason", "This and previous lap invalidated",
    "This and previous lap invalidated without reason", "Retired", "Black flag timer",
)
INFRINGEMENTS = (
    "Blocking by slow driving", "Blocking by wrong-way driving", "Reversing off the start line", "Big collision",
    "Small collision", "Collision, failed to hand back position", "Collision, failed to hand back positions",
    "Corner cutting, gained time", "Corner cutting overtake", "Corner cutting overtakes", "Crossed pit exit lane",
    "Ignoring blue flags", "Ignoring yellow flags", "Ignoring drive-through", "Too many drive-throughs",
    "Drive-through reminder, serve within laps", "Drive-through reminder, serve this lap", "Pit lane speeding",
    "Parked for too long", "Ignoring tyre regulations", "Too many penalties", "Multiple warnings",
    "Approaching disqualification", "Tyre regulations, select single", "Tyre regulations, select multiple",
    "Lap invalidated, corner cutting", "Lap invalidated, running wide", "Corner cutting, ran wide, minor gain",
    "Corner cutting, ran wide, significant gain", "Corner cutting, ran wide, extreme gain",
    "Lap invalidated, wall riding", "Lap invalidated, flashback used", "Lap invalidated, reset to track",
    "Blocking the pit lane", "Jump start", "Safety car to car collision", "Safety car illegal overtake",
    "Safety car, exceeding allowed pace", "Virtual safety car, exceeding allowed pace",
    "Formation lap below allowed speed", "Formation lap parking", "Retired, mechanical failure",
    "Retired, terminally damaged", "Safety car, falling too far back", "Black flag timer",
    "Unserved stop-go penalty", "Unserved drive-through penalty", "Engine component change", "Gearbox change",
    "Parc fermé change", "League grid penalty", "Retry penalty", "Illegal time gain", "Mandatory pit stop",
    "Attribute assigned",
)
