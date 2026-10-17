import json

from conftest import FakeEngine, wait_until

from analysis.coach import CoachConfig
from config.settings import Settings, build_settings, load_settings, save_settings
from telemetry import packet_builder as pb
from telemetry.connection import ConnectionState
from telemetry.packet_models import CarTelemetryData, Packet, PacketId
from telemetry.state import Snapshot, TelemetryState
from ui import components as c
from voice.queue_manager import SpeechItem, SpeechQueue
from voice.speaker import VoiceCoach

# ---------------- settings ----------------

def test_defaults_are_valid(tmp_path):
    assert Settings(data_dir=str(tmp_path)).validate() == {}


def test_invalid_values_fall_back_with_messages(tmp_path):
    s, errors = build_settings({"udp_port": 80, "udp_host": "not-an-ip", "voice_volume": "loud",
                                "coach_verbosity": "chatty", "telemetry_format": 2021, "data_dir": str(tmp_path),
                                "unknown_key": 1})
    # telemetry_format is a setting from v1.0 (2022 only): the year is now read from each packet, so it is ignored
    assert set(errors) == {"udp_port", "udp_host", "voice_volume", "coach_verbosity"}
    assert s.udp_port == 20777 and s.udp_host == "127.0.0.1" and not hasattr(s, "telemetry_format")


def test_type_coercion_from_env_strings(tmp_path):
    s, errors = build_settings({"udp_port": "20888", "voice_enabled": "false", "demo_speed": "2",
                                "data_dir": str(tmp_path)})
    assert errors == {} and s.udp_port == 20888 and s.voice_enabled is False and s.demo_speed == 2.0


def test_load_precedence_and_corrupt_file(tmp_path):
    path = tmp_path / "settings.json"
    env = {"PURPLESECTOR_UDP_PORT": "21000", "PURPLESECTOR_DATA_DIR": str(tmp_path)}
    s, _ = load_settings(path, env)
    assert s.udp_port == 21000
    path.write_text(json.dumps({"udp_port": 22000}), encoding="utf-8")
    assert load_settings(path, env)[0].udp_port == 22000  # saved file wins over env
    path.write_text("{not json", encoding="utf-8")
    s, errors = load_settings(path, {"PURPLESECTOR_DATA_DIR": str(tmp_path)})
    assert "settings_file" in errors and s.udp_port == 20777


def test_save_refuses_invalid(tmp_path):
    path = tmp_path / "s.json"
    assert save_settings(Settings(udp_port=1, data_dir=str(tmp_path)), path)
    assert not path.exists()
    assert save_settings(Settings(data_dir=str(tmp_path)), path) == {} and path.exists()


def test_coach_config_from_settings(tmp_path):
    cfg = CoachConfig.from_settings(Settings(coach_cooldown_s=5, sector_loss_threshold_s=0.5, data_dir=str(tmp_path)))
    assert cfg.cooldown_s == 5 and cfg.sector_loss_threshold_s == 0.5


# ---------------- voice ----------------

def test_queue_priority_order_and_bounds():
    t = [0.0]
    q = SpeechQueue(maxsize=2, clock=lambda: t[0])
    q.put(SpeechItem("low", 1, created=0))
    q.put(SpeechItem("mid", 2, created=0))
    assert q.put(SpeechItem("high", 3, created=0))  # evicts "low"
    assert not q.put(SpeechItem("low2", 1, created=0))  # full of higher-priority items
    assert q.get(0).text == "high" and q.get(0).text == "mid" and q.get(0) is None


def test_queue_drops_stale_and_duplicates():
    t = [0.0]
    q = SpeechQueue(clock=lambda: t[0])
    q.put(SpeechItem("old", 1, created=0.0))
    assert not q.put(SpeechItem("old", 1, created=0.0))
    t[0] = 100.0
    assert q.get(0) is None and q.dropped >= 2


def test_voice_worker_speaks_with_mock_engine():
    eng = FakeEngine()
    v = VoiceCoach(engine_factory=lambda: eng, min_interval_s=0, rate=3, volume=40)
    v.start()
    try:
        assert wait_until(lambda: v.status == "ready")
        v.say("Lap one", 2)
        v.say("Personal best", 3)
        assert wait_until(lambda: len(eng.spoken) == 2)
        assert eng.rate == 3 and eng.volume == 40
        v.configure(enabled=False)
        assert not v.say("ignored")
        assert v.test()  # explicit test speaks even when coaching voice is off
        assert wait_until(lambda: len(eng.spoken) == 3)
    finally:
        v.stop()
    assert v.status == "stopped"


def test_voice_failure_is_contained():
    def broken():
        raise OSError("no SAPI")
    v = VoiceCoach(engine_factory=broken)
    v.start()
    assert wait_until(lambda: v.status == "unavailable")
    assert not v.say("hello") and "no SAPI" in v.last_error
    v.stop()

    class Flaky(FakeEngine):
        def speak(self, text):
            raise RuntimeError("audio device lost")
    v = VoiceCoach(engine_factory=Flaky, min_interval_s=0)
    v.start()
    v.say("x")
    assert wait_until(lambda: v.last_error is not None)
    assert v.status == "ready"  # still running
    v.stop()


