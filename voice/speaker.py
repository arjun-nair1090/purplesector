"""Offline voice output on a dedicated worker thread.

The engine (voice.engines.RadioEngine) renders each call with the chosen voice, Piper or
Windows SAPI, puts it through the team-radio effect and plays it, all inside the worker
thread. pyttsx3 was evaluated but its runAndWait() never returned when driven from a
worker thread on this Python/Windows combination, so it is not used.
If no engine can be created the coach keeps working silently (status "unavailable").
"""
from __future__ import annotations

import logging
import sys
import threading
import time

from voice.queue_manager import SpeechItem, SpeechQueue

log = logging.getLogger(__name__)


def default_engine_factory():
    if sys.platform != "win32":
        raise RuntimeError("Voice output requires Windows")
    from voice.engines import RadioEngine

    return RadioEngine()


class VoiceCoach:
    def __init__(self, queue: SpeechQueue | None = None, engine_factory=default_engine_factory, *,
                 enabled: bool = True, rate: int = 0, volume: int = 90, min_interval_s: float = 4.0,
                 voice: str | None = None, radio: bool = True, clock=time.monotonic):
        self.queue = queue or SpeechQueue()
        self._factory = engine_factory
        self.enabled = enabled
        self.rate, self.volume, self.min_interval_s = rate, volume, min_interval_s
        self.voice, self.radio = voice, radio  # engine voice spec (voice.engines.resolve) and radio effect
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty = True
        self.status = "stopped"           # stopped | starting | ready | unavailable
        self.last_error: str | None = None
        self.last_spoken: str | None = None
        self.spoken_count = 0
        self._last_spoken_at: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.status = "starting"
        self._thread = threading.Thread(target=self._run, name="voice", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self.queue.clear()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None
        self.status = "stopped"

    def configure(self, *, enabled=None, rate=None, volume=None, min_interval_s=None, voice=None,
                  radio=None) -> None:
        if enabled is not None:
            self.enabled = enabled
            if not enabled:
                self.queue.clear()
        if rate is not None:
            self.rate = rate
        if volume is not None:
            self.volume = volume
        if min_interval_s is not None:
            self.min_interval_s = min_interval_s
        if voice is not None:
            self.voice = voice
        if radio is not None:
            self.radio = radio
        self._dirty = True

    def say(self, text: str, priority: int = 2, key: str = "") -> bool:
        """Non-blocking. Returns False if voice is disabled or the message was dropped."""
        if not self.enabled or self.status == "unavailable":
            return False
        return self.queue.put(SpeechItem(text, priority, key))

    def test(self) -> bool:
        """Explicit user action: speaks even if coaching voice is switched off."""
        if self.status == "unavailable":
            return False
        return self.queue.put(SpeechItem("Radio check. Loud and clear, how do you read?", 3, "voice_test"))

    def _run(self) -> None:
        try:
            engine = self._factory()
        except Exception as exc:
            self.status = "unavailable"
            self.last_error = f"Speech engine unavailable: {exc}"
            log.warning(self.last_error)
            return
        self.status = "ready"
        while not self._stop.is_set():
            if self._dirty:
                # a new voice loads now, not when the next call is due (a neural voice takes a few seconds)
                self._configure(engine)
            if self._last_spoken_at is not None:
                # wait out the minimum gap first, so we then pick the most urgent *fresh* message
                wait = self.min_interval_s - (self._clock() - self._last_spoken_at)
                if wait > 0 and self._stop.wait(wait):
                    break
            item = self.queue.get(timeout=0.5)
            if item is None:
                continue
            if not self.enabled and item.key != "voice_test":
                continue
            try:
                if self._dirty:
                    self._configure(engine)
                engine.speak(item.text)
                self.last_spoken, self.spoken_count = item.text, self.spoken_count + 1
                self._last_spoken_at = self._clock()
            except Exception as exc:
                self.last_error = f"Speech failed: {exc}"
                log.warning(self.last_error)

    def _configure(self, engine) -> None:
        self._dirty = False
        try:
            engine.configure(self.rate, self.volume, voice=self.voice, radio=self.radio)
            self.last_error = getattr(engine, "fallback_reason", None)
        except Exception as exc:
            self.last_error = f"Voice settings failed: {exc}"
            log.warning(self.last_error)
