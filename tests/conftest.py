import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def wait_until(pred, timeout: float = 5.0, interval: float = 0.02) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class FakeEngine:
    def __init__(self):
        self.spoken = []

    def configure(self, rate, volume):
        self.rate, self.volume = rate, volume

    def speak(self, text):
        self.spoken.append(text)
