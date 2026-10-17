"""Theme, formatters and HTML/Plotly builders. Pure functions of data, so they're testable.

Visual language: a TV timing feed seen from the pit wall. Graphite ground, surfaces
separated by tone steps (no outlines), one condensed typeface with tabular figures, and
the timing colours used only for what they mean in F1 timing: purple fastest, green
personal best or gaining, yellow slower, red losing or a flag. The one loud element is
the pit board: your position, gap and lap in big figures on a black board.
"""
from __future__ import annotations

import base64
import html
import math
import time

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from telemetry.connection import ConnectionState
from telemetry.packet_models import WEATHER
from telemetry.state import WHEELS

PURPLE, GREEN, YELLOW, RED, BLUE = "#A66CFF", "#2BD47D", "#F2C230", "#FF4F4F", "#5CC8FF"
PURPLE_TEXT = "#B48AFF"  # purple as text on a raised surface, where #A66CFF falls under 4.5:1
BG, PANEL, RAISED, HIGH, LINE = "#12141A", "#191C23", "#20242D", "#2A2F3A", "#262A33"
TEXT, MUTED, FAINT, BOARD = "#ECEEF2", "#A3AAB7", "#8A909C", "#0A0B0E"
FONT = "'Barlow Semi Condensed', 'Segoe UI', system-ui, sans-serif"

