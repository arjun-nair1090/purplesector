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
from datetime import datetime
from pathlib import Path

import overlay
from analysis.coach import Category, Coach, CoachConfig, Priority, radio_report
from analysis.corner_feedback import CornerCoach
from analysis.field import FieldTiming
from analysis.gaps import RACE_SESSIONS, GapTracker
from analysis.lap_comparison import delta_at, reference_curve, samples_frame
from analysis.lap_tracker import LapEventKind, LapTracker
from analysis.race_control import RaceControl
from analysis.statistics import SessionStats, consistency_s, theoretical_best_ms
from analysis.strategy import LapResources, projection
from analysis.track_map import TrackMap
from analysis.weather import WeatherWatch
from config.settings import Settings, load_settings, save_settings, setup_logging
from demo.simulator import LOOPBACK_TRACK_NAME, DemoRunner, is_synthetic_uid
from demo.simulator import TRACK_NAME as DEMO_TRACK_NAME
from storage.database import init_db
from storage.session_repository import SessionRepository
from storage.telemetry_writer import TelemetryWriter
from telemetry.capture import CaptureWriter
from telemetry.connection import ConnectionMonitor, ConnectionState
from telemetry.packet_models import (
    EVENT_CODES,
    PIT_STATUS,
    RESULT_ACTIVE,
    RESULT_FINISHED,
    Packet,
    PacketId,
    udp_action_bit,
)
from telemetry.receiver import UdpReceiver
from telemetry.state import TelemetryState
from voice.speaker import VoiceCoach, default_engine_factory

