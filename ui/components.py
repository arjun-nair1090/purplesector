"""Theme, formatters and HTML/Plotly builders. Pure functions of data, so they're testable."""
from __future__ import annotations

import html
import math
import time

from plotly.subplots import make_subplots

from telemetry.connection import ConnectionState
from telemetry.state import WHEELS

PURPLE, GREEN, YELLOW, RED, BLUE = "#A66CFF", "#39D98A", "#F2C94C", "#FF5C5C", "#5CC8FF"
BG, PANEL, LINE, TEXT, MUTED = "#171A21", "#1F232C", "#2C313C", "#E9ECF2", "#8D94A3"
FONT = "Bahnschrift, 'DIN Alternate', 'Segoe UI', system-ui, sans-serif"

CSS = f"""
<style>
:root {{ --purple:{PURPLE}; --green:{GREEN}; --yellow:{YELLOW}; --red:{RED}; --blue:{BLUE};
        --bg:{BG}; --panel:{PANEL}; --line:{LINE}; --text:{TEXT}; --muted:{MUTED}; }}
[class^="ps-"] {{ font-family:{FONT}; }}
.stApp {{ background:var(--bg); }}
.block-container {{ padding-top:3.2rem; padding-bottom:1rem; max-width:none; }}
.ps-num {{ font-variant-numeric:tabular-nums; }}
.ps-bar {{ display:flex; flex-wrap:wrap; align-items:center; gap:.45rem 1.15rem; padding:.55rem .9rem;
          background:var(--panel); border:1px solid var(--line); border-left:4px solid var(--purple);
          border-radius:6px; margin-bottom:.6rem; }}
.ps-brand {{ font-weight:700; font-size:1.15rem; letter-spacing:.02em; color:var(--text); }}
.ps-brand b {{ color:var(--purple); }}
.ps-kv {{ display:flex; flex-direction:column; line-height:1.15; }}
.ps-kv small {{ color:var(--muted); font-size:.72rem; }}
.ps-kv span {{ color:var(--text); font-size:.98rem; font-variant-numeric:tabular-nums; }}
.ps-pill {{ display:inline-flex; align-items:center; gap:.4rem; padding:.18rem .6rem; border-radius:999px;
           font-size:.82rem; font-weight:600; border:1px solid currentColor; }}
.ps-dot {{ width:.55rem; height:.55rem; border-radius:50%; background:currentColor; }}
.ps-panel {{ background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.7rem .9rem; }}
.ps-panel.top {{ min-height:14.2rem; container-type:inline-size; }}
.ps-label {{ color:var(--muted); font-size:.75rem; margin-bottom:.15rem; }}
.ps-speedrow {{ display:flex; gap:.6rem; align-items:flex-end; justify-content:space-between; }}
.ps-speedrow > div:first-child {{ min-width:0; }}
/* sized from the panel's own width (container query units), so it never collides with the gear box */
.ps-speed {{ font-size:clamp(2.2rem, 31cqw, 5.6rem); font-weight:700; line-height:.88; color:var(--text);
            font-variant-numeric:tabular-nums; white-space:nowrap; }}
.ps-idle {{ color:#3A404D !important; }}
.ps-speed .ps-unit {{ display:block; font-size:.85rem; margin:.2rem 0 0; font-weight:500; }}
.ps-unit {{ color:var(--muted); font-size:.62em; margin-left:.2em; }}
[data-testid="stMetricValue"] {{ font-size:1.7rem; font-variant-numeric:tabular-nums; }}
.ps-gear {{ font-size:clamp(1.9rem, 19cqw, 3.6rem); font-weight:700; line-height:.9; color:var(--purple);
           text-align:center; border:2px solid var(--line); border-radius:6px; min-width:3.6rem;
           padding:.2rem .4rem; flex-shrink:0; }}
.ps-meter {{ height:.9rem; background:#14171D; border:1px solid var(--line); border-radius:3px; overflow:hidden; }}
.ps-meter > div {{ height:100%; }}
.ps-steer {{ position:relative; height:.9rem; background:#14171D; border:1px solid var(--line); border-radius:3px; }}
.ps-steer .c {{ position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background:var(--muted); }}
.ps-steer .k {{ position:absolute; top:1px; bottom:1px; width:6px; margin-left:-3px; border-radius:2px; background:var(--blue); }}
.ps-row {{ display:flex; justify-content:space-between; align-items:baseline; gap:.6rem; margin:.25rem 0; }}
.ps-row b {{ font-size:1.15rem; font-variant-numeric:tabular-nums; }}
.ps-big {{ font-size:2.1rem; font-weight:700; font-variant-numeric:tabular-nums; line-height:1; }}
.ps-sectors {{ display:grid; grid-template-columns:repeat(3,1fr); gap:4px; margin-top:.5rem; }}
.ps-sector {{ padding:.35rem .45rem; border-radius:4px; background:#2A2F3A; color:var(--text); }}
.ps-sector small {{ display:block; font-size:.7rem; opacity:.8; }}
.ps-sector span {{ font-size:1.05rem; font-weight:600; font-variant-numeric:tabular-nums; }}
.ps-sector.purple {{ background:var(--purple); color:#fff; }}
.ps-sector.green {{ background:var(--green); color:#06210F; }}
.ps-sector.yellow {{ background:var(--yellow); color:#2A2100; }}
.ps-sector.live {{ outline:2px solid var(--text); outline-offset:-2px; }}
.ps-msg {{ font-size:1.08rem; font-weight:600; line-height:1.3; color:var(--text); margin:.15rem 0 .35rem; }}
.ps-meta {{ color:var(--muted); font-size:.8rem; }}
.ps-tyres {{ display:grid; grid-template-columns:1fr 1fr; gap:6px; }}
.ps-tyre {{ border:1px solid var(--line); border-radius:6px; padding:.35rem .5rem; }}
.ps-tyre b {{ font-size:1.1rem; font-variant-numeric:tabular-nums; }}
.ps-tmeta {{ display:flex; flex-wrap:wrap; gap:0 .7rem; color:var(--muted); font-size:.78rem; }}
.ps-tmeta span {{ white-space:nowrap; }}
.ps-scale {{ display:flex; justify-content:space-between; color:var(--muted); font-size:.68rem; margin-top:.1rem; }}
.ps-na {{ color:var(--muted); font-style:italic; }}
.ps-banner {{ padding:.5rem .8rem; border-radius:6px; border:1px solid var(--line); background:var(--panel);
             color:var(--muted); margin-bottom:.6rem; }}
.ps-banner b {{ color:var(--text); }}
</style>
"""