# Five type sizes do all the work: small 14, body 16, figure 22, readout 40 and the delta, set as large as
# the column allows. Nothing else gets a size of its own.
CSS = f"""
<style>
:root {{ --purple:{PURPLE}; --purple-text:{PURPLE_TEXT}; --green:{GREEN}; --yellow:{YELLOW}; --red:{RED};
        --blue:{BLUE}; --bg:{BG}; --panel:{PANEL}; --raised:{RAISED}; --high:{HIGH}; --line:{LINE};
        --text:{TEXT}; --muted:{MUTED}; --faint:{FAINT}; --board:{BOARD};
        --t-s:.875rem; --t-b:1rem; --t-m:1.375rem; --t-l:2.5rem; --t-x:clamp(4.5rem, 7.2vw, 8.5rem); }}
[class^="ps-"], [class*=" ps-"] {{ font-family:{FONT}; font-variant-numeric:tabular-nums; }}
.stApp {{ background:var(--bg); }}
.block-container {{ padding-top:3.4rem; padding-bottom:2rem; max-width:none; }}
::selection {{ background:rgba(166,108,255,.45); color:#fff; }}
* {{ scrollbar-color:var(--high) transparent; scrollbar-width:thin; }}
:focus-visible {{ outline:2px solid var(--purple) !important; outline-offset:2px; }}
[data-testid="stMetricValue"] {{ font-variant-numeric:tabular-nums; }}
[data-testid="stMetricLabel"] p {{ color:var(--muted); }}
[data-testid="stExpander"] details {{ background:var(--panel); border:0; }}
[data-testid="stExpanderDetails"] {{ border:0 !important; }}
[data-testid="stCaptionContainer"] {{ max-width:65ch; }}
/* the panel switcher: selected option in readable purple, and it wraps on a phone instead of scrolling */
[data-testid="stButtonGroup"] button[data-selected] {{ color:var(--purple-text) !important; }}
[data-testid="stButtonGroup"] > div {{ flex-wrap:wrap; }}
/* ps-hook: the invisible resize-hook iframe from app.py */
[data-testid="stElementContainer"]:has(iframe[srcdoc*="__psResizeHook"]) {{ position:absolute; height:0;
  overflow:hidden; pointer-events:none; }}

.ps-brand {{ font-weight:800; font-style:italic; font-size:var(--t-m); color:var(--text); display:inline-flex;
            align-items:center; gap:.45em; letter-spacing:.01em; }}
.ps-brand b {{ color:var(--purple); font-weight:800; }}
.ps-mark {{ height:.95em; width:auto; flex-shrink:0; }}

/* header: what session this is, and whether data is flowing */
.ps-bar {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:.5rem 1.6rem; padding:.2rem 0 1rem; }}
.ps-where {{ display:flex; align-items:baseline; gap:.7rem; min-width:0; }}
.ps-where b {{ font-size:var(--t-m); font-weight:700; line-height:1; color:var(--text); white-space:nowrap; }}
.ps-where span {{ font-size:var(--t-b); color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.ps-chips {{ display:flex; flex-wrap:wrap; align-items:center; gap:.3rem 1.2rem; margin-left:auto; }}
.ps-chip {{ display:inline-flex; align-items:center; gap:.4rem; color:var(--muted); font-size:var(--t-s);
           white-space:nowrap; }}
.ps-chip i {{ width:.5rem; height:.5rem; border-radius:50%; background:currentColor; display:inline-block; }}
.ps-chip.warn {{ background:rgba(242,194,48,.14); color:var(--yellow); padding:.2rem .6rem; border-radius:3px;
                font-weight:600; }}

.ps-panel {{ background:var(--panel); border-radius:4px; padding:1rem 1.1rem; container-type:inline-size;
            box-sizing:border-box; }}
.ps-panel.fill {{ height:100%; }}
[data-testid="stColumn"]:has(.ps-panel.fill) > [data-testid="stVerticalBlock"],
[data-testid="stColumn"]:has(.ps-panel.fill) [data-testid="stElementContainer"],
[data-testid="stColumn"]:has(.ps-panel.fill) [data-testid="stHtml"] {{ height:100%; }}
.ps-meta {{ color:var(--muted); font-size:var(--t-s); line-height:1.45; }}
.ps-na {{ color:var(--faint); }}
.ps-idle {{ color:var(--faint) !important; }}
.ps-unit {{ color:var(--muted); font-size:var(--t-s); margin-left:.2em; font-weight:500; }}
.ps-row {{ display:flex; justify-content:space-between; align-items:baseline; gap:.8rem; padding:.28rem 0; }}
.ps-row > span:first-child {{ color:var(--muted); font-size:var(--t-b); }}
.ps-row b {{ font-size:var(--t-b); font-weight:600; white-space:nowrap; }}

/* the hero: pit board, lap timing beside the delta field, telemetry readout */
.ps-hero {{ background:var(--panel); border-radius:4px; overflow:hidden; container-type:inline-size; }}
.ps-board {{ background:var(--board); padding:.75rem 1.2rem; display:flex; flex-wrap:wrap; align-items:center;
            justify-content:space-between; gap:.4rem 1.6rem; }}
.ps-board .line {{ display:flex; align-items:baseline; gap:.55rem; min-width:0; }}
.ps-board .big {{ font-size:var(--t-l); font-weight:700; line-height:1; color:#fff; white-space:nowrap; }}
.ps-board .cap {{ font-size:var(--t-s); color:var(--muted); line-height:1.25; min-width:0; }}
.ps-board .cap em {{ display:block; font-style:normal; font-weight:600; white-space:nowrap; }}

.ps-lap {{ padding:1rem 1.2rem; display:flex; flex-direction:column; gap:.8rem; min-width:0; }}
.ps-laptime small {{ display:block; color:var(--muted); font-size:var(--t-b); margin-bottom:.2rem; }}
.ps-laptime b {{ font-size:var(--t-l); font-weight:600; line-height:1; color:var(--text); }}
/* the delta field: the onboard delta graphic, a flat field in the timing colour of the moment */
.ps-delta {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:.2rem 1.2rem; padding:.9rem 1.2rem 1rem;
            background:var(--high); color:var(--text); }}
.ps-delta b {{ font-size:var(--t-x); font-weight:700; line-height:.9; letter-spacing:-.02em; white-space:nowrap; }}
.ps-delta small {{ font-size:var(--t-m); font-weight:600; }}
.ps-delta.gain {{ background:var(--green); color:#062A16; }}
.ps-delta.loss {{ background:#D42A30; color:#fff; }}  /* a deeper red than --red: white text at 5:1 */
.ps-delta.none {{ background:var(--raised); color:var(--muted); }}
.ps-delta.none b {{ font-size:var(--t-l); }}
.ps-delta.none small {{ font-size:var(--t-b); font-weight:400; }}
.ps-sectors {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:3px; }}
.ps-sector {{ padding:.4rem .6rem .45rem; border-radius:3px; background:var(--raised); color:var(--text); }}
.ps-sector small {{ display:block; font-size:var(--t-s); opacity:.85; }}
.ps-sector span {{ font-size:var(--t-m); font-weight:600; }}
.ps-sector.purple {{ background:var(--purple); color:#fff; }}
.ps-sector.green {{ background:var(--green); color:#062A16; }}
.ps-sector.yellow {{ background:var(--yellow); color:#2A2100; }}
.ps-sector.live {{ box-shadow:inset 0 -3px 0 var(--text); }}
.ps-times {{ display:flex; flex-wrap:wrap; gap:.3rem 1.8rem; }}
.ps-times div {{ display:flex; align-items:baseline; gap:.5rem; }}
.ps-times div small {{ color:var(--muted); font-size:var(--t-b); }}
.ps-times div b {{ font-size:var(--t-b); font-weight:600; }}

/* telemetry readout, one tone below the timing */
.ps-strip {{ display:grid; grid-template-columns:minmax(11rem, auto) 1fr; gap:.6rem 2rem; align-items:center;
             padding:.9rem 1.2rem; background:var(--raised); }}
.ps-speed {{ display:flex; align-items:center; gap:.8rem; margin-bottom:.4rem; }}
.ps-speed .v {{ font-size:var(--t-l); font-weight:600; line-height:.9; min-width:3.4ch; text-align:right; }}
.ps-speed .v small {{ display:block; font-size:var(--t-s); font-weight:500; color:var(--muted); text-align:right;
                     margin-top:.2rem; }}
.ps-gear {{ font-size:var(--t-l); font-weight:600; line-height:1; color:var(--text); background:var(--high);
           border-radius:3px; min-width:2.6rem; padding:.15rem .4rem; text-align:center; }}
.ps-gauge {{ display:grid; grid-template-columns:4.8rem 1fr 3.6rem; align-items:center; gap:.7rem; padding:.2rem 0; }}
.ps-gauge > span {{ color:var(--muted); font-size:var(--t-b); }}
.ps-gauge > b {{ font-size:var(--t-b); font-weight:600; text-align:right; }}
.ps-meter {{ height:.6rem; background:var(--high); border-radius:2px; overflow:hidden; }}
.ps-meter > div {{ height:100%; }}
.ps-steer {{ position:relative; height:.6rem; background:var(--high); border-radius:2px; }}
.ps-steer .c {{ position:absolute; left:50%; top:0; bottom:0; width:2px; margin-left:-1px; background:var(--faint); }}
.ps-steer .k {{ position:absolute; top:-.15rem; bottom:-.15rem; width:.4rem; margin-left:-.2rem; border-radius:2px;
               background:var(--blue); }}

/* timing tower */
.ps-tower {{ max-height:36rem; overflow-y:auto; margin:0 -.3rem; }}
.ps-thead {{ display:grid; grid-template-columns:2.1rem 4px 1fr auto 5.2rem; gap:.6rem; padding:0 .3rem .45rem;
            color:var(--muted); font-size:var(--t-s); }}
.ps-thead span:nth-child(n+4) {{ text-align:right; }}
.ps-trow {{ display:grid; grid-template-columns:2.1rem 4px 1fr auto 5.2rem; align-items:center; gap:.6rem;
           padding:.38rem .3rem; border-radius:3px; font-size:var(--t-b); }}
.ps-trow:nth-child(even) {{ background:rgba(255,255,255,.025); }}
.ps-trow .p {{ font-weight:700; color:var(--muted); text-align:right; }}
.ps-trow .t {{ height:1.15rem; border-radius:1px; }}
.ps-trow .n {{ font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.ps-trow .g {{ text-align:right; color:var(--text); white-space:nowrap; }}
.ps-trow .b {{ text-align:right; color:var(--muted); }}
.ps-trow .b.purple {{ color:var(--purple); font-weight:600; }}
.ps-trow.me {{ background:var(--high) !important; }}
.ps-trow.me .n, .ps-trow.me .p, .ps-trow.me .b.purple {{ color:var(--purple-text); }}
.ps-vs {{ width:100%; max-width:52rem; border-collapse:collapse; font-size:var(--t-b); }}
.ps-vs th {{ color:var(--muted); font-weight:500; text-align:right; padding:.25rem .4rem; }}
.ps-vs td {{ text-align:right; padding:.3rem .4rem; color:var(--text); }}
.ps-vs tr:nth-child(even) td {{ background:rgba(255,255,255,.025); }}
.ps-vs th:first-child, .ps-vs td:first-child {{ text-align:left; color:var(--muted); }}

/* the engineer's last call */
.ps-call {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:.3rem 1.2rem; padding:.85rem 1.2rem;
           margin-top:.6rem; background:var(--panel); border-radius:4px; }}
.ps-call b {{ font-size:var(--t-m); font-weight:600; line-height:1.25; color:var(--text); flex:1 1 0;
             min-width:14rem; }}
.ps-call span {{ color:var(--muted); font-size:var(--t-s); white-space:nowrap; }}
.ps-call.quiet b {{ color:var(--muted); font-weight:500; font-size:var(--t-b); }}

/* logs */
.ps-log {{ display:flex; flex-direction:column; gap:.15rem; max-width:52rem; }}
.ps-log div {{ display:grid; grid-template-columns:3.6rem 1fr; gap:.6rem; font-size:var(--t-b); color:var(--muted);
              padding:.3rem 0; line-height:1.4; }}
.ps-log div span:first-child {{ color:var(--faint); }}
.ps-log div.mine span:last-child {{ color:var(--text); font-weight:600; }}
.ps-why {{ display:block; color:var(--muted); font-size:var(--t-b); font-weight:400; margin-top:.1rem; }}
.ps-log div.flag span:last-child {{ color:var(--yellow); font-weight:600; }}
.ps-log div.red span:last-child {{ color:var(--red); font-weight:600; }}

/* fuel, tyres, weather */
.ps-cells {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(6rem, 1fr)); gap:3px; margin:.5rem 0 .2rem; }}
.ps-cells div {{ background:var(--raised); border-radius:3px; padding:.4rem .6rem; }}
.ps-cells small {{ display:block; color:var(--muted); font-size:var(--t-s); }}
.ps-cells b {{ font-size:var(--t-b); font-weight:600; }}
.ps-cells .now {{ background:rgba(166,108,255,.24); }}
.ps-fc td:nth-child(2) {{ text-align:left; }}
.ps-fc th:nth-child(2) {{ text-align:left; }}
.ps-fc tr.wet td:nth-child(2), .ps-fc tr.wet td:nth-child(3) {{ color:var(--blue); font-weight:600; }}
.ps-tyres {{ display:grid; grid-template-columns:1fr 1fr; gap:3px; }}
.ps-tyre {{ background:var(--raised); border-radius:3px; padding:.5rem .65rem; }}
.ps-tyre small {{ color:var(--muted); font-size:var(--t-s); }}
.ps-tyre b {{ display:block; font-size:var(--t-m); font-weight:600; }}
.ps-tyre .m {{ display:flex; flex-wrap:wrap; gap:0 .8rem; color:var(--muted); font-size:var(--t-s); }}
.ps-laps {{ width:100%; border-collapse:collapse; font-size:var(--t-b); }}
.ps-laps th {{ color:var(--muted); font-weight:500; text-align:right; padding:.2rem .4rem; }}
.ps-laps td {{ text-align:right; padding:.25rem .4rem; color:var(--text); }}
.ps-laps tr:nth-child(even) td {{ background:rgba(255,255,255,.025); }}
.ps-laps th:first-child, .ps-laps td:first-child {{ text-align:left; }}
.ps-banner {{ padding:.7rem 1rem; border-radius:4px; background:rgba(242,194,48,.1); color:var(--text);
             margin-bottom:.8rem; font-size:var(--t-b); }}
.ps-banner b {{ color:var(--yellow); }}
.ps-chart {{ display:block; width:100%; height:auto; }}
.ps-empty {{ min-height:9rem; display:flex; align-items:center; color:var(--muted); line-height:1.45;
            font-size:var(--t-b); max-width:40rem; }}
.ps-tab {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(18rem, 1fr)); gap:.6rem 2.4rem;
           align-items:start; }}
@media (max-width: 900px) {{ .ps-strip {{ grid-template-columns:1fr; }} }}
@media (max-width: 600px) {{ .ps-call b {{ font-size:var(--t-b); }} }}
</style>
"""