# ---------------- state -> dashboard ----------------

def test_state_normalizes_units_and_history_window():
    st = TelemetryState(history_window_s=5)
    for i in range(600):
        st.apply(Packet(pb.header(PacketId.CAR_TELEMETRY, session_time=i / 60), PacketId.CAR_TELEMETRY,
                        CarTelemetryData(speed=100 + i % 10, throttle=0.42, brake=0.0, tyres_pressure=(21.55,) * 4)))
    snap = st.snapshot()
    assert snap.throttle_pct == 42.0 and snap.tyre_pressures_psi == (21.6,) * 4
    assert snap.lap_number is None and snap.fuel_kg is None  # never provided -> unavailable
    h = st.history()
    assert h[-1][0] - h[0][0] <= 5.0 and len(h) <= 5 * 60 + 1


def test_history_series_breaks_lines_at_gaps():
    rows = [(0.0, 1, 0, 0, 0, 1, 1), (0.1, 2, 0, 0, 0, 1, 1), (5.0, 3, 0, 0, 0, 1, 1), (5.1, 4, 0, 0, 0, 1, 1)]
    cols = c.history_series(rows)
    assert cols["speed"] == [1, 2, None, 3, 4]
    assert cols["t"][-1] == 0


def test_header_labels_demo_and_missing_values():
    html = c.header_bar(Snapshot(source="demo"), ConnectionState.CONNECTED, "demo", "ready", True, False)
    assert "simulated" in html and "n/a" in html
    live = c.header_bar(Snapshot(), ConnectionState.WAITING, "live", "unavailable", True, False)
    assert "Live" in live and "Voice unavailable" in live and "simulated" not in live


def test_formatters_and_sector_colours():
    assert c.fmt_lap(83456) == "1:23.456" and c.fmt_lap(None) == "—" and c.fmt_lap(45678) == "45.678"
    assert c.fmt_gear(-1) == "R" and c.fmt_gear(0) == "N" and c.fmt_gear(None) == "—"
    assert "purple" in c.sector_class(30000, 30000) and "yellow" in c.sector_class(30100, 30000)
    assert c.sector_class(None, 30000) == "ps-sector"
    assert c.val(None) == c.NA
    assert "<script>" not in c.val("<script>")


def test_pit_board_says_where_the_race_gap_comes_from():
    from analysis.field import TowerRow
    from analysis.gaps import CarGap
    from telemetry.state import Snapshot
    snap = Snapshot(position=3, lap_number=5, total_laps=30)
    gaps = {"race": True, "cars": 20, "ahead": CarGap(1, "NORRIS", 2, 1.2, None), "behind": None}
    measured = c.pit_board(snap, gaps, None)
    assert "+1.2" in measured and "measured" in measured
    me = TowerRow(0, 3, "YOU", True, None, (None,) * 3, 1.345, "game", False, (False,) * 3)
    game = c.pit_board(snap, gaps, me)
    assert "+1.3" in game and "game" in game and "measured" not in game


def test_rev_gauge_needs_the_games_rev_limit():
    from telemetry.state import Snapshot
    html = c.telemetry_strip(Snapshot(rpm=10_500, max_rpm=None))
    assert "10.5" in html and "width:0%" in html.split("Engine")[1][:200]  # no bar against an assumed limit
    html = c.telemetry_strip(Snapshot(rpm=10_500, max_rpm=12_000))
    assert "width:88%" in html.split("Engine")[1][:200]


def test_live_traces_are_one_svg_image_split_at_gaps():
    import base64
    import re
    rows = [(t / 60, 200.0, 100.0, 0.0, 0.1, 6, 10000) for t in range(120)]
    rows += [(t / 60, 150.0, 0.0, 80.0, -0.2, 3, 8000) for t in range(200, 300)]  # a gap: telemetry dropped out
    html = c.live_traces(c.history_series(rows), 30)
    svg = base64.b64decode(re.search(r'base64,([^"]+)"', html).group(1)).decode()
    assert html.startswith('<div class="ps-panel">') and 'alt="' in html
    assert svg.count('fill="none" stroke="#ECEEF2"') == 2  # speed drawn as two pieces either side of the gap
    assert "Throttle" in svg and "Brake" in svg and "Steering" in svg and "km/h" in svg
    assert c.live_traces(c.history_series([]), 30).count("<img") == 1  # empty history still draws the axes


def test_last_call_shows_the_message_with_its_category_confidence_and_time():
    from analysis.coach import Category, CoachEvent, Confidence, Priority
    e = CoachEvent("k", Category.CORNERS, "Corner 5: lost 0.42 s", Priority.MEDIUM, Confidence.MEDIUM, "telemetry",
                   timestamp=0.0)
    html = c.last_call([e])
    assert "Corner 5: lost 0.42 s" in html and c.esc(e.category) in html and "medium confidence" in html
    assert ":" in html.split("confidence")[1]  # the clock time follows
    assert "quiet" in c.last_call([]) and "quiet" not in html
