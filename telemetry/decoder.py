"""Validating decoder for the F1 22, F1 23, F1 24 and F1 25 UDP formats (2022 .. 2025).

Every packet is checked for: minimum header length, a supported packet format, a known
packet id for that year, the full spec length for its id, and a valid player-car index.
Anything else raises a PacketError subclass so the receiver can count it without
treating it as telemetry. Each year's layouts live in telemetry/formats.py; every year
decodes into the same records.
"""
from __future__ import annotations

from dataclasses import replace

from telemetry.formats import FORMATS, SUPPORTED_FORMATS, Format
from telemetry.packet_models import (
    EVENT_DETAILS,
    HISTORY_MAX_LAPS,
    MAX_CARS,
    CarDamageData,
    CarMotionData,
    CarSetupData,
    CarStatusData,
    CarTelemetryData,
    EventData,
    LapData,
    LapHistoryData,
    Packet,
    PacketHeader,
    PacketId,
    ParticipantData,
    SessionData,
    SessionHistory,
    SessionHistoryHeader,
    WeatherForecastSample,
    unpack_record,
)

HEADER_SIZE = FORMATS[2022].header_size  # 24; F1 23 on: 29


class PacketError(Exception):
    """Base for every reason a datagram is not accepted as telemetry."""


class TruncatedPacketError(PacketError):
    pass


class UnsupportedFormatError(PacketError):
    def __init__(self, packet_format: int):
        super().__init__(f"unsupported packet format {packet_format}")
        self.packet_format = packet_format


class IgnoredPacketError(PacketError):
    """Valid packet type that PurpleSector does not use (lobby info, tyre sets, ...)."""


class MalformedPacketError(PacketError):
    pass


def _format_of(data: bytes) -> int:
    if len(data) < 2:
        raise TruncatedPacketError(f"{len(data)} bytes is shorter than a packet header")
    return int.from_bytes(data[:2], "little")


def decode_header(data: bytes) -> PacketHeader:
    f = FORMATS.get(_format_of(data), FORMATS[2022])
    if len(data) < f.header_size:
        raise TruncatedPacketError(f"{len(data)} bytes is shorter than the {f.header_size}-byte header")
    return f.header.unpack(data)


def _player(cls, data: bytes, f: Format, header: PacketHeader, prefix: int = 0):
    """Decode only the player's entry of a 22-car array that starts `prefix` bytes after the header."""
    lay = f.layouts[cls]
    return lay.unpack(data, f.header_size + prefix + header.player_car_index * lay.size)


def _all_cars(cls, data: bytes, f: Format, prefix: int = 0) -> tuple:
    lay = f.layouts[cls]
    start = f.header_size + prefix
    return tuple(lay.unpack(data, start + i * lay.size) for i in range(MAX_CARS))


def _session(data: bytes, f: Format, header) -> SessionData:
    s = f.layouts[SessionData].unpack(data, f.header_size)
    sc, fo, pit = f.session_safety_car_offset, f.session_forecast_offset, f.session_pit_window_offset
    lay = f.layouts[WeatherForecastSample]
    n = min(data[sc + 2], f.forecast_slots)
    forecast = tuple(lay.unpack(data, fo + i * lay.size) for i in range(n))
    return replace(s, safety_car_status=data[sc], pit_window_ideal_lap=data[pit], pit_window_latest_lap=data[pit + 1],
                   pit_rejoin_position=data[pit + 2], forecast=forecast, forecast_accuracy=data[pit - 14],
                   session_type_name=f.session_types.get(s.session_type, f"Session type {s.session_type}"))


def _event(data: bytes, f: Format, header) -> EventData:
    o = f.header_size
    raw = data[o:o + 4]
    try:
        code = raw.decode("ascii")
    except UnicodeDecodeError:
        raise MalformedPacketError(f"non-ASCII event code {raw!r}") from None
    if not code.isalnum():
        raise MalformedPacketError(f"invalid event code {code!r}")
    cls = EVENT_DETAILS.get(code)  # event details have one layout in every year
    return EventData(code, unpack_record(cls, data, o + 4) if cls else None)


def _history(data: bytes, f: Format, header) -> SessionHistory:
    o = f.header_size
    h = f.layouts[SessionHistoryHeader].unpack(data, o)
    if h.car_idx >= MAX_CARS:
        raise MalformedPacketError(f"history car index {h.car_idx} out of range")
    lay = f.layouts[LapHistoryData]
    n = min(h.num_laps, HISTORY_MAX_LAPS)
    laps = tuple(lay.unpack(data, o + 7 + i * lay.size) for i in range(n))
    return SessionHistory(h, laps)


_DECODERS = {
    PacketId.SESSION: _session,
    PacketId.EVENT: _event,
    PacketId.CAR_TELEMETRY: lambda d, f, h: _player(CarTelemetryData, d, f, h),
    PacketId.CAR_STATUS: lambda d, f, h: _player(CarStatusData, d, f, h),
    PacketId.CAR_DAMAGE: lambda d, f, h: _player(CarDamageData, d, f, h),
    PacketId.CAR_SETUPS: lambda d, f, h: _player(CarSetupData, d, f, h),
    PacketId.SESSION_HISTORY: _history,
}

# packets whose every car is kept (gaps, track map dots, names): class, bytes before the car array
_ALL_CARS = {
    PacketId.LAP_DATA: (LapData, 0),
    PacketId.MOTION: (CarMotionData, 0),
    PacketId.PARTICIPANTS: (ParticipantData, 1),  # after m_numActiveCars
}
_NO_PLAYER = (PacketId.SESSION, PacketId.EVENT, PacketId.SESSION_HISTORY)  # nothing indexed by the player car


def decode_packet(data: bytes, supported_formats: tuple[int, ...] = SUPPORTED_FORMATS) -> Packet:
    fmt = _format_of(data)
    if fmt not in supported_formats or fmt not in FORMATS:
        if len(data) < HEADER_SIZE:
            raise TruncatedPacketError(f"{len(data)} bytes is shorter than a packet header")
        raise UnsupportedFormatError(fmt)
    f = FORMATS[fmt]
    header = decode_header(data)
    try:
        pid = PacketId(header.packet_id)
        expected = f.sizes[pid]
    except (ValueError, KeyError):
        raise MalformedPacketError(f"unknown packet id {header.packet_id} for format {fmt}") from None
    if len(data) < expected:
        raise TruncatedPacketError(f"{pid.name} packet is {len(data)} bytes, expected {expected}")
    decoder = _DECODERS.get(pid)
    if decoder is None and pid not in _ALL_CARS:
        raise IgnoredPacketError(pid.name)
    if pid not in _NO_PLAYER and header.player_car_index >= MAX_CARS:
        raise MalformedPacketError(f"player car index {header.player_car_index} out of range")
    if pid in _ALL_CARS:
        cls, prefix = _ALL_CARS[pid]
        cars = _all_cars(cls, data, f, prefix)
        n = data[f.header_size] if pid == PacketId.PARTICIPANTS else None
        return Packet(header, pid, cars[header.player_car_index], cars, n)
    return Packet(header, pid, decoder(data, f, header))