# the PurpleSector mark: three timing bars stepping up, the last one a purple sector (docs/mark.svg)
_MARK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="8 12 63 40">'
             '<path d="M9 50.5L20.5 50.5L27.2 34.5L15.7 34.5Z" fill="#4A5060"/>'
             '<path d="M25.5 50.5L37 50.5L47.9 24.5L36.4 24.5Z" fill="#8D94A3"/>'
             '<path d="M42 50.5L53.5 50.5L69 13.5L57.5 13.5Z" fill="#A66CFF"/></svg>')
# st.html strips inline <svg>, so the mark goes in as an image
MARK = f'<img class="ps-mark" alt="" src="data:image/svg+xml;base64,{base64.b64encode(_MARK_SVG.encode()).decode()}">'
BRAND = f'{MARK}<span>Purple<b>Sector</b></span>'

CONNECTION_COLORS = {
    ConnectionState.CONNECTED: GREEN, ConnectionState.WAITING: YELLOW, ConnectionState.RECONNECTING: YELLOW,
    ConnectionState.DISCONNECTED: MUTED, ConnectionState.UNSUPPORTED_FORMAT: RED,
    ConnectionState.RECEIVER_ERROR: RED,
}
NA = '<span class="ps-na">n/a</span>'

# Team colours as the broadcast graphics use them, by game year and the game's team id. Teams outside the
# ten F1 teams (F2, classic cars, My Team) get no colour rather than a guess.
_TEAMS_2022 = {0: "#6CD3BF", 1: "#F91536", 2: "#3671C6", 3: "#37BEDD", 4: "#358C75", 5: "#2293D1", 6: "#5E8FAA",
               7: "#B6BABD", 8: "#F58020", 9: "#C92D4B"}
_TEAMS_2024 = {0: "#27F4D2", 1: "#E8002D", 2: "#3671C6", 3: "#64C4FF", 4: "#229971", 5: "#FF87BC", 6: "#6692FF",
               7: "#B6BABD", 8: "#FF8000", 9: "#52E252"}
TEAM_COLOURS = {2022: _TEAMS_2022, 2023: _TEAMS_2022, 2024: _TEAMS_2024, 2025: {**_TEAMS_2024, 5: "#0093CC"}}


def team_colour(year, team_id) -> str:
    return TEAM_COLOURS.get(year, {}).get(team_id, HIGH)


# ---------------- formatting ----------------

def fmt_lap(ms) -> str:
    if ms is None or (isinstance(ms, float) and math.isnan(ms)) or ms <= 0:
        return "—"
    m, rem = divmod(int(ms), 60_000)
    return f"{m}:{rem / 1000:06.3f}" if m else f"{rem / 1000:.3f}"


def fmt_delta(s) -> str:
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return "—"
    return f"{s:+.3f}"


def minus(text: str) -> str:
    """Typographic minus for signed figures."""
    return text.replace("-", "−")


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


def sector_class(t, best, live=False, field_best=None) -> str:
    """Timing-screen colours. With the field's times: purple = fastest of anyone, green = your best,
    yellow = slower. Alone (or before the field's times arrive) your best is the fastest: purple."""
    cls = "ps-sector"
    if t and best:
        if field_best:
            cls += " purple" if t <= field_best else (" green" if t <= best else " yellow")
        else:
            cls += " purple" if t <= best else " yellow"
    return cls + (" live" if live else "")


# ---------------- header ----------------

