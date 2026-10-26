"""The single source of user configuration, plus logging setup.

Precedence (lowest to highest): dataclass defaults < environment variables / .env
(PURPLESECTOR_<FIELD>) < data/settings.json (written by the Settings page).
Invalid values never crash the app: each invalid field falls back to its default
and the problem is reported.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import logging.handlers
import os
import threading
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from config.defaults import APP_HOME, ENV_FILE, ENV_PREFIX, SETTINGS_PATH

VERBOSITY = ("quiet", "normal", "detailed")
CONFIDENCE = ("low", "medium", "high")
MODES = ("live", "demo")
DEMO_SCENARIOS = ("standard", "quick_lap", "interruption")


@dataclass
class Settings:
    udp_host: str = "127.0.0.1"
    udp_port: int = 20777
    mode: str = "live"
    voice_enabled: bool = True
    voice_rate: int = 0                # -10 (slow) .. 10 (fast), 0 is the voice's own pace
    voice_volume: int = 90             # 0 .. 100
    voice_name: str = "piper:en_GB-northern_english_male-medium"  # piper:<model> or sapi:<Windows voice>
    voice_radio_fx: bool = True        # team-radio sound: band-limited, compressed, with the channel clicks
    driver_name: str = ""              # what the engineer calls you; empty = no name
    voice_min_interval_s: float = 4.0
    coach_verbosity: str = "normal"
    coach_min_confidence: str = "medium"
    coach_cooldown_s: float = 30.0
    sector_loss_threshold_s: float = 0.25
    segment_loss_threshold_s: float = 0.15
    brake_abrupt_ratio: float = 0.6
    history_window_s: int = 30
    ui_refresh_ms: int = 250
    recording_enabled: bool = True
    record_demo: bool = False
    data_dir: str = "data"
    demo_scenario: str = "standard"
    demo_speed: float = 1.0
    radio_button: int = 0              # the game's "UDP Action" button (1..12) that asks for a status call; 0 = off
    overlay_port: int = 8777           # localhost endpoint the always-on-top overlay reads
    corner_calls: bool = True          # a call after each corner that lost time to the delta reference
    corner_loss_threshold_s: float = 0.15
    race_radio: bool = True            # race calls: position, DRS battles, laps to go, tyres, fuel, flags, damage
    race_update_laps: int = 3          # a gaps update at the line every N laps in a race; 0 = only when it matters

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).expanduser()
        return p if p.is_absolute() else APP_HOME / p

    def validate(self) -> dict[str, str]:
        """Return {field: human-readable problem}; empty when valid."""
        e: dict[str, str] = {}
        try:
            ipaddress.IPv4Address(self.udp_host)
        except ValueError:
            e["udp_host"] = f"'{self.udp_host}' is not an IPv4 address (use 127.0.0.1 for the same PC)."
        if not 1024 <= self.udp_port <= 65535:
            e["udp_port"] = "Port must be between 1024 and 65535 (the game's default: 20777)."
        _choice(e, "mode", self.mode, MODES)
        _choice(e, "coach_verbosity", self.coach_verbosity, VERBOSITY)
        _choice(e, "coach_min_confidence", self.coach_min_confidence, CONFIDENCE)
        _choice(e, "demo_scenario", self.demo_scenario, DEMO_SCENARIOS)
        _range(e, "voice_rate", self.voice_rate, -10, 10)
        _range(e, "voice_volume", self.voice_volume, 0, 100)
        _range(e, "voice_min_interval_s", self.voice_min_interval_s, 0, 120)
        _range(e, "coach_cooldown_s", self.coach_cooldown_s, 0, 3600)
        _range(e, "sector_loss_threshold_s", self.sector_loss_threshold_s, 0.01, 10)
        _range(e, "segment_loss_threshold_s", self.segment_loss_threshold_s, 0.01, 10)
        _range(e, "brake_abrupt_ratio", self.brake_abrupt_ratio, 0.05, 0.95)
        _range(e, "history_window_s", self.history_window_s, 5, 300)
        _range(e, "ui_refresh_ms", self.ui_refresh_ms, 100, 2000)
        _range(e, "demo_speed", self.demo_speed, 0.25, 8)
        _range(e, "radio_button", self.radio_button, 0, 12)
        _range(e, "corner_loss_threshold_s", self.corner_loss_threshold_s, 0.02, 5)
        _range(e, "race_update_laps", self.race_update_laps, 0, 20)
        if self.voice_name.partition(":")[0] not in ("piper", "sapi"):
            e["voice_name"] = "Must be a Piper voice (piper:<model>) or a Windows voice (sapi:<name>)."
        if len(self.driver_name) > 24:
            e["driver_name"] = "Keep the name to 24 characters or fewer."
        if not 1024 <= self.overlay_port <= 65535:
            e["overlay_port"] = "Port must be between 1024 and 65535."
        elif self.overlay_port == self.udp_port:
            e["overlay_port"] = "Must differ from the UDP telemetry port."
        if not str(self.data_dir).strip():
            e["data_dir"] = "Data directory cannot be empty."
        else:
            try:
                self.data_path.mkdir(parents=True, exist_ok=True)
                probe = self.data_path / ".write_test"
                probe.write_text("ok")
                probe.unlink()
            except OSError as exc:
                e["data_dir"] = f"Data directory is not writable: {exc}"
        return e


def _choice(e, name, value, options):
    if value not in options:
        e[name] = f"Must be one of: {', '.join(options)}."


def _range(e, name, value, lo, hi):
    if not lo <= value <= hi:
        e[name] = f"Must be between {lo} and {hi}."


_FIELD_TYPES = {f.name: f.type for f in fields(Settings)}


def _coerce(name: str, value):
    kind = _FIELD_TYPES[name]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"'{value}' is not true/false")
    if kind == "int":
        if isinstance(value, bool) or float(value) != int(float(value)):
            raise ValueError(f"'{value}' is not a whole number")
        return int(float(value))
    if kind == "float":
        if isinstance(value, bool):
            raise ValueError(f"'{value}' is not a number")
        return float(value)
    return str(value)


def build_settings(raw: dict) -> tuple[Settings, dict[str, str]]:
    """Build Settings from loosely-typed input; invalid fields fall back to defaults."""
    defaults = Settings()
    values, errors = asdict(defaults), {}
    for name, value in raw.items():
        if name not in _FIELD_TYPES:
            continue
        try:
            values[name] = _coerce(name, value)
        except (TypeError, ValueError) as exc:
            errors[name] = str(exc)
    s = Settings(**values)
    for name, msg in s.validate().items():
        errors[name] = msg
        setattr(s, name, getattr(defaults, name))
    return s, errors


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_settings(path: Path = SETTINGS_PATH, env: dict | None = None) -> tuple[Settings, dict[str, str]]:
    env_vars = {**_read_env_file(ENV_FILE), **(os.environ if env is None else env)}
    raw = {k[len(ENV_PREFIX):].lower(): v for k, v in env_vars.items() if k.startswith(ENV_PREFIX)}
    errors = {}
    if path.exists():
        try:
            raw.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            errors["settings_file"] = f"Could not read {path.name} ({exc}); using defaults."
    s, field_errors = build_settings(raw)
    return s, {**errors, **field_errors}


def save_settings(s: Settings, path: Path = SETTINGS_PATH) -> dict[str, str]:
    errors = s.validate()
    if not errors:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
        tmp.replace(path)
    return errors


class RecentErrorsHandler(logging.Handler):
    """Keeps the last WARNING+ log records for the diagnostics panel."""

    def __init__(self, capacity: int = 50):
        super().__init__(logging.WARNING)
        self.records: deque[tuple[float, str, str]] = deque(maxlen=capacity)
        self._lock_ = threading.Lock()

    def emit(self, record):
        with self._lock_:
            self.records.append((record.created, record.levelname, self.format(record)))

    def snapshot(self):
        with self._lock_:
            return list(self.records)


recent_errors = RecentErrorsHandler()


def setup_logging(log_dir: Path) -> None:
    root = logging.getLogger()
    if any(isinstance(h, RecentErrorsHandler) for h in root.handlers):
        return  # already configured (Streamlit reruns)
    root.setLevel(logging.INFO)
    # Windows' proactor loop logs WinError 10054 tracebacks whenever a browser tab closes its websocket.
    # ponytail: blanket-silences Streamlit's asyncio logger; narrow it with a Filter if asyncio errors matter.
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    recent_errors.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(recent_errors)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "purplesector.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        root.warning("File logging unavailable in %s", log_dir)
