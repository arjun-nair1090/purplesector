"""Background writer: keeps all database I/O off the packet-receive thread.

Commands go through a bounded queue. Recordings are addressed by an in-memory key
so callers never wait for a database id.
"""
from __future__ import annotations

import logging
import queue
import threading

from storage.session_repository import SessionRepository

log = logging.getLogger(__name__)
_STOP = object()


class TelemetryWriter:
    def __init__(self, repo: SessionRepository, maxsize: int = 256):
        self.repo = repo
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._ids: dict[str, int] = {}
        self._lap_counts: dict[str, int] = {}
        self._thread: threading.Thread | None = None
        self.laps_written = 0
        self.dropped = 0
        self.last_error: str | None = None

    @property
    def pending(self) -> int:
        return self._q.qsize()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="db-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Flush everything queued, then stop."""
        if not self._thread:
            return
        self._q.put(_STOP)
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning("DB writer did not finish within %.0fs; %d commands pending", timeout, self.pending)
        self._thread = None

    def _submit(self, *cmd) -> bool:
        try:
            self._q.put(cmd, timeout=0.5)
            return True
        except queue.Full:
            self.dropped += 1
            self.last_error = f"Write queue full; dropped {cmd[0]}"
            log.error(self.last_error)
            return False

    # public, non-blocking API
    def start_session(self, key: str, session_uid: int, source: str, track_name: str | None = None) -> bool:
        return self._submit("start", key, session_uid, source, track_name)

    def update_session(self, key: str, **meta) -> bool:
        return self._submit("update", key, meta)

    def save_lap(self, key: str, lap) -> bool:
        return self._submit("lap", key, lap, list(lap.samples))  # capture samples now; tracker may trim later

    def end_session(self, key: str, status: str = "complete") -> bool:
        return self._submit("end", key, status)

    def _run(self) -> None:
        while True:
            cmd = self._q.get()
            if cmd is _STOP:
                return
            try:
                self._apply(*cmd)
            except Exception as exc:
                self.last_error = f"Database write failed ({cmd[0]}): {exc}"
                log.exception(self.last_error)

    def _apply(self, op, key, *args) -> None:
        if op == "start":
            self._ids[key] = self.repo.create_session(*args)
            self._lap_counts[key] = 0
            return
        sid = self._ids.get(key)
        if sid is None:
            raise RuntimeError(f"unknown recording {key}")
        if op == "update":
            self.repo.update_session(sid, **args[0])
        elif op == "lap":
            self.repo.save_lap(sid, *args)
            self.laps_written += 1
            self._lap_counts[key] += 1
        elif op == "end":
            if self._lap_counts.pop(key, 0):
                self.repo.finish_session(sid, *args)
            else:
                self.repo.delete_session(sid)  # no completed lap: nothing worth keeping in History
            del self._ids[key]

    def session_id(self, key: str | None) -> int | None:
        return self._ids.get(key) if key else None