def header_bar(snap, conn_state, mode, voice_status, voice_enabled, recording) -> str:
    color = CONNECTION_COLORS.get(conn_state, MUTED)
    source = {
        "demo": '<span class="ps-chip warn">Demo, simulated data</span>',
        "loopback": '<span class="ps-chip warn">Synthetic loopback test</span>',
    }.get(mode, f'<span class="ps-chip">Live, {esc(snap.game or "F1 22 to F1 25")}</span>')
    chips = [source, f'<span class="ps-chip" style="color:{color}"><i></i>{esc(conn_state)}</span>']
    if voice_status == "unavailable":
        chips.append(f'<span class="ps-chip" style="color:{RED}">Voice unavailable</span>')
    else:
        chips.append(f'<span class="ps-chip" style="color:{TEXT if voice_enabled else MUTED}">'
                     f'Voice {"on" if voice_enabled else "off"}</span>')
    chips.append(f'<span class="ps-chip" style="color:{RED}"><i></i>Recording</span>' if recording
                 else f'<span class="ps-chip" style="color:{MUTED}">Not recording</span>')
    session = snap.session_type
    if session and snap.game_paused:
        session += " (paused)"
    where = f'<b>{val(session)}</b><span>{val(snap.track_name)}</span>'
    return f'<div class="ps-bar"><div class="ps-where">{where}</div><div class="ps-chips">{"".join(chips)}</div></div>'


# ---------------- hero: pit board and lap timing ----------------

def pit_board(snap, gaps, me_row) -> str:
    """Your position, the gap that matters now and the lap, as the crew would hold them out."""
    pos = f"P{snap.position}" if snap.position else "P—"
    of = f"of {gaps['cars']}" if gaps.get("cars") else "position"
    if gaps.get("race"):
        ahead = gaps.get("ahead") is not None
        g = gaps.get("ahead") or gaps.get("behind")
        game_gap = me_row.gap_s if ahead and me_row is not None and me_row.gap_source == "game" else None
        if g is not None and (g.gap_s is not None or game_gap is not None):
            value = game_gap if game_gap is not None else g.gap_s
            gap = f"+{value:.1f}" if ahead else minus(f"-{value:.1f}")
            source = "game interval" if game_gap is not None else "measured"
            trend = ""
            if g.change_per_lap_s is not None and abs(g.change_per_lap_s) >= 0.05:
                good = (g.change_per_lap_s < 0) if ahead else (g.change_per_lap_s > 0)
                word = "closing" if g.change_per_lap_s < 0 else "opening"
                trend = f'<em style="color:{GREEN if good else RED}">{word} {abs(g.change_per_lap_s):.1f} s a lap</em>'
            gap_cap = f"{esc(g.name)} {'ahead' if ahead else 'behind'}, {source}{trend}"
        else:
            gap, gap_cap = "—", "the gap appears once both cars have passed the same point"
    elif me_row is not None and me_row.gap_s is not None:
        gap, gap_cap = f"+{me_row.gap_s:.3f}", "to the fastest lap"
    elif me_row is not None and me_row.purple_lap:
        gap, gap_cap = "P1", "your lap is the fastest"
    else:
        gap, gap_cap = "—", "gap to the fastest lap, once you have a timed lap"
    lap = f"L{snap.lap_number}" if snap.lap_number else "L—"
    lap_cap = f"of {snap.total_laps}" if snap.lap_number and snap.total_laps else "lap"
    return ('<div class="ps-board">'
            f'<div class="line"><span class="big{" ps-idle" if snap.position is None else ""}">{pos}</span>'
            f'<span class="cap">{esc(of)}</span></div>'
            f'<div class="line"><span class="big{" ps-idle" if gap == "—" else ""}">{gap}</span>'
            f'<span class="cap">{gap_cap}</span></div>'
            f'<div class="line"><span class="big{" ps-idle" if snap.lap_number is None else ""}">{lap}</span>'
            f'<span class="cap">{esc(lap_cap)}</span></div></div>')


def delta_badge(delta_s, ref_label) -> str:
    """Live delta to the reference lap: green when gaining (negative), red when losing."""
    if delta_s is None:
        return '<div class="ps-delta none"><b>—</b><small>Delta n/a until you have a clean, complete lap.</small></div>'
    if abs(delta_s) < 0.0005:  # rounds to 0.000: level with the reference, neither gaining nor losing
        cls, text = "even", "0.000"
    else:
        cls = "gain" if delta_s < 0 else "loss"
        text = minus(f"{delta_s:+.3f}")
    return f'<div class="ps-delta {cls}"><b>{text}</b><small>vs {esc(ref_label)}</small></div>'


def timing_block(snap, laps_view, field_best=(None, None, None)) -> str:
    best = laps_view["best"]
    best_s = laps_view["best_sectors"]
    best_ms = best.lap_time_ms if best else None
    ideal = laps_view.get("theoretical_best_ms")
    pb = laps_view.get("alltime_best_ms")
    cur = [snap.sector1_ms, snap.sector2_ms, None]
    live_idx = (snap.sector or 0) - 1
    sectors = "".join(
        f'<div class="{sector_class(cur[i], best_s[i], i == live_idx, field_best[i])}">'
        f'<small>S{i + 1}{" best " + fmt_lap(best_s[i]) if best_s[i] else ""}</small><span>{fmt_lap(cur[i])}</span></div>'
        for i in range(3))
    last = laps_view["laps"][-1] if laps_view["laps"] else None
    last_color = ""
    if last and best and last.timed:
        last_color = f' style="color:{PURPLE if last is best else YELLOW}"'
    invalid = f' <span style="color:{RED};font-weight:600">invalid</span>' if snap.lap_invalid else ""
    times = [("Last lap", f"<b{last_color}>{fmt_lap(snap.last_lap_ms)}</b>"),
             ("Session best", f'<b style="color:{PURPLE if best_ms else FAINT}">{fmt_lap(best_ms)}</b>'),
             ("Ideal lap", f"<b>{fmt_lap(ideal)}</b>")]
    if pb:
        times.append(("Recorded PB", f"<b>{fmt_lap(pb)}</b>"))
    lap_no = snap.lap_number if snap.lap_number is not None else "—"
    return (
        f'<div class="ps-timing">{delta_badge(laps_view.get("delta_s"), laps_view.get("delta_ref"))}'
        '<div class="ps-lap"><div class="ps-laptime">'
        f'<small>Lap {lap_no}{invalid}</small>'
        f'<b class="{"ps-idle" if snap.current_lap_ms is None else ""}">{fmt_lap(snap.current_lap_ms)}</b></div>'
        f'<div class="ps-sectors">{sectors}</div>'
        '<div class="ps-times">' + "".join(f"<div><small>{k}</small>{v}</div>" for k, v in times) + "</div></div></div>")


def hero(snap, laps_view, gaps, me_row, field_best=(None, None, None)) -> str:
    return (f'<div class="ps-hero">{pit_board(snap, gaps, me_row)}{timing_block(snap, laps_view, field_best)}'
            f'{telemetry_strip(snap)}</div>')


