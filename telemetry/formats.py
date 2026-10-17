"""Wire layouts of the F1 22, F1 23, F1 24 and F1 25 UDP formats (2022 .. 2025).

Every year decodes into the same record dataclasses (telemetry/packet_models.py), so
nothing past the decoder knows which game is running. A year's layout is F1 22's,
edited where that year's specification differs: a field split into a milliseconds part
and a minutes part, a field added or widened, a name shortened. Bytes PurpleSector
doesn't use are skipped ("x"), never guessed at. Tests check every layout against the
packet sizes each specification publishes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, fields

from telemetry.packet_models import (
    PACKET_SIZES,
    SESSION_TYPES,
    SESSION_TYPES_2024,
    CarDamageData,
    CarMotionData,
    CarSetupData,
    CarStatusData,
    CarTelemetryData,
    LapData,
    LapHistoryData,
    PacketHeader,
    PacketId,
    ParticipantData,
    SessionData,
    SessionHistoryHeader,
    WeatherForecastSample,
    _binary_fields,
)

Item = tuple[str, str, int]  # (field name, struct code, count); code "x" = bytes skipped


def items_of(cls) -> list[Item]:
    """The F1 22 wire order of a record dataclass."""
    return [(fl.name, fl.metadata["fmt"], fl.metadata["count"]) for fl in _binary_fields(cls)]


def edit(items: list[Item], **swap: list[Item]) -> list[Item]:
    """Replace each named item with a list of items (which may include the original)."""
    out = []
    for it in items:
        out.extend(swap.pop(it[0], [it]))
    if swap:
        raise KeyError(f"no such fields: {', '.join(swap)}")
    return out


class Layout:
    """One record's binary layout. `minutes` maps a total-milliseconds field to the name of the
    minutes part that follows its milliseconds part on the wire (F1 23 on)."""

    def __init__(self, cls, items: list[Item], minutes: dict[str, str] | None = None):
        self.cls, self.items, self.minutes = cls, tuple(items), minutes or {}
        self.struct = struct.Struct("<" + "".join(f"{n}{f}" if n > 1 or f in "sx" else f for _, f, n in items))
        self.size = self.struct.size
        self._names = {fl.name for fl in fields(cls)}

    def unpack(self, data: bytes, offset: int = 0):
        values, out, i = self.struct.unpack_from(data, offset), {}, 0
        for name, f, n in self.items:
            if f == "x":
                continue
            if n == 1 or f == "s":
                out[name], i = values[i], i + 1
            else:
                out[name], i = tuple(values[i:i + n]), i + n
        for total, part in self.minutes.items():
            out[total] = out[total] + out.pop(part) * 60_000
        return self.cls(**{k: v for k, v in out.items() if k in self._names})

    def pack(self, obj) -> bytes:
        """Encode a record (tests and the loopback sender). Fields this layout has but the object
        doesn't carry are zero."""
        vals = {}
        for name, f, n in self.items:
            if f != "x":
                v = getattr(obj, name, None)
                vals[name] = (b"" if f == "s" else (0 if n == 1 else (0,) * n)) if v is None else v
        for total, part in self.minutes.items():
            vals[total], vals[part] = vals[total] % 60_000, vals[total] // 60_000
        flat = []
        for name, f, n in self.items:
            if f == "x":
                continue
            flat.extend(vals[name] if n > 1 and f != "s" else [vals[name]])
        return self.struct.pack(*flat)


@dataclass(frozen=True)
class Format:
    year: int
    header: Layout
    layouts: dict          # record class -> Layout
    sizes: dict            # PacketId -> total bytes
    forecast_slots: int    # weather forecast samples the session packet has room for
    session_types: dict
    lap_data_trailer: int = 2   # time-trial car indices after the 22 lap records

    @property
    def header_size(self) -> int:
        return self.header.size

    @property
    def session_safety_car_offset(self) -> int:
        return self.header_size + 19 + 21 * 5  # fixed fields, 21 marshal zones

    @property
    def session_forecast_offset(self) -> int:
        return self.session_safety_car_offset + 3  # after safety car, network game, sample count

    @property
    def session_pit_window_offset(self) -> int:
        # forecast samples, then forecast accuracy, AI difficulty, three uint32 link identifiers
        return self.session_forecast_offset + self.forecast_slots * 8 + 2 + 12


_BASE = {cls: items_of(cls) for cls in (PacketHeader, CarMotionData, CarTelemetryData, LapData, SessionData,
                                        ParticipantData, CarStatusData, CarDamageData, CarSetupData, LapHistoryData,
                                        SessionHistoryHeader, WeatherForecastSample)}
_SIMPLE = (CarMotionData, CarTelemetryData, SessionData, SessionHistoryHeader, WeatherForecastSample)


def _plain(*classes) -> dict:
    return {cls: Layout(cls, _BASE[cls]) for cls in classes}


def _ms_min(name: str, part: str) -> list[Item]:
    return [(name, "H", 1), (part, "B", 1)]


_HEADER_2023 = edit(_BASE[PacketHeader], packet_format=[("packet_format", "H", 1), ("game_year", "B", 1)],
                    frame_identifier=[("frame_identifier", "I", 1), ("overall_frame_identifier", "I", 1)])