CONNECTION_COLORS = {
    ConnectionState.CONNECTED: GREEN, ConnectionState.WAITING: YELLOW, ConnectionState.RECONNECTING: YELLOW,
    ConnectionState.DISCONNECTED: MUTED, ConnectionState.UNSUPPORTED_FORMAT: RED,
    ConnectionState.RECEIVER_ERROR: RED,
}
NA = '<span class="ps-na">n/a</span>'


# ---------------- formatting ----------------

def fmt_lap(ms) -> str:
    if ms is None or ms <= 0:
        return "—"
    m, rem = divmod(int(ms), 60_000)
    return f"{m}:{rem / 1000:06.3f}" if m else f"{rem / 1000:.3f}"


def fmt_delta(s) -> str:
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return "—"
    return f"{s:+.3f}"


def fmt_gear(g) -> str:
    return "—" if g is None else {-1: "R", 0: "N"}.get(g, str(g))


def fmt_age(ts) -> str:
    if not ts:
        return "never"
    d = time.time() - ts
    return f"{d:.1f} s ago" if d < 60 else time.strftime("%H:%M:%S", time.localtime(ts))


def fmt_duration(s) -> str:
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def esc(x) -> str:
    return html.escape(str(x))


def val(x, fmt="{}", unit="") -> str:
    """Formatted value, or an explicit n/a marker when the game hasn't provided it."""
    return NA if x is None else esc(fmt.format(x)) + (f'<span class="ps-unit">{esc(unit)}</span>' if unit else "")


def sector_class(t, best, live=False) -> str:
    """Timing-screen colours: purple = session best (or matches it), yellow = slower, grey = no time."""
    cls = "ps-sector"
    if t and best:
        cls += " purple" if t <= best else " yellow"
    return cls + (" live" if live else "")


# ---------------- HTML blocks ----------------

def kv(label, value_html) -> str:
    return f'<div class="ps-kv"><small>{esc(label)}</small><span>{value_html}</span></div>'


