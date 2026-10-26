"""Live page: fast fragment for readouts; slower fragments for traces, the race row (map, gaps, fuel) and stats."""
from __future__ import annotations

import subprocess
import sys
import time

import streamlit as st

from config.defaults import FROZEN, PROJECT_ROOT
from config.settings import recent_errors
from service import get_service
from telemetry.connection import ConnectionState
from ui import components as c

PANELS = {"engineer": "Engineer", "race": "Race control", "fuel": "Fuel and tyres", "car": "Car",
          "weather": "Weather", "field": "Against the field", "session": "Session"}

SCENARIO_LABELS ={"standard": "Continuous laps", "quick_lap": "Completed lap in ~15 s",
                   "interruption": "Telemetry drop-out at 25 s"}

STATE_HELP = {
    ConnectionState.DISCONNECTED: "The receiver is stopped. Switch to Live mode in the sidebar.",
    ConnectionState.WAITING: "Listening on {ep}. Start a session in F1 22, F1 23, F1 24 or F1 25 with UDP telemetry on (see Settings for the exact options).",
    ConnectionState.RECONNECTING: "No telemetry for {age:.0f} s. The game may be paused, in a menu, or telemetry was switched off.",
    ConnectionState.UNSUPPORTED_FORMAT: "Packets are arriving in UDP format {fmt}. PurpleSector reads formats 2022 to 2025: set UDP Format to the game's own year in its telemetry settings.",
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


def _me(field):
    return next((r for r in field["tower"] if r.is_player), None)


def render() -> None:
    svc = get_service()
    refresh = svc.settings.ui_refresh_ms / 1000

    @st.fragment(run_every=refresh)
    def readouts():
        snap = svc.state.snapshot()
        state = svc.monitor.state()
        st.html(c.header_bar(snap, state, "loopback" if svc.loopback_test else svc.mode, svc.voice.status,
                             svc.voice.enabled, svc.recording) + _banner(svc, state))
        if state == ConnectionState.RECEIVER_ERROR and svc.mode == "live":
            st.button("Try again", key="retry_receiver", on_click=svc.restart_receiver,
                      help="Re-open the UDP port after closing the other program")
        field = svc.field_view()
        field_best = tuple(r.field_ms for r in field["versus"][:3]) if field["versus"] else (None, None, None)
        left, right = st.container(key="split_live_top").columns([1.8, 1], gap="small")
        left.html(c.hero(snap, svc.laps_view(), svc.gaps_view(), _me(field), field_best)
                  + c.last_call(svc.coach_events()))
        right.html(c.tower_block(field, field.get("year")))

    @st.fragment(run_every=max(1.0, refresh * 4))
    def charts():
        left, right = st.container(key="split_live_charts").columns([1.8, 1], gap="small")
        with left:
            rows = svc.state.history()
            if rows:
                st.html(c.live_traces(c.history_series(rows), svc.settings.history_window_s))
            else:
                st.html('<div class="ps-panel"><div class="ps-empty" style="min-height:540px">Speed, pedals and '
                        f'steering for the last {svc.settings.history_window_s} s appear here once telemetry is '
                        'flowing.</div></div>')
        with right:
            view = svc.map_view()
            if view["map"] is not None:
                st.plotly_chart(c.track_map_chart(view["map"], view["dots"], height=380), key="live_map",
                                config={"displayModeBar": False, "responsive": True})
                n = len(view["map"].corners)
                st.caption(f"Outline from {view['source']}." + (" Corners numbered from your speed trace." if n
                                                                else ""))
            else:
                st.html('<div class="ps-panel"><div class="ps-empty" style="min-height:420px">The track map draws '
                        'itself from your first complete lap with position data, or your recorded best here. '
                        'Then every car gets a dot.</div></div>')

    @st.fragment(run_every=max(0.5, refresh * 2))
    def race():
        # one panel at a time: picking another reruns only this fragment
        pick = st.segmented_control("Panel", list(PANELS), default="engineer", required=True, key="live_panel",
                                    format_func=PANELS.get, label_visibility="collapsed")
        snap = svc.state.snapshot()
        body = {"engineer": lambda: c.coach_block(svc.coach_events()),
                "race": lambda: c.race_control_block(svc.race_control.entries),
                "fuel": lambda: c.strategy_block(svc.strategy_view()),
                "car": lambda: c.car_block(snap),
                "weather": lambda: c.weather_block(snap),
                "field": lambda: c.versus_panel(svc.field_view()["versus"]),
                "session": lambda: _stats(svc)}[pick or "engineer"]()
        st.html(f'<div class="ps-panel">{body}</div>')

    @st.fragment(run_every=max(1.0, refresh * 4))
    def summary():
        _diagnostics(svc)

    readouts()
    charts()
    race()
    summary()


def _stats(svc) -> str:
    s = svc.stats.summary()
    laps = svc.laps_view()
    cons = laps["consistency_s"]
    rows = [("Laps completed", str(len(laps["laps"]))),
            ("Spread of valid laps", f"±{cons:.3f} s" if cons is not None else c.NA),
            ("Session time", c.fmt_duration(s["duration_s"]) if s else c.NA),
            ("Peak speed", f'{s["peak_speed_kph"]} km/h' if s else c.NA),
            ("Mean speed, all samples", f'{s["mean_speed_kph"]:.0f} km/h' if s else c.NA),
            ("Full throttle, of samples", f'{s["full_throttle_pct"]:.0f}%' if s else c.NA),
            ("Braking, of samples", f'{s["braking_pct"]:.0f}%' if s else c.NA)]
    cells = [f'<div class="ps-row"><span>{k}</span><b>{v}</b></div>' for k, v in rows]
    return f'<div class="ps-tab"><div>{"".join(cells[:3])}</div><div>{"".join(cells[3:])}</div></div>'


def _diagnostics(svc) -> None:
    with st.expander("Diagnostics and packet capture", expanded=svc.capture is not None):
        cap = svc.capture
        if cap is not None:
            a, b = st.columns([1, 3])
            a.button("Stop capture", key="stop_capture", on_click=svc.stop_capture, type="primary")
            b.markdown(f"Capturing every packet to `{cap.path}`: **{cap.count:,}** so far, "
                       f"{cap.size_bytes / 1e6:.1f} MB."
                       + (" Stopped at the size limit: stop it and start a new capture to go on." if cap.full else ""))
        else:
            a, b = st.columns([1, 3])
            a.button("Capture raw packets", key="start_capture", on_click=svc.start_capture,
                     disabled=svc.mode != "live",
                     help="Saves exactly what the game sends to data/captures, for checking PurpleSector against "
                          "a real session.")
            b.caption("Drive a few laps with capture on, then stop it. `report FILE` summarises the file and "
                      "`replay FILE` plays it back into the app: `scripts\\diagnostics.py report FILE` from source, "
                      "or `PurpleSector.exe report FILE`." if svc.mode == "live" else
                      "Packet capture records the live game; switch to Live to use it.")
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
                '<div class="ps-meta">Race engineer for F1 22 to 25</div>')
        mode = st.segmented_control("Telemetry source", ["live", "demo"], default=svc.mode, required=True,
                                    format_func={"live": "Live game", "demo": "Demo"}.get)
        if mode and mode != svc.mode:
            svc.update_settings(mode=mode)
            st.rerun()
        if svc.mode == "demo":
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
        v = st.toggle("Voice coach", value=s.voice_enabled)
        if v != s.voice_enabled:
            svc.update_settings(voice_enabled=v)
        pick = voice_select(svc, "Engineer's voice", key="sidebar_voice")
        if pick != s.voice_name:
            svc.update_settings(voice_name=pick)
            st.rerun()
        voice_download(svc)
        vol = st.slider("Radio volume", 0, 100, s.voice_volume, step=5, format="%d%%")
        if vol != s.voice_volume:
            svc.update_settings(voice_volume=vol)
        a, b = st.columns(2)
        if a.button("Test voice", width="stretch"):
            if not svc.voice.test():
                st.warning(svc.voice.last_error or "Voice output is unavailable.")
        if b.button("Radio check", width="stretch",
                    help="Your engineer reads out position, delta, gaps, fuel and tyres now. "
                         + (f"Wheel button: UDP Action {s.radio_button}." if s.radio_button
                            else "Assign a wheel button under Settings → Radio on request.")):
            svc.radio_check()
        if st.button("Open overlay", width="stretch", disabled=svc.overlay_server is None,
                     help="Small always-on-top window with delta, gear, gaps and the last call. Needs the game in "
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


def voice_select(svc, label: str, key: str) -> str:
    """The voice picker: Piper voices first (marked when they still need downloading), then the Windows voices."""
    options = {o.id: o for o in svc.voice_options()}
    current = svc.settings.voice_name
    if current not in options:
        options = {current: None, **options}  # keep a saved choice visible even if it can't be offered now

    def fmt(vid):
        o = options.get(vid)
        if o is None:
            return vid.partition(":")[2] or vid
        return o.label + ("" if o.installed else f" · download {o.size_mb} MB")
    return st.selectbox(label, list(options), index=list(options).index(current), format_func=fmt, key=key,
                        help="Neural voices sound like a person and run offline once downloaded. The Windows "
                             "voices are always available and are used while a neural voice isn't.")


@st.fragment(run_every=1.0)
def voice_download(svc) -> None:
    """Download button and progress for the chosen neural voice; nothing once it's installed."""
    s = svc.settings
    opt = next((o for o in svc.voice_options() if o.id == s.voice_name), None)
    dl = svc.voice_download
    if dl and dl["voice"] == s.voice_name and dl["error"] is None:
        if dl["progress"] < 1:
            st.progress(dl["progress"], text=f"Downloading voice… {dl['progress']:.0%}")
            return
        if not dl.get("shown"):
            dl["shown"] = True
            st.rerun()  # the whole page, so the picker drops its "download" label
    if opt is not None and opt.kind == "piper" and not opt.installed:
        if st.button(f"Download this voice ({opt.size_mb} MB)", width="stretch", key="voice_dl",
                     help=f"One download from the Piper voice library (Hugging Face), then it works offline. "
                          f"Voice recordings licence: {opt.licence}."):
            svc.download_voice(s.voice_name)
            st.rerun(scope="fragment")
        if dl and dl["voice"] == s.voice_name and dl["error"]:
            st.caption(f"Download failed: {dl['error']}")
    if svc.voice_note:
        st.caption(svc.voice_note)


_overlay_proc: subprocess.Popen | None = None


def _open_overlay(port: int) -> None:
    global _overlay_proc
    if _overlay_proc is not None and _overlay_proc.poll() is None:
        st.toast("The overlay is already open. Right-click it to close it.")
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window behind the overlay on Windows
    # packaged, the exe itself runs the overlay; from source, overlay.py
    cmd = [sys.executable, "--overlay"] if FROZEN else [sys.executable, str(PROJECT_ROOT / "overlay.py")]
    _overlay_proc = subprocess.Popen([*cmd, "--port", str(port)],
                                     creationflags=flags)
    st.toast("Overlay opened. Drag it into place; right-click it to close.")