# ---------------- telemetry strip ----------------

def _gauge(label, pct, color, text=None) -> str:
    w = 0 if pct is None else max(0.0, min(100.0, pct))
    shown = text if text is not None else val(pct, "{:.0f}", "%")
    return (f'<div class="ps-gauge"><span>{label}</span><div class="ps-meter"><div style="width:{w:.0f}%;'
            f'background:{color}"></div></div><b>{shown}</b></div>')


def telemetry_strip(snap) -> str:
    rpm, max_rpm = snap.rpm, snap.max_rpm
    rpm_pct = None if rpm is None or not max_rpm else min(100, rpm / max_rpm * 100)  # no bar without the limit
    rpm_color = RED if (rpm_pct or 0) > 96 else (YELLOW if (rpm_pct or 0) > 88 else BLUE)
    st = snap.steer_input
    knob = 50 if st is None else 50 + max(-1, min(1, st)) * 50
    steer = ('<div class="ps-gauge"><span>Steering</span><div class="ps-steer"><div class="c"></div>'
             + ("" if st is None else f'<div class="k" style="left:{knob:.1f}%"></div>')
             + f'</div><b>{val(st, "{:+.2f}")}</b></div>')
    drs = NA if snap.drs_active is None else (
        f'<b style="color:{GREEN}">open</b>' if snap.drs_active else ("available" if snap.drs_allowed else "closed"))
    speed = "—" if snap.speed_kph is None else snap.speed_kph
    rpm_text = val(None if rpm is None else rpm / 1000, "{:.1f}", "k")
    return (
        '<div class="ps-strip"><div>'
        f'<div class="ps-speed"><div class="v{" ps-idle" if snap.speed_kph is None else ""}">{speed}'
        '<small>km/h</small></div>'
        f'<div class="ps-gear{" ps-idle" if snap.gear is None else ""}">{fmt_gear(snap.gear)}</div></div>'
        + f'<div class="ps-row"><span>DRS</span><b>{drs}</b></div>'
        + f'<div class="ps-row"><span>ERS store</span><b>{val(snap.ers_store_mj, "{:.2f}", "MJ")}</b></div></div>'
        '<div>' + _gauge("Throttle", snap.throttle_pct, GREEN) + _gauge("Brake", snap.brake_pct, RED)
        + _gauge("Engine", rpm_pct, rpm_color, rpm_text) + steer + "</div></div>")


# ---------------- timing tower ----------------

def tower_block(view, year) -> str:
    rows, race = view["tower"], view["race"]
    head = ('<div class="ps-panel fill"><div class="ps-thead"><span>Pos</span><span></span><span>Driver</span>'
            f'<span>{"Interval" if race else "Gap"}</span><span>Best</span></div>')
    if not rows:
        return head + ('<div class="ps-empty">The order fills in from the game\'s lap data: every car, its '
                       'best lap and how far apart you are.</div></div>')
    sources = {r.gap_source for r in rows if r.gap_s is not None}
    teams = view.get("teams", {})
    body = []
    for r in rows:
        gap = "—" if r.gap_s is None else (f"+{r.gap_s:.1f}" if race else f"+{r.gap_s:.3f}")
        if not race and r.gap_s is None and r.purple_lap:
            gap = "fastest"
        body.append(f'<div class="ps-trow{" me" if r.is_player else ""}"><span class="p">{r.position}</span>'
                    f'<span class="t" style="background:{team_colour(year, teams.get(r.car_index))}"></span>'
                    f'<span class="n">{esc(r.name)}</span><span class="g">{gap}</span>'
                    f'<span class="b{" purple" if r.purple_lap else ""}">{fmt_lap(r.best_lap_ms)}</span></div>')
    note = {"measured": "Intervals measured from each car's distance trace."}
    foot = " ".join(note[s] for s in sorted(sources) if s in note)
    return (head + f'<div class="ps-tower">{"".join(body)}</div>'
            + (f'<div class="ps-meta" style="margin-top:.45rem">{foot}</div>' if foot else "") + "</div>")


def versus_panel(rows) -> str:
    """Your best sectors, lap and ideal lap against the session's fastest, one column each."""
    if not rows:
        return ('<div class="ps-empty">Your best sectors against the fastest in the session, once the game has '
                'sent lap history for the cars.</div>')

    def gap(r):
        if r.delta_s is None:
            return "—"
        color = PURPLE if r.delta_s <= 0 else (YELLOW if r.delta_s < 0.3 else MUTED)
        return f'<span style="color:{color}">{minus(f"{r.delta_s:+.3f}")}</span>'

    def line(label, cells):
        return f"<tr><td>{label}</td>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>"
    return ('<table class="ps-vs"><tr><th></th>' + "".join(f"<th>{r.label}</th>" for r in rows) + "</tr>"
            + line("Fastest", [fmt_lap(r.field_ms) for r in rows])
            + line("Set by", [esc(r.holder or "") for r in rows])
            + line("You", [fmt_lap(r.yours_ms) for r in rows])
            + line("Gap", [gap(r) for r in rows]) + "</table>")


# ---------------- engineer, race control, strategy, weather, car ----------------

CONFIDENCE = {1: "low", 2: "medium", 3: "high"}


def last_call(events) -> str:
    """The engineer's latest message, with what kind, how sure and when."""
    if not events:
        return ('<div class="ps-call quiet"><b>Your engineer speaks up when there is something worth saying.</b>'
                '</div>')
    e = events[-1]
    return (f'<div class="ps-call"><b>{esc(e.message)}</b><span>{esc(e.category)}</span>'
            f'<span>{CONFIDENCE[int(e.confidence)]} confidence</span>'
            f'<span>{time.strftime("%H:%M:%S", time.localtime(e.timestamp))}</span></div>')


def coach_block(events) -> str:
    """Everything the engineer said, newest first, each with its category, confidence and evidence."""
    if not events:
        return ('<div class="ps-empty">Quiet until there is something worth saying: lap times, personal bests, '
                'corners where you lost time, the weather, race control.</div>')
    rows = "".join(f'<div class="mine"><span>{time.strftime("%H:%M", time.localtime(o.timestamp))}</span>'
                   f'<span>{esc(o.message)}<small class="ps-why">{esc(o.category)}, '
                   f'{CONFIDENCE[int(o.confidence)]} confidence. Based on {esc(o.evidence)}.</small></span></div>'
                   for o in list(events)[-8:][::-1])
    return f'<div class="ps-log" style="margin-top:0">{rows}</div>'


