import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from analysis.coach import Priority, radio_report
from analysis.gaps import CarGap
from analysis.race_radio import RaceRadio
from analysis.radio_speech import say_gap, say_name, say_time, with_name
from config.settings import Settings
from voice import engines
from voice.radio import BAND_HZ, apply_volume, radio_fx, wav_bytes

SR = 22050

# ---------------- the radio sound ----------------


def tone(*freqs, seconds=1.0):
    t = np.arange(int(SR * seconds)) / SR
    return sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)


def band_energy(x, lo, hi):
    f = np.fft.rfftfreq(len(x), 1 / SR)
    p = np.abs(np.fft.rfft(x)) ** 2
    return p[(f >= lo) & (f < hi)].sum() / p.sum()


def test_radio_keeps_the_voice_band_and_drops_the_rest():
    out = radio_fx(tone(100, 1000, 7000), SR, seed=1)
    assert band_energy(out, *BAND_HZ) > 0.9  # the 100 Hz rumble and 7 kHz air are gone
    assert np.max(np.abs(out)) <= 1.0


def test_radio_wraps_the_call_in_the_channel_opening_and_closing():
    x = tone(1000, seconds=0.5)
    out = radio_fx(x, SR, seed=1)
    assert len(out) > len(x) + SR * 0.3  # key-up burst before, squelch tail after
    assert np.max(np.abs(out[:20])) > 0.05  # the click is at the very start


def test_volume_is_squared_and_wav_is_16_bit_mono():
    x = np.full(100, 0.5)
    assert apply_volume(x, 100)[0] == pytest.approx(0.5)
    assert apply_volume(x, 50)[0] == pytest.approx(0.125)
    assert not apply_volume(x, 0).any()
    with wave.open(io.BytesIO(wav_bytes(x, SR))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, SR, 100)


# ---------------- which voice speaks ----------------


class FakeSynth:
    def __init__(self, spec):
        if "broken" in spec:
            raise RuntimeError("model file is corrupt")
        self.spec = spec

    def render(self, text, rate):
        return tone(1000, seconds=0.2) * 0.3, SR


def test_engine_loads_the_chosen_voice_and_falls_back_to_windows():
    played = []
    e = engines.RadioEngine(synth_factory=FakeSynth, player=played.append)
    e.configure(0, 80, voice="piper:x.onnx")
    assert e._synth.spec == "piper:x.onnx" and e.fallback_reason is None
    e.speak("Box this lap.")
    assert played and played[0][:4] == b"RIFF"
    e.configure(0, 80, voice="piper:broken.onnx")
    assert e._synth.spec == "sapi:" and "corrupt" in e.fallback_reason
    e.configure(0, 80, voice="sapi:Microsoft Zira Desktop")
    assert e._synth.spec == "sapi:Microsoft Zira Desktop" and e.fallback_reason is None


def test_volume_and_radio_switch_change_the_sound():
    played = []
    e = engines.RadioEngine(synth_factory=FakeSynth, player=played.append)
    e.configure(0, 100, voice="sapi:", radio=False)
    e.speak("x")
    e.configure(0, 30, voice="sapi:", radio=False)
    e.speak("x")
    e.configure(0, 100, voice="sapi:", radio=True)
    e.speak("x")
    loud, quiet, radio = (len(p) for p in played)
    assert loud == quiet < radio  # the radio adds the channel bursts
    pcm = [np.frombuffer(p[44:], "<i2") for p in played[:2]]
    assert np.abs(pcm[1]).max() < np.abs(pcm[0]).max() * 0.15


def test_resolve_says_why_a_neural_voice_is_not_speaking(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "piper_available", lambda: True)
    spec, why = engines.resolve("piper:en_GB-cori-medium", tmp_path)
    assert spec == "sapi:" and "downloaded" in why
    (tmp_path / "en_GB-cori-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_GB-cori-medium.onnx.json").write_text("{}")
    spec, why = engines.resolve("piper:en_GB-cori-medium", tmp_path)
    assert spec == f"piper:{tmp_path / 'en_GB-cori-medium.onnx'}" and why is None
    monkeypatch.setattr(engines, "piper_available", lambda: False)
    assert "piper-tts" in engines.resolve("piper:en_GB-cori-medium", tmp_path)[1]
    assert engines.resolve("sapi:Microsoft David Desktop", tmp_path) == ("sapi:Microsoft David Desktop", None)
    assert engines.resolve("espeak:x", tmp_path)[0] == "sapi:"


def test_voice_settings_validate(tmp_path):
    s = Settings(data_dir=str(tmp_path))
    assert s.validate() == {} and s.voice_name == engines.DEFAULT_VOICE
    assert "voice_name" in Settings(voice_name="espeak:x", data_dir=str(tmp_path)).validate()
    assert "driver_name" in Settings(driver_name="x" * 25, data_dir=str(tmp_path)).validate()
    assert "race_update_laps" in Settings(race_update_laps=-1, data_dir=str(tmp_path)).validate()


# ---------------- how it's said ----------------