def header_bar(snap, conn_state, mode, voice_status, voice_enabled, recording) -> str:
    color = CONNECTION_COLORS.get(conn_state, MUTED)
    mode_pill = {
        "demo": f'<span class="ps-pill" style="color:{YELLOW}">Demo · simulated data</span>',
        "loopback": f'<span class="ps-pill" style="color:{YELLOW}">Live port · synthetic loopback test</span>',
    }.get(mode, f'<span class="ps-pill" style="color:{TEXT}">Live · F1 22</span>')
    conn = f'<span class="ps-pill" style="color:{color}"><span class="ps-dot"></span>{esc(conn_state)}</span>'
    lap = snap.lap_number
    laps = f"{lap}" + (f" / {snap.total_laps}" if lap and snap.total_laps else "") if lap else None
    if voice_status == "unavailable":
        voice = f'<span style="color:{RED}">Unavailable</span>'
    else:
        voice = "On" if voice_enabled else "Off"
    rec = f'<span style="color:{RED}">● Recording</span>' if recording else "Not recording"
    session = snap.session_type
    if snap.game_paused:
        session = (session or "") + " (paused)"
    return (
        '<div class="ps-bar"><span class="ps-brand">Purple<b>Sector</b></span>'
        f"{mode_pill}{conn}"
        + kv("Session", val(session)) + kv("Track", val(snap.track_name)) + kv("Lap", val(laps))
        + kv("Position", val(snap.position, "P{}")) + kv("Voice coach", voice) + kv("Session log", rec)
        + "</div>"
    )


def speed_block(snap) -> str:
    rpm, max_rpm = snap.rpm, snap.max_rpm or 12000
    rpm_pct = 0 if rpm is None else min(100, rpm / max_rpm * 100)
    rpm_color = RED if rpm_pct > 95 else (YELLOW if rpm_pct > 85 else BLUE)
    drs = NA if snap.drs_active is None else (
        f'<b style="color:{GREEN}">Open</b>' if snap.drs_active else
        ("Available" if snap.drs_allowed else "Closed"))
    return (
        '<div class="ps-panel top"><div class="ps-speedrow">'
        f'<div><div class="ps-label">Speed</div><div class="ps-speed{" ps-idle" if snap.speed_kph is None else ""}">{"—" if snap.speed_kph is None else snap.speed_kph}'
        '<span class="ps-unit">km/h</span></div></div>'
        f'<div><div class="ps-label" style="text-align:center">Gear</div><div class="ps-gear{" ps-idle" if snap.gear is None else ""}">{fmt_gear(snap.gear)}</div></div>'
        '</div>'
        f'<div class="ps-row" style="margin-top:.6rem"><span class="ps-label">Engine</span>'
        f'<b>{val(rpm, "{:,}", "rpm")}</b></div>'
        f'<div class="ps-meter"><div style="width:{rpm_pct:.0f}%;background:{rpm_color}"></div></div>'
        f'<div class="ps-row"><span class="ps-label">DRS</span><span>{drs}</span></div>'
        "</div>"
    )


def inputs_block(snap) -> str:
    def meter(label, pct, color):
        w = 0 if pct is None else max(0.0, min(100.0, pct))
        return (f'<div class="ps-row"><span class="ps-label">{label}</span><b>{val(pct, "{:.0f}", "%")}</b></div>'
                f'<div class="ps-meter"><div style="width:{w:.0f}%;background:{color}"></div></div>')
    st = snap.steer_input
    knob = 50 if st is None else 50 + max(-1, min(1, st)) * 50
    steer = (f'<div class="ps-row"><span class="ps-label">Steering input</span>'
             f'<b>{val(st, "{:+.2f}")}</b></div><div class="ps-steer"><div class="c"></div>'
             + ("" if st is None else f'<div class="k" style="left:{knob:.1f}%"></div>') + "</div>"
             '<div class="ps-scale"><span>−1 full left</span><span>0</span><span>+1 full right</span></div>')
    return ('<div class="ps-panel top">' + meter("Throttle", snap.throttle_pct, GREEN)
            + meter("Brake", snap.brake_pct, RED) + steer
            + f'<div class="ps-row"><span class="ps-label">Clutch</span><span>{val(snap.clutch_pct, "{}", "%")}</span></div>'
            + "</div>")