def race_control_block(entries) -> str:
    if not entries:
        return ('<div class="ps-empty">Penalties, safety cars, flags, retirements and fastest laps appear here as '
                'the game announces them.</div>')
    rows = []
    for e in list(entries)[-10:][::-1]:
        cls = ("red" if e.code == "RDFL" or (e.involves_player and e.code == "PENA") else
               "flag" if e.code in ("SC", "SCAR") else "mine" if e.involves_player else "")
        when = f"Lap {e.lap}" if e.lap else fmt_duration(e.session_time)
        rows.append(f'<div class="{cls}"><span>{when}</span><span>{esc(e.message)}</span></div>')
    return f'<div class="ps-log" style="margin-top:0">{"".join(rows)}</div>'


def strategy_block(s) -> str:
    """Fuel and tyre use measured per lap, the projection from it, and the game's pit window."""
    burn, wear = s["fuel_per_lap_kg"], s["wear_per_lap_pct"]
    margin = s["fuel_margin"]
    margin_html = NA
    if margin is not None:
        color = RED if margin < 0 else (YELLOW if margin < 0.5 else GREEN)
        margin_html = (f'<b style="color:{color}">{minus(f"{margin:+.1f}")}</b>'
                       f'<span class="ps-unit">laps, {esc(s["fuel_source"])}</span>')
    rows = [
        ("Fuel per lap", val(burn, "{:.2f}", "kg") + (f'<span class="ps-unit">avg of {s["fuel_laps_measured"]}</span>'
                                                       if burn is not None else "")),
        ("Fuel left at that rate", val(s["fuel_laps_left"], "{:.1f}", "laps")),
        ("Margin at the flag", margin_html),
        ("Most-worn tyre, per lap", val(wear, "{:.2f}", "%")),
    ]
    body = "".join(f'<div class="ps-row"><span>{k}</span><span>{v}</span></div>' for k, v in rows)
    ideal, latest, lap = s["pit_ideal"], s["pit_latest"], s["lap_number"]
    if ideal or latest:
        in_window = bool(lap and ideal and latest and ideal <= lap <= latest)
        rejoin = "P" + str(s["pit_rejoin"]) if s["pit_rejoin"] else "—"
        pit = ('<div class="ps-cells">'
               f'<div class="{"now" if in_window else ""}"><small>Pit window, game</small>'
               f'<b>Lap {ideal or "?"} to {latest or "?"}</b></div>'
               f'<div><small>Rejoin, game</small><b>{rejoin}</b></div></div>')
    else:
        pit = '<div class="ps-meta">Pit window: n/a (the game sends it in races with a strategy).</div>'
    if s["laps"]:
        table = ('<table class="ps-laps"><tr><th>Lap</th><th>Time</th><th>Fuel kg</th><th>Wear %</th></tr>'
                 + "".join(f"<tr><td>{lap.lap_number}</td><td>{fmt_lap(lap.lap_time_ms)}</td>"
                           f"<td>{'—' if lap.fuel_used_kg is None else f'{lap.fuel_used_kg:.2f}'}</td>"
                           f"<td>{'—' if lap.tyre_wear_pct is None else f'{lap.tyre_wear_pct:.2f}'}</td></tr>"
                           for lap in reversed(s["laps"]))
                 + "</table>")
    else:
        table = '<div class="ps-meta">Fuel and tyre use per lap, measured from your second lap on.</div>'
    return f'<div class="ps-tab"><div>{body}{pit}</div><div>{table}</div></div>'


def weather_block(snap) -> str:
    accuracy = {0: "perfect", 1: "approximate"}.get(snap.forecast_accuracy)
    now = (f'<div class="ps-row"><span>Now</span><b>{val(snap.weather)}</b></div>'
           f'<div class="ps-row"><span>Track</span><b>{val(snap.track_temp_c, "{}", "°C")}</b></div>'
           f'<div class="ps-row"><span>Air</span><b>{val(snap.air_temp_c, "{}", "°C")}</b></div>'
           f'<div class="ps-row"><span>Forecast accuracy, game</span><b>{val(accuracy)}</b></div>')
    ahead = [f for f in snap.forecast if f.time_offset_min > 0][:6]
    if not ahead:
        return (f'<div class="ps-tab"><div>{now}</div><div class="ps-meta">No forecast from the game for this '
                'session yet.</div></div>')
    rows = "".join(f'<tr class="{"wet" if f.weather >= 3 else ""}"><td>in {f.time_offset_min} min</td>'
                   f'<td>{esc(WEATHER.get(f.weather, f"Weather {f.weather}"))}</td><td>{f.rain_percentage}%</td></tr>'
                   for f in ahead)
    return (f'<div class="ps-tab"><div>{now}</div><table class="ps-laps ps-fc" style="margin-top:0"><tr>'
            f'<th>Ahead</th><th>Forecast</th><th>Rain</th></tr>{rows}</table></div>')


def car_block(snap) -> str:
    def wheel(i):
        t = snap.tyre_surface_temps_c[i] if snap.tyre_surface_temps_c else None
        inner = snap.tyre_inner_temps_c[i] if snap.tyre_inner_temps_c else None
        p = snap.tyre_pressures_psi[i] if snap.tyre_pressures_psi else None
        w = snap.tyre_wear_pct[i] if snap.tyre_wear_pct else None
        b = snap.brake_temps_c[i] if snap.brake_temps_c else None
        return (f'<div class="ps-tyre"><small>{WHEELS[i]} surface</small><b>{val(t, "{}", "°C")}</b>'
                f'<div class="m"><span>inner {val(inner, "{}", "°C")}</span><span>{val(p, "{:.1f}", "psi")}</span>'
                f'<span>wear {val(w, "{:.1f}", "%")}</span><span>brake {val(b, "{}", "°C")}</span></div></div>')
    tyres = "".join(wheel(i) for i in (2, 3, 0, 1))  # FL FR / RL RR
    compound = snap.tyre_visual or snap.tyre_compound
    wings = snap.wing_damage_pct
    damage = None if wings is None else (f"wing {wings[0]}% {wings[1]}%, rear {wings[2]}%, "
                                         f"floor {snap.floor_damage_pct}%")
    rows = [("Tyres", val(compound) + ("" if snap.tyre_age_laps is None else f", {snap.tyre_age_laps} lap{'' if snap.tyre_age_laps == 1 else 's'} old")),
            ("Fuel", val(snap.fuel_kg, "{:.1f}", "kg")),
            ("Fuel margin, game", val(snap.fuel_laps_remaining, "{:+.1f}", "laps")),
            ("Engine", val(snap.engine_temp_c, "{}", "°C")),
            ("Damage", val(damage))]
    body = "".join(f'<div class="ps-row"><span>{esc(k)}</span><span>{v}</span></div>' for k, v in rows)
    return f'<div class="ps-tab"><div class="ps-tyres">{tyres}</div><div>{body}</div></div>'


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
    fig.update_layout(height=height, margin=dict(l=48, r=12, t=10, b=30), paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      font=dict(family=FONT, color=TEXT, size=14), showlegend=True,
                      legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom", bgcolor="rgba(0,0,0,0)"),
                      hovermode="x unified", hoverlabel=dict(bgcolor=RAISED, bordercolor=HIGH, font_family=FONT))
    fig.update_xaxes(gridcolor=LINE, zeroline=False, linecolor=LINE)
    fig.update_yaxes(gridcolor=LINE, zeroline=False, linecolor=LINE, title_font=dict(color=MUTED, size=12))
    return fig


