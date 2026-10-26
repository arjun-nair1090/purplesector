"""Team-radio sound: the engineer's voice as it reaches a helmet, built from plain numpy.

A voice channel band-limited to roughly 300-3400 Hz, driven into soft saturation like a
compressed radio link, a faint hiss under the speech, and the static burst of the
channel opening and closing around it. Volume is applied last, here rather than in the
speech engine, so every voice sounds the same at the same setting.
"""
from __future__ import annotations

import io
import wave

import numpy as np

BAND_HZ = (300.0, 3400.0)


def bandpass(x: np.ndarray, sr: int, lo: float, hi: float, edge_hz: float = 120.0) -> np.ndarray:
    """Zero-phase band-pass in the frequency domain, with raised-cosine edges (no ringing at a hard cut)."""
    if not len(x):
        return x
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    gain = np.clip((f - (lo - edge_hz)) / (2 * edge_hz), 0, 1) * np.clip(((hi + edge_hz) - f) / (2 * edge_hz), 0, 1)
    gain = 0.5 - 0.5 * np.cos(np.pi * gain)
    return np.fft.irfft(spec * gain, len(x))


def _static(sr: int, seconds: float, rng: np.random.Generator, level: float) -> np.ndarray:
    """A burst of channel noise that dies away: the click and hiss of the radio keying up or dropping."""
    n = int(sr * seconds)
    env = np.exp(-np.linspace(0, 5, n))
    burst = bandpass(rng.normal(0, 1, n), sr, 900, 4200) * env * level
    burst[: max(1, sr // 400)] += level * 1.5  # the key click
    return burst


def radio_fx(audio: np.ndarray, sr: int, *, seed: int | None = None) -> np.ndarray:
    """Mono float audio in [-1, 1] -> the same speech over a team radio, with the channel opening and closing."""
    x = np.asarray(audio, dtype=np.float64)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0:
        x = x / peak
    rng = np.random.default_rng(seed)
    x = bandpass(x, sr, *BAND_HZ)
    x = np.tanh(2.6 * x) / np.tanh(2.6)          # a compressed, slightly overdriven link
    x = bandpass(x, sr, *BAND_HZ) * 0.85        # trim what the saturation added outside the band
    x = x + bandpass(rng.normal(0, 1, len(x)), sr, 1200, 4000) * 0.012  # hiss under the voice
    gap = np.zeros(int(sr * 0.06))
    out = np.concatenate([_static(sr, 0.09, rng, 0.08), gap, x, gap, _static(sr, 0.16, rng, 0.11)])
    return np.clip(out, -1.0, 1.0)


def apply_volume(audio: np.ndarray, volume: int) -> np.ndarray:
    """0..100 as perceived loudness: squared, so the low end of the slider is usable."""
    v = max(0, min(int(volume), 100)) / 100
    return np.asarray(audio, dtype=np.float64) * v * v


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    """16-bit mono WAV in memory, for winsound."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()
