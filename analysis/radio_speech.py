"""How the engineer says things: numbers the way they come over team radio, and a little variety.

Only the spoken form changes here; the panel keeps the exact figures. Small gaps are said in
tenths ("two tenths"), lap times as the pit wall reads them ("1 21 point 3"), and recurring
calls rotate between a few phrasings so the same message doesn't sound canned every lap.
"""
from __future__ import annotations

import random
import re

_rng = random.Random()

WHEEL_NAMES = {"RL": "rear left", "RR": "rear right", "FL": "front left", "FR": "front right"}


def seed(n: int | None) -> None:
    """Fix the phrasing choices (tests)."""
    _rng.seed(n)


def pick(*options: str) -> str:
    return _rng.choice(options)


def say_time(ms: int) -> str:
    """81_063 -> "1 21 point 1"; 58_300 -> "58 point 3". Read aloud, as an engineer reads a lap time."""
    tenths = int(round(ms / 100))
    m, rem = divmod(tenths, 600)
    s, t = divmod(rem, 10)
    if not m:
        return f"{s} point {t}"
    return f"{m} {'oh ' if s < 10 else ''}{s} point {t}"


def say_gap(seconds: float) -> str:
    """A time difference as said on the radio: tenths below a second, seconds and a tenth above."""
    s = abs(seconds)
    n = int(round(s * 10))
    if n == 0:
        return "less than a tenth"
    if n < 10:
        return "a tenth" if n == 1 else f"{n} tenths"
    return f"{s:.1f}"


def say_name(name: str) -> str:
    """The game's "T. OKAFOR" or "DE VRIES" as an engineer says it: the surname, in normal case."""
    return re.sub(r"^\w\.\s*", "", name.strip()).title()


def say_signed(x: float) -> str:
    return f"{'minus' if x < 0 else 'plus'} {abs(x):.1f}"


def with_name(text: str, name: str | None) -> str:
    """"Good job." + "Arjun" -> "Good job, Arjun." (no name, no change)."""
    if not name:
        return text
    return f"{text[:-1]}, {name}{text[-1]}" if text[-1:] in ".!" else f"{text}, {name}"
