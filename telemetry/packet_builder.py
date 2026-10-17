"""Builds full-size, spec-conformant F1 22 .. F1 25 packets from records.

Used by the test suite and by scripts/diagnostics.py to send loopback packets.
Never used on the live path. The year comes from the header's packet_format (an
unsupported year is built with F1 22's layouts, so tests can produce one). The
player's record goes at `player_car_index`; all other cars are zero-filled.
"""
from __future__ import annotations

from telemetry.formats import FORMATS
from telemetry.packet_models import (
    MAX_CARS,
    PacketHeader,
    PacketId,
    SessionData,
    SessionHistoryHeader,
    WeatherForecastSample,
    pack_record,
)


def _fmt(hdr: PacketHeader):
    return FORMATS.get(hdr.packet_format, FORMATS[2022])


def header(packet_id: PacketId, *, session_uid=0xC0FFEE, session_time=0.0, frame=0, player=0, fmt=2022) -> PacketHeader:
    return PacketHeader(packet_format=fmt, game_major_version=1, game_minor_version=18, packet_version=1,
                        packet_id=int(packet_id), session_uid=session_uid, session_time=session_time,
                        frame_identifier=frame, player_car_index=player, secondary_player_car_index=255,
                        game_year=fmt % 100, overall_frame_identifier=frame)


def build_packet(hdr: PacketHeader, record=None, *, prefix: bytes = b"", tail: bytes = b"") -> bytes:
    """Per-car packets: header + prefix + 22 records (player's filled in) + tail, padded to spec size."""
    f = _fmt(hdr)
    pid = PacketId(hdr.packet_id)
    buf = bytearray(f.sizes.get(pid, 64))
    raw = f.header.pack(hdr)
    buf[:len(raw)] = raw
    pos = len(raw)
    buf[pos:pos + len(prefix)] = prefix
    pos += len(prefix)
    if record is not None:
        lay = f.layouts[type(record)]
        start = pos + hdr.player_car_index * lay.size
        buf[start:start + lay.size] = lay.pack(record)
        pos += MAX_CARS * lay.size
    buf[pos:pos + len(tail)] = tail
    return bytes(buf)


def build_cars(hdr: PacketHeader, records: dict, *, prefix: bytes = b"") -> bytes:
    """Per-car packets with several cars filled in: {car index: record}."""
    f = _fmt(hdr)
    buf = bytearray(build_packet(hdr, prefix=prefix))
    for idx, rec in records.items():
        lay = f.layouts[type(rec)]
        start = f.header_size + len(prefix) + idx * lay.size
        buf[start:start + lay.size] = lay.pack(rec)
    return bytes(buf)


def build_session(hdr: PacketHeader, session: SessionData) -> bytes:
    f = _fmt(hdr)
    buf = bytearray(build_packet(hdr))
    raw = f.layouts[SessionData].pack(session)
    buf[f.header_size:f.header_size + len(raw)] = raw
    sc, fo, pit = f.session_safety_car_offset, f.session_forecast_offset, f.session_pit_window_offset
    buf[sc] = session.safety_car_status
    buf[sc + 2] = len(session.forecast)
    lay = f.layouts[WeatherForecastSample]
    for i, sample in enumerate(session.forecast[:f.forecast_slots]):
        buf[fo + i * lay.size:fo + (i + 1) * lay.size] = lay.pack(sample)
    buf[pit - 14] = session.forecast_accuracy or 0
    buf[pit:pit + 3] = bytes((session.pit_window_ideal_lap, session.pit_window_latest_lap, session.pit_rejoin_position))
    return bytes(buf)


def build_event(hdr: PacketHeader, code: str, details=None) -> bytes:
    f = _fmt(hdr)
    buf = bytearray(build_packet(hdr))
    o = f.header_size
    buf[o:o + 4] = code.encode("ascii")
    if details is not None:
        raw = pack_record(details)
        buf[o + 4:o + 4 + len(raw)] = raw
    return bytes(buf)


def encode(p) -> bytes:
    """A decoded Packet back to wire bytes, as the game sends it (every car for per-car packets)."""
    if p.packet_id == PacketId.SESSION:
        return build_session(p.header, p.data)
    if p.packet_id == PacketId.EVENT:
        return build_event(p.header, p.data.code, p.data.details)
    if p.packet_id == PacketId.SESSION_HISTORY:
        return build_history(p.header, p.data.header, p.data.laps)
    if p.cars is not None:  # lap data, motion, participants
        prefix = bytes([p.num_active_cars or 0]) if p.packet_id == PacketId.PARTICIPANTS else b""
        return build_cars(p.header, dict(enumerate(p.cars)), prefix=prefix)
    return build_packet(p.header, p.data)


def build_history(hdr: PacketHeader, hist_header: SessionHistoryHeader, laps) -> bytes:
    f = _fmt(hdr)
    buf = bytearray(build_packet(hdr))
    o = f.header_size
    buf[o:o + 7] = f.layouts[SessionHistoryHeader].pack(hist_header)
    lay = f.layouts[type(laps[0])] if laps else None
    for i, lap in enumerate(laps):
        buf[o + 7 + i * lay.size:o + 7 + (i + 1) * lay.size] = lay.pack(lap)
    return bytes(buf)
