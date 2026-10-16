"""Live page: fast fragment for readouts; slower fragments for traces, the race row (map, gaps, fuel) and stats."""
from __future__ import annotations

import subprocess
import sys
import time

import streamlit as st

from config.defaults import PROJECT_ROOT
from config.settings import recent_errors
from service import get_service
from telemetry.connection import ConnectionState
from ui import components as c

SCENARIO_LABELS = {"standard": "Continuous laps", "quick_lap": "Completed lap in ~15 s",
                   "interruption": "Telemetry drop-out at 25 s"}

STATE_HELP = {
    ConnectionState.DISCONNECTED: "The receiver is stopped. Switch to Live mode in the sidebar.",
    ConnectionState.WAITING: "Listening on {ep}. Start a session in F1 22 with UDP telemetry on (see Settings for the exact options).",
    ConnectionState.RECONNECTING: "No telemetry for {age:.0f} s. The game may be paused, in a menu, or telemetry was switched off.",
    ConnectionState.UNSUPPORTED_FORMAT: "Packets are arriving in UDP format {fmt}. Set UDP Format to 2022 in F1 22's telemetry settings.",
    ConnectionState.RECEIVER_ERROR: "{err}",
}


def _banner(svc, state) -> str:
    if state == ConnectionState.CONNECTED:
        return ""
    if svc.mode == "demo" and state == ConnectionState.DISCONNECTED:
        return '<div class="ps-banner"><b>Demo stopped.</b> Press Start demo in the sidebar to run the simulator.</div>'
    m = svc.monitor
    text = STATE_HELP[state].format(ep=c.esc(m.endpoint or f"{svc.settings.udp_host}:{svc.settings.udp_port}"),
                                    age=m.seconds_since_valid() or 0, fmt=m.last_unsupported_format,
                                    err=c.esc(m.error or ""))
    return f'<div class="ps-banner"><b>{c.esc(state)}.</b> {text}</div>'


def render() -> None:
    svc = get_service()
    refresh = svc.settings.ui_refresh_ms / 1000

    @st.fragment(run_every=refresh)
    def readouts():
        snap = svc.state.snapshot()
        state = svc.monitor.state()
        st.html(c.header_bar(snap, state, "loopback" if svc.loopback_test else svc.mode, svc.voice.status, svc.voice.enabled, svc.recording)
                + _banner(svc, state))
        if state == ConnectionState.RECEIVER_ERROR and svc.mode == "live":
            st.button("Try again", key="retry_receiver", on_click=svc.restart_receiver,
                      help="Re-open the UDP port after closing the other program")
        laps = svc.laps_view()
        a, b, lp, k = st.columns([1.15, 1.1, 1.25, 1.5])
        a.html(c.speed_block(snap))
        b.html(c.inputs_block(snap))
        lp.html(c.lap_block(snap, laps))
        k.html(c.coach_block(svc.coach_events()))

    @st.fragment(run_every=max(1.0, refresh * 4))
    def charts():
        left, right = st.columns([2.2, 1])
        with left:
            rows = svc.state.history()
            if rows:
                st.plotly_chart(c.live_chart(c.history_series(rows), svc.settings.history_window_s),
                                key="live_chart", config={"displayModeBar": False, "responsive": True})
            else:
                st.html('<div class="ps-panel ps-meta" style="min-height:430px">Traces appear here once '
                        'telemetry is flowing. They show the last '
                        f'{svc.settings.history_window_s} s of speed, throttle, brake and steering.</div>')
        with right:
            st.html(c.car_block(svc.state.snapshot()))

    @st.fragment(run_every=max(0.5, refresh * 2))
    def race():
        snap = svc.state.snapshot()
        m, g, s = st.columns([1.35, 1, 1])
        with m:
            view = svc.map_view()
            if view["map"] is not None:
                st.plotly_chart(c.track_map_chart(view["map"], view["dots"]), key="live_map",
                                config={"displayModeBar": False, "responsive": True})
                n = len(view["map"].corners)
                st.caption(f"Outline from {view['source']}. Corner numbers 1–{n} are detected from your speed "
                           "trace, not the circuit's official turn numbers." if n else f"Outline from {view['source']}.")
            else:
                st.html('<div class="ps-panel ps-meta" style="min-height:340px">The track map draws itself from '
                        'your first complete lap with position data (or your recorded best on this track). '
                        'Live dots then show every car.</div>')
        g.html(c.gaps_block(svc.gaps_view(), snap))
        s.html(c.strategy_block(svc.strategy_view()))

    @st.fragment(run_every=max(1.0, refresh * 4))
    def summary():
        _stats(svc)
        _diagnostics(svc)

    readouts()
    charts()
    race()
    summary()