def lap_block(snap, laps_view) -> str:
    best = laps_view["best"]
    best_s = laps_view["best_sectors"]
    last_lap = laps_view["laps"][-1] if laps_view["laps"] else None
    invalid = f' <span style="color:{RED};font-size:.9rem">invalid</span>' if snap.lap_invalid else ""
    best_ms = best.lap_time_ms if best else None
    game_best = snap.best_lap_ms_game
    # sectors: current lap's completed sectors, otherwise blank
    cur = [snap.sector1_ms, snap.sector2_ms, None]
    live_idx = (snap.sector or 0) - 1
    sectors = "".join(
        f'<div class="{sector_class(cur[i], best_s[i], live=i == live_idx)}"><small>S{i + 1}'
        f'{" · best " + fmt_lap(best_s[i]) if best_s[i] else ""}</small><span>{fmt_lap(cur[i])}</span></div>'
        for i in range(3))
    last_cls = ""
    if last_lap and best and last_lap.timed:
        last_cls = f' style="color:{PURPLE}"' if last_lap is best else f' style="color:{YELLOW}"'
    return (
        '<div class="ps-panel top">'
        f'<div class="ps-label">Current lap{"" if snap.lap_number is None else " " + str(snap.lap_number)}</div>'
        f'<div class="ps-big{" ps-idle" if snap.current_lap_ms is None else ""}">{fmt_lap(snap.current_lap_ms)}{invalid}</div>'
        f'<div class="ps-row"><span class="ps-label">Last lap</span><b{last_cls}>{fmt_lap(snap.last_lap_ms)}</b></div>'
        f'<div class="ps-row"><span class="ps-label">Best valid lap (recorded)</span>'
        f'<b style="color:{PURPLE}">{fmt_lap(best_ms)}</b></div>'
        + (f'<div class="ps-row"><span class="ps-label">Best lap (game history)</span><b>{fmt_lap(game_best)}</b></div>'
           if game_best else "")
        + f'<div class="ps-sectors">{sectors}</div></div>'
    )


def coach_block(events) -> str:
    if not events:
        return ('<div class="ps-panel top"><div class="ps-label">Race engineer</div>'
                '<div class="ps-meta">Quiet until there is something worth saying. Messages appear after '
                'completed laps, invalidations, pit stops and session events.</div></div>')
    e = events[-1]
    conf = {1: "Low", 2: "Medium", 3: "High"}[int(e.confidence)]
    older = "".join(f'<div class="ps-meta">{time.strftime("%H:%M:%S", time.localtime(o.timestamp))} — '
                    f'{esc(o.message)}</div>' for o in list(events)[-4:-1][::-1])
    return (
        '<div class="ps-panel top"><div class="ps-label">Race engineer · '
        f'{esc(e.category)} · {time.strftime("%H:%M:%S", time.localtime(e.timestamp))}</div>'
        f'<div class="ps-msg">{esc(e.message)}</div>'
        f'<div class="ps-meta">Confidence: {conf}. Based on: {esc(e.evidence)}</div>'
        f'<div style="margin-top:.4rem;border-top:1px solid {LINE};padding-top:.3rem">{older}</div></div>'
    )


def car_block(snap) -> str:
    def wheel(i):
        name = WHEELS[i]
        t = snap.tyre_surface_temps_c[i] if snap.tyre_surface_temps_c else None
        inner = snap.tyre_inner_temps_c[i] if snap.tyre_inner_temps_c else None
        p = snap.tyre_pressures_psi[i] if snap.tyre_pressures_psi else None
        w = snap.tyre_wear_pct[i] if snap.tyre_wear_pct else None
        b = snap.brake_temps_c[i] if snap.brake_temps_c else None
        return (f'<div class="ps-tyre"><div class="ps-label">{name}</div><b>{val(t, "{}", "°C")}</b>'
                f'<div class="ps-tmeta"><span>Inner {val(inner, "{}", "°C")}</span><span>{val(p, "{:.1f}", "psi")}</span>'
                f'<span>Wear {val(w, "{:.1f}", "%")}</span><span>Brake {val(b, "{}", "°C")}</span></div></div>')
    order = (2, 3, 0, 1)  # display FL FR / RL RR
    tyres = "".join(wheel(i) for i in order)
    compound = snap.tyre_visual or snap.tyre_compound
    wings = snap.wing_damage_pct
    wing_txt = None if wings is None else f"FL {wings[0]}% · FR {wings[1]}% · R {wings[2]}%"
    rows = [
        ("Tyres", val(compound) + ("" if snap.tyre_age_laps is None else f" · {snap.tyre_age_laps} laps old")),
        ("Fuel", val(snap.fuel_kg, "{:.1f}", "kg")),
        ("Fuel margin (laps, game)", val(snap.fuel_laps_remaining, "{:+.1f}")),
        ("ERS store", val(snap.ers_store_mj, "{:.2f}", "MJ")),
        ("Engine temp", val(snap.engine_temp_c, "{}", "°C")),
        ("Wing damage", val(wing_txt)),
        ("Floor / gearbox / engine", val(None if snap.floor_damage_pct is None else
                                         f"{snap.floor_damage_pct}% / {snap.gearbox_damage_pct}% / {snap.engine_damage_pct}%")),
    ]
    rows_html = "".join(f'<div class="ps-row"><span class="ps-label">{esc(k)}</span><span>{v}</span></div>'
                        for k, v in rows)
    return f'<div class="ps-panel"><div class="ps-tyres">{tyres}</div><div style="margin-top:.5rem">{rows_html}</div></div>'