log = logging.getLogger(__name__)
RESTART_RECEIVER = ("udp_host", "udp_port")


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
        self.gaps = GapTracker()
        self.resources = LapResources()
        self.weather = WeatherWatch()
        self.race_control = RaceControl()
        self.field = FieldTiming()
        self.corners = CornerCoach(settings.corner_loss_threshold_s)
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
        self.overlay_server = None
        self.capture: CaptureWriter | None = None
        self._radio_n = 0
        self._reset_pace()
        self._reset_race()
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
        self._start_overlay()
        self._start_source()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

    def shutdown(self) -> None:
        self._watchdog_stop.set()
        self.stop_capture()
        self._stop_source()
        self._end_recording()
        self.writer.stop()
        self.voice.stop()
        self._stop_overlay()
        log.info("PurpleSector shut down cleanly")

    def _start_overlay(self) -> None:
        self.overlay_server = overlay.serve(self.settings.overlay_port, self.overlay_view)

    def _stop_overlay(self) -> None:
        if self.overlay_server:
            self.overlay_server.shutdown()
            self.overlay_server.server_close()
            self.overlay_server = None

    def _start_source(self) -> None:
        self.monitor.reset(self.mode)
        if self.mode == "live":
            s = self.settings
            self.receiver = UdpReceiver(s.udp_host, s.udp_port, self.monitor, lambda p: self.ingest(p, "live"))
            self.receiver.on_datagram = self._capture_datagram
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

    # ---------------- raw capture (verifying against a real session) ----------------
    def start_capture(self) -> Path | None:
        """Write every datagram from the game to data/captures until stopped. None if already capturing."""
        with self._lock:
            if self.capture is not None:
                return None
            path = self.settings.data_path / "captures" / f"capture_{datetime.now():%Y%m%d_%H%M%S}.pscap.gz"
            self.capture = CaptureWriter(path)
            log.info("Capturing raw packets to %s", path)
            return path

    def stop_capture(self) -> None:
        with self._lock:
            cap, self.capture = self.capture, None
        if cap is not None:
            cap.close()
            log.info("Capture closed: %d datagrams, %s", cap.count, cap.path)

    def _capture_datagram(self, data: bytes) -> None:
        cap = self.capture
        if cap is not None:
            cap.write(data)

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
            # session history rotates through the cars, possibly several in one frame: dedupe per car
            key = (pid, packet.data.header.car_idx) if pid == PacketId.SESSION_HISTORY else pid
            if self._last_frames.get(key) == h.frame_identifier and pid != PacketId.EVENT:
                self.monitor.record_duplicate()
                return
            self._last_frames[key] = h.frame_identifier
            if pid == PacketId.SESSION_HISTORY:
                self._guard("field", self.field.on_history, packet.data)
                if packet.data.header.car_idx != h.player_car_index:
                    return  # history packets rotate through all cars; only the player's feeds the state
            self._guard("state", self.state.apply, packet)
            self._format = h.packet_format
            if pid == PacketId.LAP_DATA:
                self._player_idx = h.player_car_index
                if packet.cars:
                    self._lap_cars = packet.cars
                    self._guard("gaps", self.gaps.on_lap_data, h.session_time, packet.cars)
                events = self._guard("lap tracker", self.tracker.on_lap_data, packet.data) or []
                for ev in events:
                    self._guard("lap event", self._on_lap_event, ev)
                self.delta_s = delta_at(self._ref_curve, packet.data.lap_distance, packet.data.current_lap_time_ms)
            elif pid == PacketId.CAR_TELEMETRY:
                d = packet.data
                self._guard("lap tracker", self.tracker.on_telemetry, h.session_time, d)
                self._guard("corners", self._check_corner)
                self._guard("stats", self.stats.add, d.speed, d.throttle * 100, d.brake * 100)
            elif pid == PacketId.MOTION:
                self._guard("lap tracker", self.tracker.on_motion, packet.data)
                if packet.cars:
                    self._car_positions = tuple(c.position for c in packet.cars)
            elif pid == PacketId.PARTICIPANTS:
                if packet.cars:
                    self._guard("gaps", self.gaps.set_names, packet.cars)
                    self._teams = {i: p.team_id for i, p in enumerate(packet.cars)}
            elif pid == PacketId.SESSION:
                self._guard("recorder", self._update_recording_meta)
                self._guard("weather", self._check_weather)
                self._guard("race control", self._race_control_session, h)
                self._guard("pace", self._ensure_alltime, self.state.snapshot().track_name)
            elif pid == PacketId.EVENT:
                self._guard("event", self._on_game_event, packet.data)
                self._guard("race control", self._race_control_event, packet.data, h)

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
        self.corners.reset()
        self.stats.reset()
        self.coach.reset()
        self._last_frames.clear()
        self._reset_pace()
        self._reset_race()
        if session_uid is not None:
            self._begin_recording(session_uid)
            if self.mode == "demo":
                self._ensure_alltime(DEMO_TRACK_NAME)

    # ---------------- pace: live delta and all-time best ----------------
    def _reset_pace(self) -> None:
        self._history_best_ms: int | None = None  # best recorded lap on this track from earlier sessions
        self._alltime_curve = None   # (lap ms, curve, samples) of the fastest fully recorded earlier lap here
        self._session_curve = None   # (lap ms, curve, samples) of this session's best fully recorded lap
        self._ref_curve = None
        self._ref_frame = None       # samples of the delta reference, for the corner calls
        self.corners.clear_reference()
        self.delta_ref: str | None = None
        self.delta_s: float | None = None
        self._pace_track: str | None = None

    def _reset_race(self) -> None:
        self.gaps.reset()
        self.resources.reset()
        self.weather.reset()
        self.race_control.reset()
        self.field.reset()
        self.track_map: TrackMap | None = None
        self.track_map_source: str | None = None
        self._player_idx = 0
        self._lap_cars = None
        self._car_positions = None
        self._teams: dict[int, int] = {}
        self._format = 2022
        self._buttons = 0

    def _set_track_map(self, df, source: str) -> None:
        """First lap with positions this session draws the map; kept for the session so corner numbers
        stay the same in every message."""
        if self.track_map is None:
            m = TrackMap.from_samples(df)
            if m is not None:
                self.track_map, self.track_map_source = m, source
                self._refresh_corner_reference()

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
                curve = (ref[0], c, ref[1]) if c is not None else None
        except Exception:
            log.exception("Loading recorded best for %s failed", track)
            return
        with self._lock:
            if session_uid != self.session_uid:
                return  # the session changed while we were loading
            self._history_best_ms = best
            self._alltime_curve = curve
            self._pick_reference()
            if ref is not None:
                self._guard("track map", self._set_track_map, ref[1], "your recorded best lap")

    def _pick_reference(self) -> None:
        """Delta reference = the faster of the recorded PB and this session's best (both fully recorded).
        On a tie this session's lap wins, so "PB (recorded)" always means a lap from an earlier session."""
        options = [(ref[0], rank, ref[1], ref[2], label) for rank, ref, label in
                   ((0, self._session_curve, "Session best"), (1, self._alltime_curve, "PB (recorded)")) if ref]
        if options:
            _, _, self._ref_curve, self._ref_frame, self.delta_ref = min(options, key=lambda o: (o[0], o[1]))
        else:
            self._ref_curve = self._ref_frame = self.delta_ref = None
        self._refresh_corner_reference()

    def _refresh_corner_reference(self) -> None:
        if self._ref_frame is not None and self.track_map is not None and self.track_map.corners:
            self.corners.set_reference(self.track_map.corners, self._ref_frame, self.delta_ref)
        else:
            self.corners.clear_reference()

    def _check_corner(self) -> None:
        # nothing to learn from a pit or out lap, or behind a safety car
        if (not self.settings.corner_calls or self.tracker.current_lap_off_pace
                or self.state.snapshot().safety_car_status in (1, 2)):
            return
        r = self.corners.on_progress(self.tracker.current_lap_number, self.tracker.current_samples)
        if r is not None:
            e = r.event()
            if self.coach.accept(e):
                self._speak([e])

    def _update_pace(self, lap) -> None:
        # a pit or out lap would draw the pit lane into the outline
        if lap.samples_complete and not lap.pit_lap and not lap.out_lap and self.track_map is None:
            self._set_track_map(samples_frame(lap.samples), f"lap {lap.lap_number}")
        if lap is self.tracker.best_lap() and lap.samples_complete:
            c = reference_curve(lap.samples)
            if c is not None:
                self._session_curve = (lap.lap_time_ms, c, samples_frame(lap.samples))
                self._pick_reference()

    def _on_laps_undone(self, lap_number: int) -> None:
        """A flashback back across the line removed laps from `lap_number` on: re-derive what depended
        on them, and take them out of the recording too."""
        if self.recording_key:
            self.writer.undo_laps_from(self.recording_key, lap_number)
        self.resources.reset()  # the stored line reading belongs to a lap that no longer happened
        best = self.tracker.best_lap()
        c = reference_curve(best.samples) if best is not None and best.samples_complete else None
        self._session_curve = (best.lap_time_ms, c, samples_frame(best.samples)) if c is not None else None
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
            snap = self.state.snapshot()
            ev.lap.fuel_used_kg, ev.lap.tyre_wear_pct = self.resources.line_crossed(snap.fuel_kg, snap.tyre_wear_pct)
            ev.lap.tyre_age_laps, ev.lap.tyre_compound, ev.lap.setup = snap.tyre_age_laps, snap.tyre_visual, snap.setup
            self._guard("gaps", self.gaps.on_player_lap, self._player_idx)
            if self.recording_key:
                self.writer.save_lap(self.recording_key, ev.lap)
            self._speak(self.coach.on_lap_completed(ev.lap, self.tracker, self._history_best_ms, snap.track_name,
                                                    self.track_map.corners if self.track_map else None))
            self._update_pace(ev.lap)
        elif k == LapEventKind.FLASHBACK and ev.crossed_line:
            self._on_laps_undone(ev.lap_number)
        elif k == LapEventKind.LAP_INVALIDATED:
            self._speak(self.coach.simple(f"invalid:{ev.lap_number}", Category.LAP_PERFORMANCE,
                                          f"Lap {ev.lap_number} invalidated", Priority.HIGH,
                                          "Game lap-invalid flag", "Lap invalidated."))
        elif k in (LapEventKind.PIT_ENTRY, LapEventKind.PIT_EXIT):
            entry = k == LapEventKind.PIT_ENTRY
            self._speak(self.coach.simple(f"pit:{k.name}:{ev.lap_number}", Category.SESSION,
                                          "Pit entry" if entry else "Pit exit", Priority.LOW,
                                          f"Game pit status ({PIT_STATUS[1] if entry else PIT_STATUS[0]})"))

    def _race_control_session(self, h) -> None:
        snap = self.state.snapshot()
        self._log_race_control(self.race_control.on_safety_car(snap.safety_car_status, h.session_time,
                                                               snap.lap_number))

    def _race_control_event(self, ev, h) -> None:
        lap = self.state.snapshot().lap_number
        if ev.code == "FTLP" and ev.details is not None and ev.details.vehicle_idx == h.player_car_index                 and self.tracker.laps:
            lap = self.tracker.laps[-1].lap_number  # your fastest lap is the one just completed
        self._log_race_control(self.race_control.on_event(ev, session_time=h.session_time, lap=lap,
                                                          names=self.gaps.names, player=h.player_car_index))

    def _log_race_control(self, entry) -> None:
        if entry is None:
            return
        if self.recording_key:
            self.writer.add_event(self.recording_key, entry.session_time, entry.lap, entry.code, entry.message)
        if entry.speech:
            self._speak(self.coach.simple(f"rc:{entry.code}:{entry.session_time:.1f}", Category.RACE_CONTROL,
                                          entry.message, Priority.HIGH, f"Game event {entry.code}", entry.speech))

    def _check_weather(self) -> None:
        snap = self.state.snapshot()
        self._speak([e for e in self.weather.check(snap.weather_code, snap.forecast, snap.forecast_accuracy)
                     if self.coach.accept(e)])

    def _on_game_event(self, ev) -> None:
        if ev.code == "BUTN" and ev.details is not None:
            held = ev.details.button_status
            n = self.settings.radio_button
            if n and held & udp_action_bit(n) and not self._buttons & udp_action_bit(n):
                self.radio_check()  # on press, not while held
            self._buttons = held
            return
        if ev.code == "FLBK" and ev.details is not None:
            t = ev.details.flashback_session_time
            self.race_control.rewind(t)
            if self.recording_key:
                self.writer.undo_events_after(self.recording_key, t)
            return
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
        meta = {"session_type": snap.session_type, "track_id": snap.track_id, "track_name": snap.track_name,
                "driver_name": snap.player_name}
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
            self.corners.threshold_s = new.corner_loss_threshold_s
            self.voice.configure(enabled=new.voice_enabled, rate=new.voice_rate, volume=new.voice_volume,
                                 min_interval_s=new.voice_min_interval_s)
            if new.history_window_s != old.history_window_s:
                self.state.set_history_window(new.history_window_s)
            if self.demo and (new.demo_speed != old.demo_speed):
                self.demo.speed = new.demo_speed
            if new.overlay_port != old.overlay_port:
                self._stop_overlay()
                self._start_overlay()
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

    # ---------------- race: gaps, strategy, map, radio, overlay ----------------
    def gaps_view(self) -> dict:
        """Cars directly ahead/behind by race position. Positions in practice and qualifying are by lap
        time, not track position, so gaps are only reported in races."""
        with self._lock:
            race = self.state.snapshot().session_type in RACE_SESSIONS
            view = self.gaps.view(self._player_idx) if race else {"ahead": None, "behind": None}
            cars = (sum(c.result_status in (RESULT_ACTIVE, RESULT_FINISHED) for c in self._lap_cars)
                    if self._lap_cars else None)
            return {"race": race, "cars": cars or None, **view}

    def strategy_view(self) -> dict:
        with self._lock:
            laps = list(self.tracker.laps)
            snap = self.state.snapshot()
            p = projection(laps, snap)
            fuel_margin, fuel_source = p["fuel_margin_laps"], "measured"
            if fuel_margin is None and snap.fuel_laps_remaining is not None:
                fuel_margin, fuel_source = snap.fuel_laps_remaining, "game estimate"
            return {**p, "fuel_margin": fuel_margin, "fuel_source": fuel_source if fuel_margin is not None else None,
                    "laps": [lap for lap in laps if lap.fuel_used_kg is not None or lap.tyre_wear_pct is not None][-5:],
                    "pit_ideal": snap.pit_window_ideal_lap, "pit_latest": snap.pit_window_latest_lap,
                    "pit_rejoin": snap.pit_rejoin_position, "lap_number": snap.lap_number}

    def field_view(self) -> dict:
        """Timing tower and your best sectors against the session's fastest."""
        with self._lock:
            race = self.state.snapshot().session_type in RACE_SESSIONS
            names = self.gaps.names
            return {"race": race, "tower": self.field.tower(self._lap_cars, names, self._player_idx, race, self.gaps),
                    "versus": self.field.versus(self._player_idx, names), "teams": dict(self._teams),
                    "year": self._format}

    def map_view(self) -> dict:
        """Outline plus live dots: (u, v, role) for every active car, role in player/ahead/behind/other."""
        with self._lock:
            m, positions, cars = self.track_map, self._car_positions, self._lap_cars
            dots = []
            if m is not None and positions is not None:
                g = self.gaps_view()
                ahead = g["ahead"].car_index if g["ahead"] else None
                behind = g["behind"].car_index if g["behind"] else None
                for i, pos in enumerate(positions):
                    active = cars[i].result_status == RESULT_ACTIVE if cars else i == self._player_idx
                    if not active:
                        continue
                    role = ("player" if i == self._player_idx else "ahead" if i == ahead
                            else "behind" if i == behind else "other")
                    dots.append((*m.project(pos), role))
            return {"map": m, "source": self.track_map_source, "dots": dots}

    def _radio_facts(self) -> dict:
        snap = self.state.snapshot()
        s = self.strategy_view()
        return {"delta_s": self.delta_s, "delta_ref": self.delta_ref, "gaps": self.gaps_view(),
                "fuel_margin_laps": s["fuel_margin"], "fuel_margin_source": s["fuel_source"],
                "last_lap_ms": snap.last_lap_ms}

    def radio_check(self) -> bool:
        """Status call on request (wheel button or the sidebar). Returns whether it was queued."""
        with self._lock:
            self._radio_n += 1
            e = radio_report(self._radio_n, **self._radio_facts())
            if self.coach.accept(e):
                self._speak([e])
                return True
            return False

    def overlay_view(self) -> dict:
        with self._lock:
            snap = self.state.snapshot()
            g, s = self.gaps_view(), self.strategy_view()
            last = self.coach.events[-1].message if self.coach.events else None

            def gap(c):
                return None if c is None else {"name": c.name, "position": c.position, "gap_s": c.gap_s,
                                               "change_per_lap_s": c.change_per_lap_s}
            gear = snap.gear
            return {"source": snap.source, "delta_s": self.delta_s, "delta_ref": self.delta_ref,
                    "gear": None if gear is None else {-1: "R", 0: "N"}.get(gear, str(gear)),
                    "speed_kph": snap.speed_kph, "lap": snap.lap_number, "position": snap.position,
                    "ahead": gap(g["ahead"]), "behind": gap(g["behind"]),
                    "fuel_margin_laps": s["fuel_margin"], "fuel_source": s["fuel_source"], "last_call": last}


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