_LAP_2023 = edit(_BASE[LapData],
                 sector1_time_ms=_ms_min("sector1_time_ms", "s1_min"),
                 sector2_time_ms=_ms_min("sector2_time_ms", "s2_min")
                 + [("delta_to_car_in_front_ms", "H", 1), ("delta_to_race_leader_ms", "H", 1)],
                 warnings=[("warnings", "B", 1), ("corner_cutting_warnings", "x", 1)])
_LAP_2024 = edit(_BASE[LapData],
                 sector1_time_ms=_ms_min("sector1_time_ms", "s1_min"),
                 sector2_time_ms=_ms_min("sector2_time_ms", "s2_min")
                 + _ms_min("delta_to_car_in_front_ms", "front_min") + _ms_min("delta_to_race_leader_ms", "leader_min"),
                 warnings=[("warnings", "B", 1), ("corner_cutting_warnings", "x", 1)],
                 pit_stop_should_serve_pen=[("pit_stop_should_serve_pen", "B", 1), ("speed_trap", "x", 5)])
_SECTOR_MINUTES = {"sector1_time_ms": "s1_min", "sector2_time_ms": "s2_min"}
_HISTORY_LAP_2023 = edit(_BASE[LapHistoryData], **{f"sector{i}_time_ms": _ms_min(f"sector{i}_time_ms", f"s{i}_min")
                                                   for i in (1, 2, 3)})
_STATUS_2023 = edit(_BASE[CarStatusData], vehicle_fia_flags=[("vehicle_fia_flags", "b", 1), ("engine_power", "x", 8)])
_PARTICIPANT_2023 = edit(_BASE[ParticipantData],
                         your_telemetry=[("your_telemetry", "B", 1), ("online_names_platform", "x", 2)])
_PARTICIPANT_2024 = edit(_BASE[ParticipantData],
                         your_telemetry=[("your_telemetry", "B", 1), ("online_names_tech_platform", "x", 4)])
_PARTICIPANT_2025 = edit(_BASE[ParticipantData], name=[("name", "s", 32)],
                         your_telemetry=[("your_telemetry", "B", 1), ("online_tech_platform_livery", "x", 17)])
_SETUP_2024 = edit(_BASE[CarSetupData], brake_bias=[("brake_bias", "B", 1), ("engine_braking", "B", 1)])
_DAMAGE_2025 = edit(_BASE[CarDamageData], brakes_damage=[("brakes_damage", "B", 4), ("tyre_blisters", "x", 4)])


def _newer(header, lap, lap_minutes, history, status, participant, setup, damage) -> dict:
    return {**_plain(*_SIMPLE), LapData: Layout(LapData, lap, lap_minutes),
            LapHistoryData: Layout(LapHistoryData, history, {f"sector{i}_time_ms": f"s{i}_min" for i in (1, 2, 3)}),
            CarStatusData: Layout(CarStatusData, status), ParticipantData: Layout(ParticipantData, participant),
            CarSetupData: Layout(CarSetupData, setup), CarDamageData: Layout(CarDamageData, damage),
            PacketHeader: header}


_H22, _H23 = Layout(PacketHeader, _BASE[PacketHeader]), Layout(PacketHeader, _HEADER_2023)
_SIZES_2023 = [1349, 644, 1131, 45, 1306, 1107, 1352, 1239, 1020, 1218, 953, 1460, 231, 217]
_SIZES_2024 = [1349, 753, 1285, 45, 1350, 1133, 1352, 1239, 1020, 1306, 953, 1460, 231, 237, 101]
_SIZES_2025 = [1349, 753, 1285, 45, 1284, 1133, 1352, 1239, 1042, 954, 1041, 1460, 231, 273, 101, 1131]
_LAP_MIN_2024 = {**_SECTOR_MINUTES, "delta_to_car_in_front_ms": "front_min", "delta_to_race_leader_ms": "leader_min"}

FORMATS = {
    2022: Format(2022, _H22, {**_plain(*_BASE), PacketHeader: _H22}, dict(PACKET_SIZES), 56, SESSION_TYPES),
    2023: Format(2023, _H23, _newer(_H23, _LAP_2023, _SECTOR_MINUTES, _HISTORY_LAP_2023, _STATUS_2023,
                                    _PARTICIPANT_2023, _BASE[CarSetupData], _BASE[CarDamageData]),
                 {PacketId(i): n for i, n in enumerate(_SIZES_2023)}, 56, SESSION_TYPES),
    2024: Format(2024, _H23, _newer(_H23, _LAP_2024, _LAP_MIN_2024, _HISTORY_LAP_2023, _STATUS_2023,
                                    _PARTICIPANT_2024, _SETUP_2024, _BASE[CarDamageData]),
                 {PacketId(i): n for i, n in enumerate(_SIZES_2024)}, 64, SESSION_TYPES_2024),
    2025: Format(2025, _H23, _newer(_H23, _LAP_2024, _LAP_MIN_2024, _HISTORY_LAP_2023, _STATUS_2023,
                                    _PARTICIPANT_2025, _SETUP_2024, _DAMAGE_2025),
                 {PacketId(i): n for i, n in enumerate(_SIZES_2025)}, 64, SESSION_TYPES_2024),
}
SUPPORTED_FORMATS = tuple(FORMATS)