# ---------------- charts ----------------

def history_series(rows, gap_s: float = 0.5):
    """Split history rows into columns, inserting None at gaps/rewinds so lines aren't interpolated."""
    cols = {k: [] for k in ("t", "speed", "throttle", "brake", "steer")}
    if not rows:
        return cols
    now = rows[-1][0]
    prev = None
    for r in rows:
        if prev is not None and (r[0] - prev > gap_s or r[0] < prev):
            for k in cols:
                cols[k].append(None)
        cols["t"].append(r[0] - now)
        cols["speed"].append(r[1])
        cols["throttle"].append(r[2])
        cols["brake"].append(r[3])
        cols["steer"].append(r[4])
        prev = r[0]
    return cols


def _style(fig, height):
    fig.update_layout(height=height, margin=dict(l=48, r=12, t=8, b=28), paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      font=dict(family=FONT, color=TEXT, size=12), showlegend=True,
                      legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom", bgcolor="rgba(0,0,0,0)"),
                      hovermode="x unified")
    fig.update_xaxes(gridcolor=LINE, zeroline=False)
    fig.update_yaxes(gridcolor=LINE, zeroline=False)
    return fig


def live_chart(cols, window_s: int):
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.4, 0.35, 0.25])
    fig.add_scatter(x=cols["t"], y=cols["speed"], name="Speed (km/h)", line=dict(color=PURPLE, width=2),
                    connectgaps=False, row=1, col=1)
    fig.add_scatter(x=cols["t"], y=cols["throttle"], name="Throttle (%)", line=dict(color=GREEN, width=1.5),
                    fill="tozeroy", fillcolor="rgba(57,217,138,.18)", connectgaps=False, row=2, col=1)
    fig.add_scatter(x=cols["t"], y=cols["brake"], name="Brake (%)", line=dict(color=RED, width=2, dash="solid"),
                    connectgaps=False, row=2, col=1)
    fig.add_scatter(x=cols["t"], y=cols["steer"], name="Steering input", line=dict(color=BLUE, width=1.5),
                    connectgaps=False, row=3, col=1)
    fig.update_yaxes(title_text="km/h", rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title_text="%", range=[0, 105], row=2, col=1)
    fig.update_yaxes(title_text="steer", range=[-1.05, 1.05], row=3, col=1)
    fig.update_xaxes(range=[-window_s, 0], row=3, col=1, title_text="seconds before now (game clock)")
    return _style(fig, 430)


def comparison_chart(frame, lap_label, ref_label):
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.22, 0.32, 0.23, 0.23])
    d = frame["distance_m"]
    fig.add_scatter(x=d, y=frame["delta_s"], name="Running delta (s, + = slower)", line=dict(color=YELLOW, width=2),
                    connectgaps=False, row=1, col=1)
    for col, row, title in (("speed", 2, "km/h"), ("throttle", 3, "throttle %"), ("brake", 4, "brake %")):
        fig.add_scatter(x=d, y=frame[f"{col}_ref"], name=f"{ref_label}" if row == 2 else None, showlegend=row == 2,
                        line=dict(color=MUTED, width=1.5), connectgaps=False, row=row, col=1, legendgroup="ref")
        fig.add_scatter(x=d, y=frame[f"{col}_lap"], name=f"{lap_label}" if row == 2 else None, showlegend=row == 2,
                        line=dict(color=PURPLE if col == "speed" else (GREEN if col == "throttle" else RED), width=2),
                        connectgaps=False, row=row, col=1, legendgroup="lap")
        fig.update_yaxes(title_text=title, row=row, col=1)
    fig.update_yaxes(title_text="Δ s", row=1, col=1)
    fig.update_xaxes(title_text="lap distance (m)", row=4, col=1)
    fig.add_hline(y=0, line=dict(color=LINE), row=1, col=1)
    return _style(fig, 640)
