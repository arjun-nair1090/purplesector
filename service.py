"""The single shared application service: owns every subsystem and the data flow.

    UDP receiver ──┐                               ┌─> TelemetryState (UI reads)
                   ├─> AppService.ingest(packet) ──┼─> LapTracker ─> Coach ─> VoiceCoach
    DemoRunner ────┘   (tagged live/demo)          ├─> SessionStats
                                                   └─> TelemetryWriter (DB thread)

Each subsystem call in ingest() is isolated: a failure is logged and the others keep
running. Packets whose source differs from the current mode are dropped, so demo
and live data can never mix.
"""
from __future__ import annotations

import atexit
import logging
import threading
import uuid
from dataclasses import asdict

from analysis.coach import Category, Coach, CoachConfig, Priority
from analysis.lap_comparison import delta_at, reference_curve
from analysis.lap_tracker import LapEventKind, LapTracker
from analysis.statistics import SessionStats, consistency_s, theoretical_best_ms
from config.settings import Settings, load_settings, save_settings, setup_logging
from demo.simulator import LOOPBACK_TRACK_NAME, DemoRunner, is_synthetic_uid
from demo.simulator import TRACK_NAME as DEMO_TRACK_NAME
from storage.database import init_db
from storage.session_repository import SessionRepository
from storage.telemetry_writer import TelemetryWriter
from telemetry.connection import ConnectionMonitor, ConnectionState
from telemetry.packet_models import EVENT_CODES, PIT_STATUS, Packet, PacketId
from telemetry.receiver import UdpReceiver
from telemetry.state import TelemetryState
from voice.speaker import VoiceCoach, default_engine_factory

log = logging.getLogger(__name__)
RESTART_RECEIVER = ("udp_host", "udp_port", "telemetry_format")


