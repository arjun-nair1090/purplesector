"""Connection health derived from actual packet timestamps, plus receiver diagnostics."""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from enum import StrEnum

from config.defaults import CONNECTION_STALE_S


class ConnectionState(StrEnum):
    DISCONNECTED = "Disconnected"
    WAITING = "Waiting for telemetry"
    CONNECTED = "Connected"
    RECONNECTING = "Reconnecting"
    UNSUPPORTED_FORMAT = "Unsupported telemetry format"
    RECEIVER_ERROR = "Receiver error"


class ConnectionMonitor:
    """Thread-safe counters. `state()` is computed from timestamps, never from socket state alone."""

    def __init__(self, clock=time.monotonic, stale_s: float = CONNECTION_STALE_S):
        self._clock = clock
        self._stale_s = stale_s
        self._lock = threading.Lock()
        self.reset()

    def reset(self, source: str = "live") -> None:
        with self._lock:
            self.source = source
            self.running = False
            self.error: str | None = None
            self.endpoint = ""
            self.packets_total = 0
            self.packets_valid = 0
            self.malformed = 0
            self.unsupported = 0
            self.ignored = 0
            self.duplicates = 0
            self.last_unsupported_format: int | None = None
            self.packet_types: Counter[str] = Counter()
            self.decoder_errors: deque[tuple[float, str]] = deque(maxlen=20)
            self.last_packet_at: float | None = None     # monotonic, any datagram
            self.last_valid_at: float | None = None      # monotonic, decoded telemetry
            self.last_valid_wall: float | None = None    # time.time(), for display
            self._recent: deque[float] = deque(maxlen=4096)

    # -- recording (called from the receiving thread) --
    def set_running(self, running: bool, endpoint: str = "") -> None:
        with self._lock:
            self.running = running
            if endpoint:
                self.endpoint = endpoint
            if running:
                self.error = None

    def set_error(self, message: str) -> None:
        with self._lock:
            self.error = message
            self.running = False

    def _datagram(self) -> float:
        now = self._clock()
        self.packets_total += 1
        self.last_packet_at = now
        return now

    def record_valid(self, packet_name: str) -> None:
        with self._lock:
            now = self._datagram()
            self.packets_valid += 1
            self.last_valid_at = now
            self.last_valid_wall = time.time()
            self.packet_types[packet_name] += 1
            self._recent.append(now)

    def record_ignored(self, packet_name: str) -> None:
        with self._lock:
            self._datagram()
            self.ignored += 1
            self.packet_types[packet_name] += 1

    def record_duplicate(self) -> None:
        with self._lock:
            self.duplicates += 1

    def record_unsupported(self, packet_format: int) -> None:
        with self._lock:
            self._datagram()
            self.unsupported += 1
            self.last_unsupported_format = packet_format

    def record_malformed(self, message: str) -> None:
        with self._lock:
            self._datagram()
            self.malformed += 1
            self.decoder_errors.append((time.time(), message))

    # -- reading (UI / watchdog) --
    def state(self) -> ConnectionState:
        with self._lock:
            now = self._clock()
            if self.error:
                return ConnectionState.RECEIVER_ERROR
            if not self.running:
                return ConnectionState.DISCONNECTED
            if self.last_valid_at is not None and now - self.last_valid_at <= self._stale_s:
                return ConnectionState.CONNECTED
            if (self.last_unsupported_format is not None and self.last_packet_at is not None
                    and now - self.last_packet_at <= self._stale_s):
                return ConnectionState.UNSUPPORTED_FORMAT
            if self.last_valid_at is not None:
                return ConnectionState.RECONNECTING
            return ConnectionState.WAITING

    def seconds_since_valid(self) -> float | None:
        with self._lock:
            return None if self.last_valid_at is None else self._clock() - self.last_valid_at

    def packet_rate(self, window_s: float = 2.0) -> float:
        with self._lock:
            cutoff = self._clock() - window_s
            return sum(1 for t in self._recent if t >= cutoff) / window_s

    def diagnostics(self) -> dict:
        rate = self.packet_rate()
        with self._lock:
            return {
                "source": self.source,
                "endpoint": self.endpoint,
                "running": self.running,
                "error": self.error,
                "packets_total": self.packets_total,
                "packets_valid": self.packets_valid,
                "malformed": self.malformed,
                "unsupported_format": self.unsupported,
                "last_unsupported_format": self.last_unsupported_format,
                "ignored_types": self.ignored,
                "duplicates": self.duplicates,
                "packet_rate_hz": round(rate, 1),
                "packet_types": dict(self.packet_types),
                "last_packet_wall": self.last_valid_wall,
                "decoder_errors": list(self.decoder_errors),
            }
