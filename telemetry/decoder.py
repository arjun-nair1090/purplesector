"""Validating decoder for F1 22 (format 2022) UDP packets.

Every packet is checked for: minimum header length, packet format, known packet id,
full spec length for its id, and a valid player-car index. Anything else raises a
PacketError subclass so the receiver can count it without treating it as telemetry.
"""
from __future__ import annotations

from dataclasses import replace

from telemetry.packet_models import (
    HISTORY_MAX_LAPS,
    MAX_CARS,
    PACKET_SIZES,
    SESSION_HISTORY_LAPS_OFFSET,
    SESSION_PIT_WINDOW_OFFSET,
    SESSION_SAFETY_CAR_OFFSET,
    ButtonDetails,
    CarDamageData,
    CarMotionData,
    CarStatusData,
    CarTelemetryData,
    EventData,
    FastestLapDetails,
    FlashbackDetails,
    LapData,
    LapHistoryData,
    Packet,
    PacketHeader,
    PacketId,
    ParticipantData,
    SessionData,
    SessionHistory,
    SessionHistoryHeader,
    record_struct,
    unpack_record,
)

HEADER_SIZE = record_struct(PacketHeader).size  # 24


class PacketError(Exception):
    """Base for every reason a datagram is not accepted as telemetry."""


class TruncatedPacketError(PacketError):
    pass


class UnsupportedFormatError(PacketError):
    def __init__(self, packet_format: int):
        super().__init__(f"unsupported packet format {packet_format}")
        self.packet_format = packet_format


class IgnoredPacketError(PacketError):
    """Valid F1 22 packet type that PurpleSector does not use (setups, lobby info, ...)."""


class MalformedPacketError(PacketError):
    pass


def decode_header(data: bytes) -> PacketHeader:
    if len(data) < HEADER_SIZE:
        raise TruncatedPacketError(f"{len(data)} bytes is shorter than the {HEADER_SIZE}-byte header")
    return unpack_record(PacketHeader, data)


def _player_record(cls, data: bytes, header: PacketHeader, prefix: int = 0):
    """Decode only the player's entry of a 22-car array that starts `prefix` bytes after the header."""
    idx = header.player_car_index
    if idx >= MAX_CARS:
        raise MalformedPacketError(f"player car index {idx} out of range")
    return unpack_record(cls, data, HEADER_SIZE + prefix + idx * record_struct(cls).size)


def _all_cars(cls, data: bytes, prefix: int = 0) -> tuple:
    size = record_struct(cls).size
    return tuple(unpack_record(cls, data, HEADER_SIZE + prefix + i * size) for i in range(MAX_CARS))


def _session(data: bytes, header: PacketHeader) -> SessionData:
    s = unpack_record(SessionData, data, HEADER_SIZE)
    o = SESSION_PIT_WINDOW_OFFSET
    return replace(s, safety_car_status=data[SESSION_SAFETY_CAR_OFFSET], pit_window_ideal_lap=data[o],
                   pit_window_latest_lap=data[o + 1], pit_rejoin_position=data[o + 2])


def _event(data: bytes, header: PacketHeader) -> EventData:
    raw = data[HEADER_SIZE:HEADER_SIZE + 4]
    try:
        code = raw.decode("ascii")
    except UnicodeDecodeError:
        raise MalformedPacketError(f"non-ASCII event code {raw!r}") from None
    if not code.isalnum():
        raise MalformedPacketError(f"invalid event code {code!r}")
    details = None
    if code == "FTLP":
        details = unpack_record(FastestLapDetails, data, HEADER_SIZE + 4)
    elif code == "FLBK":
        details = unpack_record(FlashbackDetails, data, HEADER_SIZE + 4)
    elif code == "BUTN":
        details = unpack_record(ButtonDetails, data, HEADER_SIZE + 4)
    return EventData(code, details)


def _history(data: bytes, header: PacketHeader) -> SessionHistory:
    h = unpack_record(SessionHistoryHeader, data, HEADER_SIZE)
    if h.car_idx >= MAX_CARS:
        raise MalformedPacketError(f"history car index {h.car_idx} out of range")
    size = record_struct(LapHistoryData).size
    n = min(h.num_laps, HISTORY_MAX_LAPS)
    laps = tuple(unpack_record(LapHistoryData, data, SESSION_HISTORY_LAPS_OFFSET + i * size) for i in range(n))
    return SessionHistory(h, laps)


_DECODERS = {
    PacketId.SESSION: _session,
    PacketId.EVENT: _event,
    PacketId.CAR_TELEMETRY: lambda d, h: _player_record(CarTelemetryData, d, h),
    PacketId.CAR_STATUS: lambda d, h: _player_record(CarStatusData, d, h),
    PacketId.CAR_DAMAGE: lambda d, h: _player_record(CarDamageData, d, h),
    PacketId.SESSION_HISTORY: _history,
}

# packets whose every car is kept (gaps, track map dots, names): class, bytes before the car array
_ALL_CARS = {
    PacketId.LAP_DATA: (LapData, 0),
    PacketId.MOTION: (CarMotionData, 0),
    PacketId.PARTICIPANTS: (ParticipantData, 1),  # after m_numActiveCars
}


def decode_packet(data: bytes, supported_formats: tuple[int, ...] = (2022,)) -> Packet:
    header = decode_header(data)
    if header.packet_format not in supported_formats:
        raise UnsupportedFormatError(header.packet_format)
    try:
        pid = PacketId(header.packet_id)
    except ValueError:
        raise MalformedPacketError(f"unknown packet id {header.packet_id}") from None
    expected = PACKET_SIZES[pid]
    if len(data) < expected:
        raise TruncatedPacketError(f"{pid.name} packet is {len(data)} bytes, expected {expected}")
    if pid in _ALL_CARS:
        if header.player_car_index >= MAX_CARS:
            raise MalformedPacketError(f"player car index {header.player_car_index} out of range")
        cls, prefix = _ALL_CARS[pid]
        cars = _all_cars(cls, data, prefix)
        n = data[HEADER_SIZE] if pid == PacketId.PARTICIPANTS else None
        return Packet(header, pid, cars[header.player_car_index], cars, n)
    decoder = _DECODERS.get(pid)
    if decoder is None:
        raise IgnoredPacketError(pid.name)
    return Packet(header, pid, decoder(data, header))
