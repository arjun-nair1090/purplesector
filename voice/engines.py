"""Which voice speaks, and how its words become sound.

Two kinds of voice, both offline once installed:
- Piper neural voices (optional `piper-tts` package): natural-sounding, one model file per voice,
  downloaded on request into the data folder. Piper is GPL-3.0, so it is not bundled with the exe.
- Windows SAPI voices (David, Zira, ...): always present on Windows, used whenever a Piper voice
  isn't available.

Either way the speech is rendered to audio, put through the team-radio effect and played here,
so the volume and the radio sound are the same for every voice.

A voice is named by an id that survives restarts ("piper:en_GB-cori-medium", "sapi:Microsoft Zira
Desktop"); `resolve` turns it into a spec the engine can load, with a reason when it has to fall back.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice.radio import apply_volume, radio_fx, wav_bytes

log = logging.getLogger(__name__)

PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}/{model}{ext}?download=true"


@dataclass(frozen=True)
class PiperModel:
    model: str      # file stem, e.g. en_GB-cori-medium
    label: str
    path: str       # folder in the rhasspy/piper-voices repository
    licence: str    # of the recordings the voice was trained on
    size_mb: int = 61


# Voices whose training data allows use by anyone (public domain or Creative Commons with attribution).
PIPER_MODELS = (
    PiperModel("en_GB-northern_english_male-medium", "British engineer, male", "en/en_GB/northern_english_male/medium",
               "CC BY-SA 4.0"),
    PiperModel("en_GB-cori-medium", "British engineer, female", "en/en_GB/cori/medium", "public domain"),
    PiperModel("en_GB-alba-medium", "Scottish engineer, female", "en/en_GB/alba/medium", "CC BY 4.0"),
    PiperModel("en_US-joe-medium", "American engineer, male", "en/en_US/joe/medium", "CC0"),
    PiperModel("en_US-norman-medium", "American engineer, male (2)", "en/en_US/norman/medium", "public domain"),
)
DEFAULT_VOICE = f"piper:{PIPER_MODELS[0].model}"


@dataclass(frozen=True)
class VoiceOption:
    id: str
    label: str
    kind: str              # piper | sapi
    installed: bool
    size_mb: int | None = None
    licence: str | None = None


def piper_available() -> bool:
    return importlib.util.find_spec("piper") is not None


def model_path(voices_dir: Path, model: str) -> Path:
    return Path(voices_dir) / f"{model}.onnx"


def piper_installed(voices_dir: Path, model: str) -> bool:
    p = model_path(voices_dir, model)
    return p.is_file() and p.with_suffix(".onnx.json").is_file()


def sapi_voices() -> list[str]:
    """Names of the Windows SAPI voices, from the registry (no COM, so any thread may ask)."""
    if sys.platform != "win32":
        return []
    import winreg

    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Speech\Voices\Tokens") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i) + r"\Attributes") as attrs:
                        names.append(winreg.QueryValueEx(attrs, "Name")[0])
                except OSError:
                    continue
    except OSError:
        pass
    return names


def voice_options(voices_dir: Path) -> list[VoiceOption]:
    """Every voice that can be picked: the Piper voices (installed or downloadable) and the Windows voices."""
    out = []
    if piper_available():
        out += [VoiceOption(f"piper:{m.model}", m.label, "piper", piper_installed(voices_dir, m.model), m.size_mb,
                            m.licence) for m in PIPER_MODELS]
    out += [VoiceOption(f"sapi:{n}", f"{n.removeprefix('Microsoft ').removesuffix(' Desktop')} (Windows)", "sapi",
                        True) for n in sapi_voices()]
    return out


def resolve(voice_id: str, voices_dir: Path) -> tuple[str, str | None]:
    """voice id -> (engine spec, why it fell back or None). The spec names what the engine loads:
    "piper:<model file>" or "sapi:<voice name>" ("sapi:" alone is the Windows default voice)."""
    kind, _, name = (voice_id or "").partition(":")
    if kind == "piper":
        if not piper_available():
            return "sapi:", "Neural voices need the piper-tts package; using the Windows voice."
        if not piper_installed(voices_dir, name):
            return "sapi:", "This voice isn't downloaded yet; using the Windows voice."
        return f"piper:{model_path(voices_dir, name)}", None
    if kind == "sapi":
        return f"sapi:{name}", None
    return "sapi:", f"Unknown voice '{voice_id}'; using the Windows voice."


def download_piper_model(model: str, voices_dir: Path, progress=None) -> Path:
    """Fetch a voice's model and config into `voices_dir` (once; it is offline from then on).
    Writes to .part files and renames at the end, so an interrupted download never looks installed.
    `progress(fraction)` is called as the model file arrives."""
    m = next((m for m in PIPER_MODELS if m.model == model), None)
    if m is None:
        raise ValueError(f"Unknown voice: {model}")
    voices_dir = Path(voices_dir)
    voices_dir.mkdir(parents=True, exist_ok=True)
    for ext in (".onnx.json", ".onnx"):  # the small config first: a bad URL fails fast
        dest = voices_dir / f"{model}{ext}"
        part = dest.with_name(dest.name + ".part")
        with urllib.request.urlopen(PIPER_URL.format(path=m.path, model=model, ext=ext), timeout=30) as r, \
                open(part, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total and ext == ".onnx":
                    progress(done / total)
        part.replace(dest)
    return model_path(voices_dir, model)


# ---------------- synthesis (on the voice worker thread only) ----------------

class PiperSynth:
    def __init__(self, model_file: str):
        from piper import PiperVoice

        self._voice = PiperVoice.load(model_file)

    def render(self, text: str, rate: int) -> tuple[np.ndarray, int]:
        from piper import SynthesisConfig

        # rate -10..10 as in Windows: each step about 5% faster or slower
        cfg = SynthesisConfig(length_scale=0.95 ** rate)
        chunks = list(self._voice.synthesize(text, syn_config=cfg))
        if not chunks:
            return np.zeros(0), 22050
        sr = chunks[0].sample_rate
        pause = np.zeros(int(sr * 0.12))  # a breath between sentences
        parts = []
        for c in chunks:
            parts += [c.audio_float_array.astype(np.float64), pause]
        return np.concatenate(parts[:-1]), sr


class SapiSynth:
    SAMPLE_RATE = 22050
    _FORMAT_22KHZ_16BIT_MONO = 22  # SpeechAudioFormatType.SAFT22kHz16BitMono

    def __init__(self, name: str = ""):
        import ctypes

        ctypes.windll.ole32.CoInitialize(None)  # COM must be initialised on the speaking thread
        import comtypes.client

        self._client = comtypes.client
        self._voice = comtypes.client.CreateObject("SAPI.SpVoice")
        if name:
            tokens = self._voice.GetVoices(f"Name={name}")
            if tokens.Count:
                self._voice.Voice = tokens.Item(0)

    def render(self, text: str, rate: int) -> tuple[np.ndarray, int]:
        stream = self._client.CreateObject("SAPI.SpMemoryStream")
        fmt = stream.Format
        fmt.Type = self._FORMAT_22KHZ_16BIT_MONO
        stream.Format = fmt
        self._voice.AudioOutputStream = stream
        self._voice.Rate = int(rate)
        self._voice.Speak(text, 0)
        pcm = np.frombuffer(bytes(stream.GetData()), dtype="<i2").astype(np.float64) / 32768
        return pcm, self.SAMPLE_RATE


def make_synth(spec: str):
    kind, _, arg = (spec or "sapi:").partition(":")
    return PiperSynth(arg) if kind == "piper" else SapiSynth(arg)


def play_wav(data: bytes) -> None:
    import winsound

    winsound.PlaySound(data, winsound.SND_MEMORY)  # blocks this worker until the call is finished


class RadioEngine:
    """The VoiceCoach engine: render the words with the chosen voice, put them on the radio, play them.
    A voice that fails to load falls back to the Windows default voice rather than going silent."""

    def __init__(self, synth_factory=make_synth, player=play_wav):
        self._make, self._play = synth_factory, player
        self._spec: str | None = None
        self._synth = None
        self.rate, self.volume, self.radio = 0, 90, True
        self.fallback_reason: str | None = None

    def configure(self, rate: int, volume: int, voice: str | None = None, radio: bool = True) -> None:
        self.rate, self.volume, self.radio = rate, volume, radio
        spec = voice or "sapi:"
        if spec != self._spec or self._synth is None:
            self._spec = spec
            self._load()

    def _load(self) -> None:
        try:
            self._synth, self.fallback_reason = self._make(self._spec), None
        except Exception as exc:
            if self._spec == "sapi:":
                raise
            self.fallback_reason = f"Voice failed to load ({exc}); using the Windows voice."
            log.warning(self.fallback_reason)
            self._synth = self._make("sapi:")

    def speak(self, text: str) -> None:
        if self._synth is None:
            self._load()
        audio, sr = self._synth.render(text, self.rate)
        if not len(audio):
            return
        if self.radio:
            audio = radio_fx(audio, sr)
        else:
            peak = float(np.max(np.abs(audio)))
            audio = audio / peak * 0.9 if peak > 0 else audio
        self._play(wav_bytes(apply_volume(audio, self.volume), sr))