def _pieces(xs, ys):
    """Runs of (x, y) between the None gaps history_series inserts, so lines never bridge a drop-out."""
    run = []
    for x, y in zip(xs, ys, strict=True):
        if x is None or y is None:
            if run:
                yield run
            run = []
        else:
            run.append((x, y))
    if run:
        yield run


def live_traces(cols, window_s: int, w: int = 1000, h: int = 540) -> str:
    """The last `window_s` seconds of speed, pedals and steering as one SVG image. Redrawn every refresh;
    an image swaps in place, where a heavy Plotly figure blanked for a moment on every update."""
    left, right, top, bottom, gap = 64, 14, 40, 46, 14
    plot_w, plot_h = w - left - right, h - top - bottom - 2 * gap
    speeds = [v for v in cols["speed"] if v is not None]
    top_speed = max(100.0, math.ceil(max(speeds, default=0) / 50) * 50)
    panels = [(0.42, 0.0, top_speed, "km/h", (0, top_speed / 2, top_speed)),
              (0.34, 0.0, 105.0, "pedal %", (0, 50, 100)), (0.24, -1.05, 1.05, "steer", (-1, 0, 1))]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="Bahnschrift, Segoe UI, '
           f'sans-serif" font-size="20"><rect width="{w}" height="{h}" fill="{PANEL}"/>']
    y0, boxes = top, []
    for share, lo, hi, label, ticks in panels:
        ph = plot_h * share
        boxes.append((y0, ph, lo, hi))
        for t in ticks:
            y = y0 + ph - (t - lo) / (hi - lo) * ph
            out.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{LINE}"/>'
                       f'<text x="{left - 8}" y="{y + 6:.1f}" fill="{MUTED}" text-anchor="end">{t:g}</text>')
        out.append(f'<text transform="translate(18 {y0 + ph / 2:.0f}) rotate(-90)" fill="{MUTED}" '
                   f'text-anchor="middle">{label}</text>')
        y0 += ph + gap
    for s in range(-window_s, 1, 5):
        x = left + (s + window_s) / window_s * plot_w
        out.append(f'<text x="{x:.1f}" y="{h - bottom + 22}" fill="{MUTED}" text-anchor="middle">{s}</text>')
    out.append(f'<text x="{left + plot_w / 2:.0f}" y="{h - 4}" fill="{MUTED}" text-anchor="middle">seconds before '
               'now, game clock</text>')

    def series(key, panel, colour, width, fill=None):
        by, ph, lo, hi = boxes[panel]
        for run in _pieces(cols["t"], cols[key]):
            pts = [(left + (max(t, -window_s) + window_s) / window_s * plot_w,
                    by + ph - (min(max(v, lo), hi) - lo) / (hi - lo) * ph) for t, v in run[::2] + run[-1:]]
            line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            if fill:
                base = by + ph - (0 - lo) / (hi - lo) * ph
                out.append(f'<polygon points="{pts[0][0]:.1f},{base:.1f} {line} {pts[-1][0]:.1f},{base:.1f}" '
                           f'fill="{fill}"/>')
            out.append(f'<polyline points="{line}" fill="none" stroke="{colour}" stroke-width="{width}" '
                       'stroke-linejoin="round"/>')
    series("speed", 0, TEXT, 2)
    series("throttle", 1, GREEN, 1.6, "rgba(43,212,125,.16)")
    series("brake", 1, RED, 2, "rgba(255,79,79,.16)")
    series("steer", 2, BLUE, 1.6)
    x = left
    for name, colour in (("Speed", TEXT), ("Throttle", GREEN), ("Brake", RED), ("Steering", BLUE)):
        out.append(f'<line x1="{x}" x2="{x + 24}" y1="18" y2="18" stroke="{colour}" stroke-width="3"/>'
                   f'<text x="{x + 32}" y="24" fill="{TEXT}">{name}</text>')
        x += 52 + 9 * len(name)
    out.append("</svg>")
    data = base64.b64encode("".join(out).encode()).decode()
    return (f'<div class="ps-panel"><img class="ps-chart" alt="Speed, throttle, brake and steering for the last '
            f'{window_s} seconds" src="data:image/svg+xml;base64,{data}"></div>')


DOT_STYLE = {"player": (PURPLE, 15, "You"), "ahead": (GREEN, 11, "Car ahead"),
             "behind": (RED, 11, "Car behind"), "other": (MUTED, 8, "Other cars")}


def track_map_chart(m, dots=(), *, color=None, color_label=None, height=340):
    """Outline (optionally coloured per point), detected corner labels, and live car dots."""
    fig = go.Figure()
    u, v = np.append(m.u, m.u[0]), np.append(m.v, m.v[0])
    fig.add_scatter(x=u, y=v, mode="lines", line=dict(color=HIGH, width=13), hoverinfo="skip", showlegend=False)
    if color is None:
        fig.add_scatter(x=u, y=v, mode="lines", line=dict(color="#4A5060", width=4), hoverinfo="skip",
                        showlegend=False)
    else:
        finite = np.abs(np.asarray(color, float))
        finite = finite[np.isfinite(finite)]
        lim = float(finite.max()) if finite.size and finite.max() > 0 else 1.0
        fig.add_scatter(x=m.u, y=m.v, mode="markers", showlegend=False,
                        marker=dict(size=6, color=color, cmin=-lim, cmax=lim,
                                    colorscale=[[0, GREEN], [0.5, "#4A5060"], [1, RED]],
                                    colorbar=dict(title=dict(text=color_label, side="top"), thickness=10, len=0.8)),
                        customdata=m.distance_m, hovertemplate="%{customdata:.0f} m<br>%{marker.color:+.3f} s<extra></extra>")
    sx, sy = m.point_at(float(m.distance_m[0]))
    fig.add_scatter(x=[sx], y=[sy], mode="markers", marker=dict(symbol="line-ns", size=16, color=TEXT,
                                                                 line=dict(width=3, color=TEXT)),
                    hovertext="Start / finish", hoverinfo="text", showlegend=False)
    if m.corners:
        # badges just outside the line: offset along the outline's normal, away from the centre
        span = max(float(np.ptp(m.u)), float(np.ptp(m.v)))
        cu, cv = float(np.mean(m.u)), float(np.mean(m.v))
        xs, ys = [], []
        for c in m.corners:
            (x0, y0), (x1, y1) = m.point_at(c.apex_m - 10), m.point_at(c.apex_m + 10)
            px, py = m.point_at(c.apex_m)
            nx, ny = y0 - y1, x1 - x0
            norm = math.hypot(nx, ny) or 1.0
            nx, ny = nx / norm, ny / norm
            if (px - cu) * nx + (py - cv) * ny < 0:
                nx, ny = -nx, -ny
            xs.append(px + nx * span * 0.045)
            ys.append(py + ny * span * 0.045)
        fig.add_scatter(x=xs, y=ys, mode="markers+text", text=[str(c.number) for c in m.corners],
                        textfont=dict(color=TEXT, size=14, family=FONT), textposition="middle center",
                        marker=dict(size=24, color=RAISED, line=dict(width=0)),
                        hovertext=[f"{c.name}: {c.min_speed_kph:.0f} km/h at {c.apex_m:.0f} m" for c in m.corners],
                        hoverinfo="text", showlegend=False)
    for role in ("other", "behind", "ahead", "player"):
        pts = [(x, y) for x, y, r in dots if r == role]
        if pts:
            c, size, name = DOT_STYLE[role]
            fig.add_scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers", name=name,
                            marker=dict(size=size, color=c, line=dict(width=2, color=PANEL)), hoverinfo="name")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1)
    fig.update_layout(height=height, margin=dict(l=4, r=4, t=4, b=4), paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      font=dict(family=FONT, color=TEXT, size=13), showlegend=bool(dots),
                      legend=dict(orientation="h", y=0, x=0, bgcolor="rgba(0,0,0,0)", font=dict(size=14)), hovermode="closest")
    return fig