def _stats(svc) -> None:
    s = svc.stats.summary()
    laps = svc.laps_view()
    done = laps["laps"]
    cols = st.columns(6)
    cols[0].metric("Laps completed", len(done), help="Laps whose lap time the game has reported this session")
    cons = laps["consistency_s"]
    cols[1].metric("Consistency", f"±{cons:.3f} s" if cons is not None else "—",
                   help="Standard deviation of your valid lap times this session (needs 3 or more laps). "
                        "Lower means more consistent.")
    cols[2].metric("Peak speed", f'{s["peak_speed_kph"]} km/h' if s else "—")
    cols[3].metric("Mean sampled speed", f'{s["mean_speed_kph"]:.0f} km/h' if s else "—",
                   help="Arithmetic mean of every received speed sample this session (fixed-rate sampling)")
    cols[4].metric("Full throttle / braking", f'{s["full_throttle_pct"]:.0f}% / {s["braking_pct"]:.0f}%' if s else "—",
                   help="Share of samples with throttle ≥ 98% / brake ≥ 5%. Measured from received telemetry.")
    cols[5].metric("Session time", c.fmt_duration(s["duration_s"]) if s else "—",
                   help="Wall-clock time since the first sample of this session")


def _diagnostics(svc) -> None:
    with st.expander("Diagnostics", expanded=False):
        d = svc.monitor.diagnostics()
        w = svc.writer
        left, mid, right = st.columns(3)
        left.markdown(
            f"**Source** {d['source']}  \n**Endpoint** {d['endpoint'] or '—'}  \n"
            f"**Receiver running** {d['running']}  \n**Last valid packet** {c.fmt_age(d['last_packet_wall'])}  \n"
            f"**Packet rate** {d['packet_rate_hz']} /s  \n**Receiver error** {d['error'] or 'none'}")
        mid.markdown(
            f"**Datagrams** {d['packets_total']}  \n**Valid** {d['packets_valid']}  \n"
            f"**Malformed** {d['malformed']}  \n**Unsupported format** {d['unsupported_format']}"
            + (f" (last: {d['last_unsupported_format']})" if d["last_unsupported_format"] else "")
            + f"  \n**Unused packet types** {d['ignored_types']}  \n**Duplicates skipped** {d['duplicates']}")
        right.markdown(
            f"**Recording** {'yes' if svc.recording else 'no'}  \n**Laps written** {w.laps_written}  \n"
            f"**Write queue** {w.pending}  \n**Dropped writes** {w.dropped}  \n"
            f"**DB** `{svc.repo.db_path}`  \n**Voice** {svc.voice.status}, spoken {svc.voice.spoken_count}, "
            f"dropped {svc.voice.queue.dropped}")
        st.caption("Packet types observed: " + (", ".join(f"{k} {v}" for k, v in sorted(d["packet_types"].items()))
                                                or "none yet"))
        errs = [f"{time.strftime('%H:%M:%S', time.localtime(t))} {m}" for t, m in d["decoder_errors"]]
        errs += [f"{time.strftime('%H:%M:%S', time.localtime(t))} {lvl} {m}" for t, lvl, m in recent_errors.snapshot()]
        for e in [svc.voice.last_error, w.last_error]:
            if e:
                errs.append(e)
        st.text("\n".join(errs[-15:]) if errs else "No errors recorded.")