class AppService:
    def __init__(self, settings: Settings, settings_errors: dict | None = None, *,
                 engine_factory=default_engine_factory):
        self.settings = settings
        self.settings_errors = settings_errors or {}
        self._lock = threading.RLock()
        self.monitor = ConnectionMonitor()
        self.state = TelemetryState(settings.history_window_s)
        self.tracker = LapTracker()
        self.stats = SessionStats()
        self.coach = Coach(CoachConfig.from_settings(settings))
        self.voice = VoiceCoach(engine_factory=engine_factory, enabled=settings.voice_enabled,
                                rate=settings.voice_rate, volume=settings.voice_volume,
                                min_interval_s=settings.voice_min_interval_s)
        self.receiver: UdpReceiver | None = None
        self.demo: DemoRunner | None = None
        self._demo_runs = 0
        self.mode = settings.mode
        self.session_uid: int | None = None
        self.loopback_test = False  # synthetic packets over real UDP
        self._reset_pace()
        self.recording_key: str | None = None
        self._recorded_meta: dict = {}
        self._last_frames: dict[int, int] = {}
        self._last_conn_state = ConnectionState.DISCONNECTED
        self._watchdog_stop = threading.Event()
        self._open_storage()

    # ---------------- lifecycle ----------------
    def _open_storage(self) -> None:
        db_path = self.settings.data_path / "purplesector.db"
        recovered = init_db(db_path)
        if recovered:
            log.warning("Marked %d session(s) from a previous run as interrupted", recovered)
        self.repo = SessionRepository(db_path)
        self.writer = TelemetryWriter(self.repo)

    def start(self) -> None:
        self.voice.start()
        self.writer.start()
        self._start_source()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

    def shutdown(self) -> None:
        self._watchdog_stop.set()
        self._stop_source()
        self._end_recording()
        self.writer.stop()
        self.voice.stop()
        log.info("PurpleSector shut down cleanly")

    def _start_source(self) -> None:
        self.monitor.reset(self.mode)
        if self.mode == "live":
            s = self.settings
            self.receiver = UdpReceiver(s.udp_host, s.udp_port, self.monitor, lambda p: self.ingest(p, "live"),
                                        (s.telemetry_format,))
            self.receiver.start()

    def _stop_source(self) -> None:
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
        self.stop_demo()

    def restart_receiver(self) -> None:
        """Controlled restart, e.g. after the port was held by another app."""
        with self._lock:
            if self.mode == "live":
                self._stop_source()
                self._start_source()

    # ---------------- demo control ----------------
    def start_demo(self) -> None:
        with self._lock:
            if self.mode != "demo":
                return
            self.stop_demo()
            self._reset_session(None)
            self.monitor.reset("demo")
            self._demo_runs += 1
            self.demo = DemoRunner(self._ingest_demo, self.settings.demo_scenario, self.settings.demo_speed,
                                   self._demo_runs)
            self.monitor.set_running(True, "demo simulator")
            self.demo.start()

    def stop_demo(self) -> None:
        if self.demo:
            self.demo.stop()
            self.demo = None
            self.monitor.set_running(False)
            self._end_recording()

    def _ingest_demo(self, packet: Packet) -> None:
        self.monitor.record_valid(packet.packet_id.name)
        self.ingest(packet, "demo")

    # ---------------- data path ----------------
    def ingest(self, packet: Packet, source: str) -> None:
        with self._lock:
            if source != self.mode:
                return  # strict live/demo separation
            h = packet.header
            if h.session_uid != self.session_uid:
                self._reset_session(h.session_uid)
            pid = packet.packet_id
            if self._last_frames.get(pid) == h.frame_identifier and pid != PacketId.EVENT:
                self.monitor.record_duplicate()
                return
            self._last_frames[pid] = h.frame_identifier
            if pid == PacketId.SESSION_HISTORY and packet.data.header.car_idx != h.player_car_index:
                return  # history packets rotate through all cars
            self._guard("state", self.state.apply, packet)
            if pid == PacketId.LAP_DATA:
                events = self._guard("lap tracker", self.tracker.on_lap_data, packet.data) or []
                for ev in events:
                    self._guard("lap event", self._on_lap_event, ev)
                self.delta_s = delta_at(self._ref_curve, packet.data.lap_distance, packet.data.current_lap_time_ms)
            elif pid == PacketId.CAR_TELEMETRY:
                d = packet.data
                self._guard("lap tracker", self.tracker.on_telemetry, h.session_time, d)
                self._guard("stats", self.stats.add, d.speed, d.throttle * 100, d.brake * 100)
            elif pid == PacketId.SESSION:
                self._guard("recorder", self._update_recording_meta)
                self._guard("pace", self._ensure_alltime, self.state.snapshot().track_name)
            elif pid == PacketId.EVENT:
                self._guard("event", self._on_game_event, packet.data)

    def _guard(self, name, fn, *args):
        try:
            return fn(*args)
        except Exception:
            log.exception("%s failed", name)
            return None

    def _reset_session(self, session_uid: int | None) -> None:
        self._end_recording()
        self.session_uid = session_uid
        self.loopback_test = self.mode == "live" and session_uid is not None and is_synthetic_uid(session_uid)
        self.state.reset("loopback" if self.loopback_test else self.mode)
        if self.mode == "demo" or self.loopback_test:
            self.state.set_track_name(DEMO_TRACK_NAME if self.mode == "demo" else LOOPBACK_TRACK_NAME)
        self.tracker.reset()
        self.stats.reset()
        self.coach.reset()
        self._last_frames.clear()
        self._reset_pace()
        if session_uid is not None:
            self._begin_recording(session_uid)
            if self.mode == "demo":
                self._ensure_alltime(DEMO_TRACK_NAME)

    # ---------------- pace: live delta and all-time best ----------------
    def _reset_pace(self) -> None:
        self._history_best_ms: int | None = None  # best recorded lap on this track from earlier sessions
        self._alltime_curve = None   # (lap ms, curve) of the fastest fully recorded earlier lap on this track
        self._session_curve = None   # (lap ms, curve) of this session's best fully recorded lap
        self._ref_curve = None
        self.delta_ref: str | None = None
        self.delta_s: float | None = None
        self._pace_track: str | None = None

    def _ensure_alltime(self, track: str | None) -> None:
        """Load the recorded best for this track once per session, off the packet thread."""
        if not track or track == self._pace_track:
            return
        self._pace_track = track
        threading.Thread(target=self._load_alltime, args=(track, self.mode, self.session_uid),
                         name="pace-loader", daemon=True).start()

    def _load_alltime(self, track: str, source: str, session_uid: int | None) -> None:
        try:
            best = self.repo.best_lap_ms(track, source)
            ref = self.repo.best_reference_lap(track, source)
            curve = None
            if ref is not None:
                c = reference_curve(list(ref[1].itertuples(index=False, name=None)))
                curve = (ref[0], c) if c is not None else None
        except Exception:
            log.exception("Loading recorded best for %s failed", track)
            return
        with self._lock:
            if session_uid != self.session_uid:
                return  # the session changed while we were loading
            self._history_best_ms = best
            self._alltime_curve = curve
            self._pick_reference()

    def _pick_reference(self) -> None:
        """Delta reference = the faster of the recorded PB and this session's best (both fully recorded).
        On a tie this session's lap wins, so "PB (recorded)" always means a lap from an earlier session."""
        options = [(ref[0], rank, ref[1], label) for rank, ref, label in
                   ((0, self._session_curve, "Session best"), (1, self._alltime_curve, "PB (recorded)")) if ref]
        if options:
            _, _, self._ref_curve, self.delta_ref = min(options, key=lambda o: (o[0], o[1]))
        else:
            self._ref_curve = self.delta_ref = None

    def _update_pace(self, lap) -> None:
        if lap is self.tracker.best_lap() and lap.samples_complete:
            c = reference_curve(lap.samples)
            if c is not None:
                self._session_curve = (lap.lap_time_ms, c)
                self._pick_reference()

    @property
    def alltime_best_ms(self) -> int | None:
        """Best lap on this track including this session, or None when there is no earlier history."""
        if self._history_best_ms is None:
            return None
        best = self.tracker.best_lap()
        return min(self._history_best_ms, best.lap_time_ms) if best else self._history_best_ms

    # ---------------- coaching ----------------
    def _speak(self, events) -> None:
        for e in events:
            self.voice.say(e.spoken, int(e.priority), e.key)

    def _on_lap_event(self, ev) -> None:
        k = ev.kind
        if k == LapEventKind.LAP_COMPLETED:
            if self.recording_key:
                self.writer.save_lap(self.recording_key, ev.lap)
            self._speak(self.coach.on_lap_completed(ev.lap, self.tracker, self._history_best_ms,
                                                    self.state.snapshot().track_name))
            self._update_pace(ev.lap)
        elif k == LapEventKind.LAP_INVALIDATED:
            self._speak(self.coach.simple(f"invalid:{ev.lap_number}", Category.LAP_PERFORMANCE,
                                          f"Lap {ev.lap_number} invalidated", Priority.HIGH,
                                          "Game lap-invalid flag", "Lap invalidated."))
        elif k in (LapEventKind.PIT_ENTRY, LapEventKind.PIT_EXIT):
            entry = k == LapEventKind.PIT_ENTRY
            self._speak(self.coach.simple(f"pit:{k.name}:{ev.lap_number}", Category.SESSION,
                                          "Pit entry" if entry else "Pit exit", Priority.LOW,
                                          f"Game pit status ({PIT_STATUS[1] if entry else PIT_STATUS[0]})"))

    def _on_game_event(self, ev) -> None:
        if ev.code in ("SSTA", "SEND", "CHQF"):
            text = EVENT_CODES[ev.code]
            self._speak(self.coach.simple(f"event:{ev.code}", Category.SESSION, text, Priority.LOW,
                                          f"Game event {ev.code}", text + "."))
            if ev.code == "SEND":
                self._end_recording(say=True)

    def _watchdog(self) -> None:
        while not self._watchdog_stop.wait(1.0):
            state = self.monitor.state()
            prev, self._last_conn_state = self._last_conn_state, state
            with self._lock:
                self._on_connection_change(prev, state)

    def _on_connection_change(self, prev: ConnectionState, state: ConnectionState) -> None:
        if prev == ConnectionState.CONNECTED and state == ConnectionState.RECONNECTING:
            self._speak(self.coach.simple("telemetry_lost", Category.SYSTEM, "Telemetry lost", Priority.HIGH,
                                          "No valid packets for 2 s", "Telemetry lost."))
        elif prev == ConnectionState.RECONNECTING and state == ConnectionState.CONNECTED:
            self.coach.simple("telemetry_back", Category.SYSTEM, "Telemetry restored", Priority.HIGH,
                              "Packets resumed")

    # ---------------- recording ----------------
    @property
    def recording(self) -> bool:
        return self.recording_key is not None

    def _should_record(self) -> bool:
        s = self.settings
        if self.loopback_test:
            return False  # synthetic loopback packets are never stored as a game session
        return s.recording_enabled and (self.mode == "live" or s.record_demo)

    def _begin_recording(self, session_uid: int) -> None:
        if not self._should_record():
            return
        self.recording_key = uuid.uuid4().hex
        self._recorded_meta = {}
        self.writer.start_session(self.recording_key, session_uid, self.mode,
                                  DEMO_TRACK_NAME if self.mode == "demo" else None)

    def _update_recording_meta(self) -> None:
        if not self.recording_key:
            return
        snap = self.state.snapshot()
        meta = {"session_type": snap.session_type, "track_id": snap.track_id, "track_name": snap.track_name}
        if meta != self._recorded_meta:
            self._recorded_meta = meta
            self.writer.update_session(self.recording_key, **meta)

    def _end_recording(self, say: bool = False) -> None:
        if self.recording_key:
            self.writer.end_session(self.recording_key)
            self.recording_key = None
            if say:
                self._speak(self.coach.simple("saved", Category.SYSTEM, "Session saved", Priority.LOW,
                                              "Recording closed", "Session saved."))

    def set_recording(self, enabled: bool) -> None:
        with self._lock:
            self.update_settings(recording_enabled=enabled)
            if not enabled:
                self._end_recording(say=True)
            elif not self.recording_key and self.session_uid is not None and self._should_record():
                self._begin_recording(self.session_uid)

    # ---------------- settings ----------------
    def update_settings(self, **changes) -> dict[str, str]:
        new = Settings(**{**asdict(self.settings), **changes})
        return self.apply_settings(new)

    def apply_settings(self, new: Settings) -> dict[str, str]:
        """Validate, persist and apply. Receiver/storage changes go through a controlled restart."""
        errors = save_settings(new)
        if errors:
            return errors
        with self._lock:
            old, self.settings = self.settings, new
            self.coach.config = CoachConfig.from_settings(new)
            self.voice.configure(enabled=new.voice_enabled, rate=new.voice_rate, volume=new.voice_volume,
                                 min_interval_s=new.voice_min_interval_s)
            if new.history_window_s != old.history_window_s:
                self.state.set_history_window(new.history_window_s)
            if self.demo and (new.demo_speed != old.demo_speed):
                self.demo.speed = new.demo_speed
            mode_changed = new.mode != old.mode
            if new.data_dir != old.data_dir:
                self._stop_source()
                self._end_recording()
                self.writer.stop()
                self._open_storage()
                self.writer.start()
                self._reset_session(None)
                self._start_source()
            elif mode_changed or (new.mode == "live" and any(getattr(new, f) != getattr(old, f)
                                                              for f in RESTART_RECEIVER)):
                self._stop_source()
                self.mode = new.mode
                self._reset_session(None)
                self._start_source()
                if new.mode == "demo":
                    self.start_demo()
            if not self._should_record():
                self._end_recording()
        return {}

    def reset_settings(self) -> dict[str, str]:
        return self.apply_settings(Settings())

    # ---------------- UI read helpers ----------------
    def laps_view(self) -> dict:
        with self._lock:
            laps = list(self.tracker.laps)
            best = self.tracker.best_lap()
            return {"laps": laps, "best": best, "best_sectors": self.tracker.best_sectors(),
                    "current_lap": self.tracker.current_lap_number,
                    "delta_s": self.delta_s, "delta_ref": self.delta_ref, "alltime_best_ms": self.alltime_best_ms,
                    "theoretical_best_ms": theoretical_best_ms(laps), "consistency_s": consistency_s(laps)}

    def coach_events(self) -> list:
        with self._lock:
            return list(self.coach.events)


_service: AppService | None = None
_service_lock = threading.Lock()


def get_service() -> AppService:
    """Process-wide singleton: Streamlit reruns never create a second receiver/socket."""
    global _service
    with _service_lock:
        if _service is None:
            settings, errors = load_settings()
            setup_logging(settings.data_path / "logs")
            for field_name, msg in errors.items():
                log.warning("Setting %s: %s (default used)", field_name, msg)
            _service = AppService(settings, errors)
            _service.start()
            if settings.mode == "demo":
                _service.start_demo()
            atexit.register(_service.shutdown)
        return _service