def progress_chart(df):
    """Best valid lap per session over time on one track."""
    fig = go.Figure()
    secs = df["best_lap_ms"] / 1000
    when = df["started_at"].str.replace("T", " ")
    best_so_far = secs.cummin()
    fig.add_scatter(x=when, y=best_so_far, mode="lines", line=dict(color=PURPLE, width=2.5, shape="hv"),
                    name="Personal best so far", hoverinfo="skip")
    fig.add_scatter(x=when, y=secs, mode="markers+lines", line=dict(color=FAINT, width=1, dash="dot"),
                    marker=dict(size=9, color=[PURPLE if s <= b else TEXT for s, b in zip(secs, best_so_far, strict=True)]),
                    name="Session best", customdata=[fmt_lap(v) for v in df["best_lap_ms"]],
                    hovertemplate="%{x}<br>%{customdata}<extra></extra>")
    lo, hi = float(secs.min()), float(secs.max())
    pad = max((hi - lo) * 0.15, 0.25)
    ticks = np.linspace(lo - pad, hi + pad, 5)
    fig.update_yaxes(title_text="best valid lap", range=[hi + pad, lo - pad],  # faster = higher
                     tickvals=ticks, ticktext=[fmt_lap(t * 1000) for t in ticks])
    fig.update_xaxes(type="category", title_text="session")
    return _style(fig, 340)


def tyre_life_chart(stints):
    """Lap time against tyre age, one series per stint, with the fitted line where there is one."""
    fig = go.Figure()
    palette = [PURPLE, BLUE, GREEN, YELLOW, TEXT]
    for k, s in enumerate(stints):
        colour = palette[k % len(palette)]
        timed = [lap for lap in s.laps if lap.timed]
        other = [lap for lap in s.laps if not lap.timed]
        label = f"Stint {k + 1}: {s.compound or 'compound n/a'}"
        fig.add_scatter(x=[lap.tyre_age_laps for lap in timed], y=[lap.lap_time_ms / 1000 for lap in timed],
                        mode="markers", name=label, marker=dict(size=9, color=colour),
                        customdata=[[lap.lap_number, fmt_lap(lap.lap_time_ms)] for lap in timed],
                        hovertemplate="Lap %{customdata[0]}: %{customdata[1]}<br>tyres %{x} laps old<extra></extra>")
        if other:
            fig.add_scatter(x=[lap.tyre_age_laps for lap in other], y=[lap.lap_time_ms / 1000 for lap in other],
                            mode="markers", name=f"{label}, invalid or pit", showlegend=False,
                            marker=dict(size=8, color=colour, symbol="circle-open"),
                            hovertemplate="not a timed lap<extra></extra>")
        if s.slope_s_per_lap is not None:
            ages = [lap.tyre_age_laps for lap in timed]
            times = [lap.lap_time_ms / 1000 for lap in timed]
            b = float(np.mean(times) - s.slope_s_per_lap * np.mean(ages))
            xs = [min(ages), max(ages)]
            fig.add_scatter(x=xs, y=[b + s.slope_s_per_lap * x for x in xs], mode="lines", showlegend=False,
                            line=dict(color=colour, width=1.5, dash="dash"), hoverinfo="skip")
    fig.update_xaxes(title_text="tyre age, laps (game)")
    fig.update_yaxes(title_text="lap time, s")
    return _style(fig, 330)


def comparison_chart(frame, lap_label, ref_label):
    fig = make_subplots(rows=5, cols=1, shared_xaxes=True, vertical_spacing=0.035,
                        row_heights=[0.2, 0.28, 0.18, 0.17, 0.17])
    d = frame["distance_m"]
    fig.add_scatter(x=d, y=frame["delta_s"], name="Running delta, + is slower", line=dict(color=YELLOW, width=2),
                    connectgaps=False, row=1, col=1)
    channels = (("speed", 2, "km/h", TEXT), ("throttle", 3, "throttle %", GREEN), ("brake", 4, "brake %", RED),
                ("steer", 5, "steer", BLUE))
    for col, row, title, colour in channels:
        fig.add_scatter(x=d, y=frame[f"{col}_ref"], name=f"{ref_label}" if row == 2 else None, showlegend=row == 2,
                        line=dict(color=FAINT, width=1.5), connectgaps=False, row=row, col=1, legendgroup="ref")
        fig.add_scatter(x=d, y=frame[f"{col}_lap"], name=f"{lap_label}" if row == 2 else None, showlegend=row == 2,
                        line=dict(color=PURPLE if col == "speed" else colour, width=2), connectgaps=False,
                        row=row, col=1, legendgroup="lap")
        fig.update_yaxes(title_text=title, row=row, col=1)
    fig.update_yaxes(title_text="Δ s", row=1, col=1)
    fig.update_yaxes(range=[-1.05, 1.05], row=5, col=1)
    fig.update_xaxes(title_text="lap distance (m)", row=5, col=1)
    fig.add_hline(y=0, line=dict(color=HIGH), row=1, col=1)
    return _style(fig, 760)
