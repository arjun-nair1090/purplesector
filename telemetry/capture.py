"""Raw packet capture, for checking PurpleSector against a real game session.

Every datagram the receiver gets (valid or not) is written as it arrived, with the
seconds since the capture started, to a gzip file:

    b"PSCAP1\\n", then per datagram: float64 t, uint16 length, the bytes

A capture can be replayed into the app at its original pace (scripts/diagnostics.py
replay) or summarised offline (diagnostics.py report): what the game sent, what was
rejected, and which laps PurpleSector saw. A file cut off by a crash reads up to the
last complete datagram.
"""
from __future__ import annotations

import gzip
import struct
import threading
import time
import zlib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

MAGIC = b"PSCAP1\n"
_REC = struct.Struct("<dH")
MAX_CAPTURE_BYTES = 4_000_000_000  # of datagrams, before compression: over an hour of 60 Hz telemetry


class CaptureWriter:
    def __init__(self, path: Path, max_bytes: int = MAX_CAPTURE_BYTES):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = gzip.open(path, "wb", compresslevel=1)  # fast: this runs on the receive thread
        self._f.write(MAGIC)
        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self.count = 0
        self.closed = False
        self.full = False  # stopped at max_bytes, so a forgotten capture can't fill the disk
        self._left = max_bytes

    def write(self, data: bytes) -> None:
        with self._lock:
            if self.closed or self.full:
                return
            if len(data) > self._left:
                self.full = True
                return
            self._left -= len(data)
            self._f.write(_REC.pack(time.monotonic() - self._t0, len(data)) + data)
            self.count += 1

    def close(self) -> None:
        with self._lock:
            if not self.closed:
                self.closed = True
                self._f.close()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0


def read_capture(path: Path) -> Iterator[tuple[float, bytes]]:
    """(seconds since start, datagram) in arrival order. Stops quietly at a truncated end."""
    with gzip.open(path, "rb") as f:
        try:
            if f.read(len(MAGIC)) != MAGIC:
                raise ValueError(f"{path.name} is not a PurpleSector capture.")
            while True:
                head = f.read(_REC.size)
                if len(head) < _REC.size:
                    return
                t, n = _REC.unpack(head)
                data = f.read(n)
                if len(data) < n:
                    return
                yield t, data
        except gzip.BadGzipFile:
            raise ValueError(f"{path.name} is not a PurpleSector capture.") from None
        except (EOFError, zlib.error):
            return  # cut off mid-stream (the app was closed while capturing)


def report(path: Path) -> dict:
    """Decode a capture the way the live app would and summarise it."""
    from analysis.coach import fmt_lap
    from analysis.lap_tracker import LapEventKind, LapTracker
    from telemetry.decoder import IgnoredPacketError, PacketError, UnsupportedFormatError, decode_packet
    from telemetry.packet_models import PacketId
    from telemetry.state import TelemetryState

    types, formats, unsupported = Counter(), Counter(), Counter()
    malformed = ignored = valid = 0
    errors: list[str] = []
    sessions, session_types, tracks = set(), [], []
    trackers: dict[int, LapTracker] = {}
    laps = []
    times = []
    state = TelemetryState()
    for _, data in read_capture(path):
        try:
            p = decode_packet(data)
        except UnsupportedFormatError as exc:
            unsupported[exc.packet_format] += 1
            continue
        except IgnoredPacketError as exc:
            ignored += 1
            types[str(exc)] += 1
            continue
        except PacketError as exc:
            malformed += 1
            if len(errors) < 10:
                errors.append(str(exc))
            continue
        valid += 1
        types[p.packet_id.name] += 1
        formats[p.header.packet_format] += 1
        times.append(p.header.session_time)
        uid = p.header.session_uid
        sessions.add(uid)
        state.apply(p)
        if p.packet_id == PacketId.SESSION:
            snap = state.snapshot()
            if snap.session_type and snap.session_type not in session_types:
                session_types.append(snap.session_type)
            if snap.track_name and snap.track_name not in tracks:
                tracks.append(snap.track_name)
        tracker = trackers.setdefault(uid, LapTracker())
        if p.packet_id == PacketId.LAP_DATA:
            for ev in tracker.on_lap_data(p.data):
                if ev.kind == LapEventKind.LAP_COMPLETED:
                    lap = ev.lap
                    laps.append({"lap": lap.lap_number, "time": fmt_lap(lap.lap_time_ms), "valid": lap.valid,
                                 "samples_complete": lap.samples_complete, "pit": lap.pit_lap,
                                 "samples": len(lap.samples)})
        elif p.packet_id == PacketId.CAR_TELEMETRY:
            tracker.on_telemetry(p.header.session_time, p.data)
        elif p.packet_id == PacketId.MOTION:
            tracker.on_motion(p.data)
    return {"valid": valid, "malformed": malformed, "ignored": ignored, "unsupported": dict(unsupported),
            "formats": dict(formats), "packet_types": dict(types), "sessions": len(sessions),
            "session_types": session_types, "tracks": tracks, "laps": laps, "errors": errors,
            "duration_s": (max(times) - min(times)) if times else 0.0}
