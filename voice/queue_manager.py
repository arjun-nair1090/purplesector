"""Small bounded priority queue for speech: urgent first, stale and duplicate messages dropped."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# Seconds after which an unspoken message is no longer worth saying, per priority (1 low .. 3 high).
DEFAULT_TTL_S = {1: 6.0, 2: 12.0, 3: 20.0}


@dataclass
class SpeechItem:
    text: str
    priority: int = 2
    key: str = ""
    created: float = field(default_factory=time.monotonic)


class SpeechQueue:
    def __init__(self, maxsize: int = 4, ttl_s: dict | None = None, clock=time.monotonic):
        self.maxsize = maxsize
        self.ttl_s = ttl_s or DEFAULT_TTL_S
        self._clock = clock
        self._items: list[SpeechItem] = []
        self._cond = threading.Condition()
        self.dropped = 0

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)

    def put(self, item: SpeechItem) -> bool:
        with self._cond:
            self._prune()
            if any(i.text == item.text or (item.key and i.key == item.key) for i in self._items):
                self.dropped += 1
                return False
            if len(self._items) >= self.maxsize:
                lowest = min(self._items, key=lambda i: (i.priority, -i.created))
                if lowest.priority > item.priority:
                    self.dropped += 1
                    return False
                self._items.remove(lowest)
                self.dropped += 1
            self._items.append(item)
            self._cond.notify()
            return True

    def get(self, timeout: float | None = None) -> SpeechItem | None:
        """Highest priority first, oldest first within a priority. None on timeout."""
        with self._cond:
            end = None if timeout is None else self._clock() + timeout
            while True:
                self._prune()
                if self._items:
                    best = max(self._items, key=lambda i: (i.priority, -i.created))
                    self._items.remove(best)
                    return best
                remaining = None if end is None else end - self._clock()
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def clear(self) -> None:
        with self._cond:
            self._items.clear()

    def _prune(self) -> None:
        now = self._clock()
        fresh = [i for i in self._items if now - i.created <= self.ttl_s.get(i.priority, 10.0)]
        self.dropped += len(self._items) - len(fresh)
        self._items = fresh