def sidebar() -> None:
    svc = get_service()
    s = svc.settings
    with st.sidebar:
        st.html(f'<div class="ps-brand" style="font-size:1.4rem">{c.BRAND}</div>'
                '<div class="ps-meta">F1 22 telemetry and race engineer</div>')
        mode = st.segmented_control("Telemetry source", ["live", "demo"], default=svc.mode, required=True,
                                    format_func={"live": "Live (F1 22)", "demo": "Demo"}.get)
        if mode and mode != svc.mode:
            svc.update_settings(mode=mode)
            st.rerun()
        if svc.mode == "demo":
            st.caption("Demo data is synthetic and labelled as such everywhere.")
            from demo.simulator import SCENARIOS
            sc = st.selectbox("Scenario", SCENARIOS, index=SCENARIOS.index(s.demo_scenario),
                              format_func=SCENARIO_LABELS.get)
            sp = st.select_slider("Simulation speed", [0.5, 1.0, 2.0, 4.0, 8.0], value=s.demo_speed
                                  if s.demo_speed in (0.5, 1.0, 2.0, 4.0, 8.0) else 1.0, format_func=lambda v: f"{v:g}×")
            if sc != s.demo_scenario or sp != s.demo_speed:
                svc.update_settings(demo_scenario=sc, demo_speed=sp)
            a, b = st.columns(2)
            if a.button("Start demo", type="primary", width="stretch",
                        help="Restarts the simulator from lap 1 with the selected scenario"):
                svc.start_demo()
            if b.button("Stop demo", width="stretch", disabled=svc.demo is None):
                svc.stop_demo()
            rd = st.toggle("Save demo sessions (labelled Demo)", value=s.record_demo)
            if rd != s.record_demo:
                svc.update_settings(record_demo=rd)
        st.divider()
        v = st.toggle("Voice coach", value=s.voice_enabled)
        if v != s.voice_enabled:
            svc.update_settings(voice_enabled=v)
        a, b = st.columns(2)
        if a.button("Test voice", width="stretch"):
            if not svc.voice.test():
                st.warning(svc.voice.last_error or "Voice output is unavailable.")
        if b.button("Radio check", width="stretch",
                    help="Your engineer reads out delta, gaps and fuel now. "
                         + (f"Wheel button: UDP Action {s.radio_button}." if s.radio_button
                            else "Assign a wheel button under Settings → Radio on request.")):
            svc.radio_check()
        if st.button("Open overlay", width="stretch", disabled=svc.overlay_server is None,
                     help="Small always-on-top window with delta, gear, gaps and the last call. Needs F1 22 in "
                          "borderless or windowed mode. Drag to move, right-click to close."
                     if svc.overlay_server else f"Overlay endpoint unavailable: port {s.overlay_port} is in use."):
            _open_overlay(s.overlay_port)
        if svc.voice.status == "unavailable":
            st.caption(f"Voice unavailable: {svc.voice.last_error}")
        r = st.toggle("Record sessions", value=s.recording_enabled,
                      help="Completed laps and their telemetry are saved to the local database")
        if r != s.recording_enabled:
            svc.set_recording(r)
        if svc.settings_errors:
            st.warning("Some settings were invalid and reset to defaults. See Settings.")


_overlay_proc: subprocess.Popen | None = None


def _open_overlay(port: int) -> None:
    global _overlay_proc
    if _overlay_proc is not None and _overlay_proc.poll() is None:
        st.toast("The overlay is already open. Right-click it to close it.")
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window behind the overlay on Windows
    _overlay_proc = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "overlay.py"), "--port", str(port)],
                                     creationflags=flags)
    st.toast("Overlay opened. Drag it into place; right-click it to close.")
