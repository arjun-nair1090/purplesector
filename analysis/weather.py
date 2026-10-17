"""Weather forecast calls from the game's own forecast.

The session packet carries the game's forecast for this session (minutes ahead, weather,
chance of rain). A call is made when the forecast shows the track changing between dry
and wet: once when the change first appears, and once more when it is five minutes or
less away. Nothing is said about cloud cover alone, and nothing is predicted beyond what
the game forecasts.
"""
from __future__ import annotations

from analysis.coach import Category, CoachEvent, Confidence, Priority
from telemetry.packet_models import WEATHER

WET = (3, 4, 5)  # light rain, heavy rain, storm
SOON_MIN = 5


class WeatherWatch:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._wet_now: bool | None = None
        self._said: set[str] = set()

    def check(self, current: int | None, forecast, accuracy: int | None) -> list[CoachEvent]:
        if current is None:
            return []
        wet = current in WET
        if wet != self._wet_now:  # the weather itself changed: earlier calls no longer apply
            self._wet_now, self._said = wet, set()
        nxt = next((s for s in sorted(forecast, key=lambda s: s.time_offset_min)
                    if s.time_offset_min > 0 and (s.weather in WET) != wet), None)
        if nxt is None:
            return []
        stage = "soon" if nxt.time_offset_min <= SOON_MIN else "ahead"
        if stage in self._said:
            return []
        self._said |= {stage, "ahead"}  # once it's close, the earlier stage is spent too
        m, r, name = nxt.time_offset_min, nxt.rain_percentage, WEATHER.get(nxt.weather, "Weather change")
        quality = {0: " (perfect)", 1: " (approximate)"}.get(accuracy, "")
        if wet:
            return [CoachEvent(f"forecast:{stage}:dry", Category.WEATHER, f"Forecast: {name} in {m} min, track drying",
                               Priority.MEDIUM, Confidence.HIGH if accuracy == 0 else Confidence.MEDIUM,
                               f"Game weather forecast{quality}", speech=f"Track expected to dry in {m} minutes.")]
        return [CoachEvent(f"forecast:{stage}:wet", Category.WEATHER, f"Forecast: {name} in {m} min ({r}% chance of rain)",
                           Priority.HIGH, Confidence.HIGH if accuracy == 0 else Confidence.MEDIUM,
                           f"Game weather forecast{quality}", speech=f"Rain expected in {m} minutes, {r} percent.")]
