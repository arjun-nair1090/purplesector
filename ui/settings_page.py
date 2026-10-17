"""Settings form. Everything is validated; receiver/storage changes restart those parts cleanly."""
from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from config.defaults import SETTINGS_PATH
from config.settings import CONFIDENCE, DEMO_SCENARIOS, VERBOSITY, build_settings
from service import get_service
from ui.dashboard import SCENARIO_LABELS


def render() -> None:
    svc = get_service()
    s = svc.settings
    st.html('<div class="ps-bar"><div class="ps-where"><b>Settings</b>'
            f'<span>saved to {SETTINGS_PATH}</span></div></div>')
    for name, msg in svc.settings_errors.items():
        st.warning(f"{_label(name)} from your saved settings or environment was invalid and was reset to its "
                   f"default: {msg}")

    with st.expander("Set up the game's telemetry", expanded=svc.monitor.packets_valid == 0 and svc.mode == "live"):
        st.markdown(
            "In F1 22, F1 23, F1 24 or F1 25 open **Settings → Telemetry Settings** and set:\n\n"
            "| Option | Value |\n|---|---|\n| UDP Telemetry | On |\n| UDP Broadcast Mode | Off |\n"
            f"| UDP IP Address | `{s.udp_host}` |\n| UDP Port | `{s.udp_port}` |\n"
            "| UDP Send Rate | 60 Hz |\n| UDP Format | the game's own year: 2022, 2023, 2024 or 2025 |\n\n"
            "PurpleSector reads the format from each packet, so there is nothing to pick here. The header on the "
            "Live page turns **Connected** within a second of the first lap starting. Packets arrive only while "
            "you're on track, not in menus.")

    with st.form("settings"):
        st.subheader("Telemetry receiver")
        a, b = st.columns(2)
        host = a.text_input("Listen address", s.udp_host, help="127.0.0.1 when the game runs on this PC. "
                            "0.0.0.0 listens on every network interface (e.g. a game on another PC/console).")
        port = b.number_input("UDP port", 1024, 65535, s.udp_port, step=1)
        mode = st.radio("Telemetry source", ["live", "demo"], index=["live", "demo"].index(s.mode), horizontal=True,
                        format_func={"live": "Live game", "demo": "Demo (synthetic)"}.get)

        st.subheader("Voice coach")
        a, b, f = st.columns(3)
        voice = a.toggle("Voice enabled", s.voice_enabled)
        rate = b.slider("Speech rate", -10, 10, s.voice_rate, help="Windows speech rate; 0 is normal")
        vol = f.slider("Volume", 0, 100, s.voice_volume)
        a, b, f, g = st.columns(4)
        interval = a.number_input("Minimum gap between messages (s)", 0.0, 120.0, float(s.voice_min_interval_s), 0.5)
        cooldown = b.number_input("Repeat cooldown (s)", 0.0, 3600.0, float(s.coach_cooldown_s), 5.0,
                                  help="The same kind of message is not repeated within this time")
        verbosity = f.selectbox("Coaching verbosity", VERBOSITY, index=VERBOSITY.index(s.coach_verbosity),
                                format_func=str.capitalize,
                                help="Quiet: personal bests, invalid laps and telemetry loss only. Normal: adds lap "
                                     "times and comparisons. Detailed: adds pit and session events.")
        conf = g.selectbox("Minimum confidence", CONFIDENCE, index=CONFIDENCE.index(s.coach_min_confidence),
                           format_func=str.capitalize,
                           help="Messages below this evidence level are neither shown nor spoken")
        a, b = st.columns(2)
        radio = a.selectbox("Radio on request: wheel button", list(range(13)), index=s.radio_button,
                            format_func=lambda n: "Off" if n == 0 else f"UDP Action {n}",
                            help="In the game, assign a button to this UDP Action under Controls. Pressing it makes "
                                 "the engineer read out your delta, gaps and fuel. The sidebar's Radio check does "
                                 "the same.")
        overlay_port = b.number_input("Overlay port (this PC only)", 1024, 65535, s.overlay_port, step=1,
                                      help="Local endpoint the always-on-top overlay reads. Bound to 127.0.0.1.")
        with st.expander("Coaching thresholds"):
            a, b, f = st.columns(3)
            sector = a.number_input("Sector loss to report (s)", 0.01, 10.0, float(s.sector_loss_threshold_s), 0.05)
            segment = b.number_input("Segment loss to report (s)", 0.01, 10.0, float(s.segment_loss_threshold_s),
                                     0.05)
            brake = f.number_input("Abrupt-brake ratio", 0.05, 0.95, float(s.brake_abrupt_ratio), 0.05,
                                   help="Flag braking when this lap's median brake rise time is below this share of "
                                        "your previous laps' average")
            a, b = st.columns(2)
            corner_calls = a.toggle("Corner calls", s.corner_calls,
                                    help="After each corner where you lost time to your delta reference, the engineer "
                                         "says how much and what was different (at most three a lap)")
            corner_loss = b.number_input("Corner loss to report (s)", 0.02, 5.0, float(s.corner_loss_threshold_s),
                                         0.05)

        st.subheader("Dashboard and recording")
        a, b, f = st.columns(3)
        window = a.slider("Trace history (s)", 5, 300, s.history_window_s)
        refresh = b.slider("Readout refresh (ms)", 100, 2000, s.ui_refresh_ms, 50)
        speed = f.select_slider("Demo speed", [0.5, 1.0, 2.0, 4.0, 8.0],
                                value=s.demo_speed if s.demo_speed in (0.5, 1.0, 2.0, 4.0, 8.0) else 1.0,
                                format_func=lambda v: f"{v:g}×")
        a, b, f = st.columns(3)
        rec = a.toggle("Record sessions", s.recording_enabled)
        rec_demo = b.toggle("Save demo sessions (labelled Demo)", s.record_demo)
        scenario = f.selectbox("Demo scenario", DEMO_SCENARIOS, index=DEMO_SCENARIOS.index(s.demo_scenario),
                               format_func=SCENARIO_LABELS.get)
        data_dir = st.text_input("Data directory", s.data_dir, help="Relative paths are inside the project folder. "
                                 "Changing it opens (or creates) a database there.")
        saved = st.form_submit_button("Save settings", type="primary")

    if saved:
        raw = dict(udp_host=host.strip(), udp_port=port, mode=mode, voice_enabled=voice,
                   voice_rate=rate, voice_volume=vol, voice_min_interval_s=interval, coach_verbosity=verbosity,
                   coach_min_confidence=conf, coach_cooldown_s=cooldown, sector_loss_threshold_s=sector,
                   segment_loss_threshold_s=segment, brake_abrupt_ratio=brake, history_window_s=window,
                   ui_refresh_ms=refresh, recording_enabled=rec, record_demo=rec_demo, demo_scenario=scenario,
                   demo_speed=speed, data_dir=data_dir.strip(), radio_button=radio, overlay_port=overlay_port,
                   corner_calls=corner_calls, corner_loss_threshold_s=corner_loss)
        candidate, errors = build_settings(raw)
        if errors:
            for name, msg in errors.items():
                st.error(f"{_label(name)}: {msg}")
            st.info("Nothing was saved. Correct the field listed and save again.")
        else:
            errors = svc.apply_settings(candidate)
            if errors:
                for name, msg in errors.items():
                    st.error(f"{_label(name)}: {msg}")
            else:
                svc.settings_errors = {}
                st.success("Settings saved and applied.")
                st.rerun()

    if st.button("Reset to safe defaults", help="Restores every setting to its default and restarts the receiver"):
        svc.reset_settings()
        svc.settings_errors = {}
        st.rerun()
    with st.expander("Current values"):
        st.json(asdict(svc.settings))


LABELS = {
    "udp_host": "Listen address", "udp_port": "UDP port",
    "mode": "Telemetry source", "voice_rate": "Speech rate", "voice_volume": "Volume",
    "voice_min_interval_s": "Minimum gap between messages", "coach_verbosity": "Coaching verbosity",
    "coach_min_confidence": "Minimum confidence", "coach_cooldown_s": "Repeat cooldown",
    "sector_loss_threshold_s": "Sector loss to report", "segment_loss_threshold_s": "Segment loss to report",
    "brake_abrupt_ratio": "Abrupt-brake ratio", "history_window_s": "Trace history",
    "ui_refresh_ms": "Readout refresh", "demo_speed": "Demo speed", "demo_scenario": "Demo scenario",
    "data_dir": "Data directory", "settings_file": "Settings file", "radio_button": "Radio wheel button",
    "overlay_port": "Overlay port", "corner_loss_threshold_s": "Corner loss to report",
}


def _label(name: str) -> str:
    return LABELS.get(name, name.replace("_", " ").capitalize())
