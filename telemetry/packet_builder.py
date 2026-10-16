"""Builds full-size, spec-conformant F1 22 packets from records.

Used by the test suite and by scripts/diagnostics.py to send loopback packets.
Never used on the live path. The player's record goes at `player_car_index`;
all other cars are zero-filled.
"""
from __future__ import annotations

from telemetry.decoder import HEADER_SIZE
from telemetry.packet_models import (
    MAX_CARS,
    PACKET_SIZES,
    SESSION_HISTORY_LAPS_OFFSET,
    SESSION_PIT_WINDOW_OFFSET,
    SESSION_SAFETY_CAR_OFFSET,
    PacketHeader,
    PacketId,
    pack_record,
    record_struct,
)


def header(packet_id: PacketId, *, session_uid=0xC0FFEE, session_time=0.0, frame=0, player=0, fmt=2022) -> PacketHeader:
    return PacketHeader(packet_format=fmt, game_major_version=1, game_minor_version=18, packet_version=1,
                        packet_id=int(packet_id), session_uid=session_uid, session_time=session_time,
                        frame_identifier=frame, player_car_index=player, secondary_player_car_index=255)


def build_packet(hdr: PacketHeader, record=None, *, prefix: bytes = b"", tail: bytes = b"") -> bytes:
    """Per-car packets: header + prefix + 22 records (player's filled in) + tail, padded to spec size."""
    pid = PacketId(hdr.packet_id)
    buf = bytearray(PACKET_SIZES[pid])
    buf[:HEADER_SIZE] = pack_record(hdr)
    pos = HEADER_SIZE
    buf[pos:pos + len(prefix)] = prefix
    pos += len(prefix)
    if record is not None:
        size = record_struct(type(record)).size
        start = pos + hdr.player_car_index * size
        buf[start:start + size] = pack_record(record)
        pos += MAX_CARS * size
    buf[pos:pos + len(tail)] = tail
    return bytes(buf)


def build_cars(hdr: PacketHeader, records: dict, *, prefix: bytes = b"") -> bytes:
    """Per-car packets with several cars filled in: {car index: record}."""
    buf = bytearray(build_packet(hdr, prefix=prefix))
    for idx, rec in records.items():
        raw = pack_record(rec)
        start = HEADER_SIZE + len(prefix) + idx * len(raw)
        buf[start:start + len(raw)] = raw
    return bytes(buf)


def build_session(hdr: PacketHeader, session) -> bytes:
    buf = bytearray(build_packet(hdr))
    raw = pack_record(session)
    buf[HEADER_SIZE:HEADER_SIZE + len(raw)] = raw
    buf[SESSION_SAFETY_CAR_OFFSET] = session.safety_car_status
    o = SESSION_PIT_WINDOW_OFFSET
    buf[o:o + 3] = bytes((session.pit_window_ideal_lap, session.pit_window_latest_lap, session.pit_rejoin_position))
    return bytes(buf)


def build_event(hdr: PacketHeader, code: str, details=None) -> bytes:
    buf = bytearray(build_packet(hdr))
    buf[HEADER_SIZE:HEADER_SIZE + 4] = code.encode("ascii")
    if details is not None:
        raw = pack_record(details)
        buf[HEADER_SIZE + 4:HEADER_SIZE + 4 + len(raw)] = raw
    return bytes(buf)


def build_history(hdr: PacketHeader, hist_header, laps) -> bytes:
    buf = bytearray(build_packet(hdr))
    raw = pack_record(hist_header)
    buf[HEADER_SIZE:HEADER_SIZE + len(raw)] = raw
    for i, lap in enumerate(laps):
        r = pack_record(lap)
        o = SESSION_HISTORY_LAPS_OFFSET + i * len(r)
        buf[o:o + len(r)] = r
    return bytes(buf)

