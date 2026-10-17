"""Weather forecast calls: said once per change the game forecasts, again when it is close."""
from analysis.weather import WeatherWatch
from telemetry.packet_models import WeatherForecastSample


def fc(*rows):
    """(minutes ahead, weather code, rain %) -> forecast samples for one session type."""
    return tuple(WeatherForecastSample(10, m, w, 30, 2, 22, 2, r) for m, w, r in rows)


def test_rain_in_the_forecast_is_called_once_then_again_when_close():
    w = WeatherWatch()
    later = fc((0, 1, 5), (5, 1, 10), (10, 3, 60), (15, 3, 70))
    (e,) = w.check(1, later, accuracy=0)
    assert e.speech == "Rain expected in 10 minutes, 60 percent."
    assert "Light rain in 10 min" in e.message and "60%" in e.message and "perfect" in e.evidence
    assert w.check(1, later, accuracy=0) == []        # same forecast: nothing new
    soon = fc((0, 1, 20), (5, 3, 65), (10, 3, 70))
    (e,) = w.check(1, soon, accuracy=1)
    assert e.speech == "Rain expected in 5 minutes, 65 percent." and "approximate" in e.evidence
    assert w.check(1, soon, accuracy=1) == []


def test_drying_track_is_called():
    (e,) = WeatherWatch().check(4, fc((0, 4, 90), (15, 2, 10), (30, 1, 0)), accuracy=0)
    assert e.speech == "Track expected to dry in 15 minutes." and "Overcast in 15 min" in e.message


def test_a_change_of_the_actual_weather_starts_over():
    w = WeatherWatch()
    rain = fc((0, 1, 5), (10, 3, 60))
    assert len(w.check(1, rain, accuracy=0)) == 1
    assert w.check(3, fc((0, 3, 60), (30, 3, 60)), accuracy=0) == []  # it rained: nothing to forecast
    assert len(w.check(1, rain, accuracy=0)) == 1                     # dry again, rain forecast again


def test_nothing_without_a_forecast_or_a_change():
    w = WeatherWatch()
    assert w.check(1, (), accuracy=None) == []
    assert w.check(1, fc((0, 1, 0)), accuracy=0) == []
    assert w.check(1, fc((0, 1, 0), (30, 2, 10)), accuracy=0) == []  # cloudier, still dry
    assert w.check(None, fc((0, 1, 0), (10, 3, 60)), accuracy=0) == []  # current weather unknown


def test_state_keeps_only_the_current_sessions_forecast():
    from telemetry.packet_models import Packet, PacketHeader, PacketId, SessionData
    from telemetry.state import TelemetryState
    st = TelemetryState()
    samples = (WeatherForecastSample(5, 0, 1, 30, 2, 22, 2, 0), WeatherForecastSample(10, 0, 2, 30, 2, 22, 2, 0),
               WeatherForecastSample(10, 5, 3, 30, 2, 22, 2, 40))
    st.apply(Packet(PacketHeader(packet_id=1), PacketId.SESSION, SessionData(weather=2, session_type=10,
                                                                               forecast=samples, forecast_accuracy=1)))
    s = st.snapshot()
    assert [x.time_offset_min for x in s.forecast] == [0, 5] and s.weather_code == 2 and s.forecast_accuracy == 1