def test_numbers_are_said_the_radio_way():
    assert say_time(81_063) == "1 21 point 1"
    assert say_time(65_340) == "1 oh 5 point 3"
    assert say_time(58_300) == "58 point 3"
    assert say_gap(0.04) == "less than a tenth" and say_gap(0.1) == "a tenth" and say_gap(-0.34) == "3 tenths"
    assert say_gap(1.26) == "1.3"
    assert say_name("T. OKAFOR") == "Okafor" and say_name("DE VRIES") == "De Vries"
    assert with_name("Good job.", "Arjun") == "Good job, Arjun." and with_name("Good job.", "") == "Good job."


def gap(name, position, gap_s, change=None, idx=1):
    return CarGap(idx, name, position, gap_s, change)


def test_radio_report_has_position_laps_to_go_and_tyres():
    e = radio_report(1, delta_s=0.3, delta_ref="session best", gaps={"ahead": gap("T. OKAFOR", 2, 1.2, -0.3)},
                     fuel_margin_laps=0.8, fuel_margin_source="measured", position=3, laps_left=12,
                     tyre_wear=(20.0, 21.0, 34.0, 30.0))
    assert e.spoken.startswith("P3, 12 to go. Delta plus 0.3.")
    assert "Gap ahead 1.2 to Okafor, closing 3 tenths a lap." in e.spoken
    assert "Tyres 34 percent, front left the worst." in e.spoken and "Fuel plus 0.8 laps." in e.spoken


# ---------------- race calls ----------------


def snap(t=100.0, **kw):
    base = dict(session_time_s=t, position=5, pit_status=0, fia_flag=0, wing_damage_pct=(0, 0, 0), total_laps=20,
                tyre_wear_pct=(10.0, 10.0, 12.0, 11.0), pit_window_ideal_lap=None, pit_window_latest_lap=None)
    return SimpleNamespace(**{**base, **kw})


def test_a_place_is_called_once_it_has_settled():
    r = RaceRadio(settle_s=3)
    assert r.on_progress(snap(100, position=5)) == []
    assert r.on_progress(snap(104, position=5)) == []  # first settled position: no call
    assert r.on_progress(snap(105, position=4)) == []
    assert r.on_progress(snap(106, position=5)) == []  # back again before it settled: nothing happened
    assert r.on_progress(snap(110, position=5)) == []
    r.on_progress(snap(111, position=3))
    (e,) = r.on_progress(snap(115, position=3))
    assert e.message == "P5 → P3" and "Up 2 places, P3." == e.spoken
    assert r.on_progress(snap(120, position=3, pit_status=1)) == []


def test_flags_and_new_wing_damage_are_called():
    r = RaceRadio(flag_cooldown_s=30)
    r.on_progress(snap(100, wing_damage_pct=(10, 0, 0)))  # damage already there when we joined: not news
    calls = r.on_progress(snap(101, fia_flag=3, wing_damage_pct=(30, 0, 0)))
    assert [e.message for e in calls] == ["Yellow flag", "Front wing, left side damage 30%"]
    assert calls[0].priority == Priority.HIGH and calls[1].spoken == "Front wing left side damage, 30 percent."
    r.on_progress(snap(102, fia_flag=0))
    assert r.on_progress(snap(110, fia_flag=3)) == []  # same flag within the cooldown
    assert r.on_progress(snap(111, fia_flag=2))[0].message == "Blue flag"


def test_line_call_bundles_what_matters():
    r = RaceRadio()
    gaps = {"ahead": gap("T. OKAFOR", 4, 0.7, -0.2, 1), "behind": gap("L. BRANDT", 6, 2.4, 0.1, 2)}
    e = r.on_line(6, snap(tyre_wear_pct=(30.0, 31.0, 52.0, 40.0)), gaps, 0.5, every=3)
    assert e.key == "race_line:6"
    assert e.spoken.startswith("P5. Okafor 7 tenths ahead, within a second.")
    assert "Gap behind 2.4 to Brandt, opening a tenth a lap." in e.spoken
    assert "Tyres at 52 percent, front left the worst." in e.spoken
    # next lap: the same fight isn't repeated, wear already called, not a routine lap -> nothing
    assert r.on_line(7, snap(tyre_wear_pct=(30.0, 31.0, 55.0, 40.0)), gaps, 0.4, every=3) is None
    assert "within a second" in r.on_line(8, snap(), gaps, 0.4, every=3).spoken


def test_line_call_fuel_pit_window_laps_to_go_and_the_flag():
    r = RaceRadio()
    e = r.on_line(15, snap(pit_window_ideal_lap=16), {}, -0.5, every=0)
    assert e.priority == Priority.HIGH
    assert e.spoken == "5 laps to go. Fuel is minus 0.5 laps. We need to save. Pit window's open, ideal lap is this one."
    assert r.on_line(16, snap(), {}, 0.1, every=0).spoken == "Fuel's back on target."
    assert r.on_line(19, snap(), {}, 0.1, every=0).spoken in ("Last lap.", "Final lap, bring it home.")
    fin = r.on_line(20, snap(position=3), {}, 0.1, every=0, name="Arjun")
    assert fin.key == "finish" and "P3." in fin.spoken and fin.spoken.endswith(", Arjun.")
