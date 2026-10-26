"""Session analysis: compare any recorded lap with a reference lap on the same track."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import streamlit as st

from analysis.lap_comparison import compare_laps
from analysis.setup import setup_diff
from analysis.statistics import lap_input_stats
from analysis.track_map import TrackMap, corners_in
from analysis.tyres import stints
from service import get_service
from ui import components as c


def _session_label(row) -> str:
    src = "Demo" if row.source == "demo" else "Live"
    if row.imported:
        src = f"Imported ({row.driver_name or 'other driver'})"
    best = c.fmt_lap(row.best_lap_ms) if pd.notna(row.best_lap_ms) else "no valid lap"
    return f"#{row.id} {row.started_at.replace('T', ' ')} — {src}, {row.track_name or 'unknown track'}, " \
           f"{row.laps} laps, best {best}"


def _lap_label(r) -> str:
    flags = [f for f, on in (("invalid", not r.valid), ("pit", r.pit_lap), ("out lap", r.out_lap),
                             ("partial data", not r.samples_complete)) if on]
    return f"Lap {r.lap_number} — {c.fmt_lap(r.lap_time_ms)}" + (f" ({', '.join(flags)})" if flags else "")


def render() -> None:
    svc = get_service()
    repo = svc.repo
    st.html('<div class="ps-bar"><div class="ps-where"><b>Session analysis</b></div></div>')
    sessions = repo.list_sessions()
    sessions = sessions[sessions.laps > 0]
    if sessions.empty:
        st.info("No recorded laps yet. Drive at least one full lap with recording on (sidebar), or run Demo mode "
                "with 'Save demo sessions' enabled.")
        return
    ids = sessions.id.tolist()
    pre = st.session_state.get("analysis_session_id")
    sid = st.selectbox("Session", ids, index=ids.index(pre) if pre in ids else 0,
                       format_func=lambda i: _session_label(sessions[sessions.id == i].iloc[0]))
    st.session_state["analysis_session_id"] = sid
    session = repo.get_session(sid)
    laps = repo.laps(sid)
    st.dataframe(_laps_table(laps), hide_index=True, height=min(35 * (len(laps) + 1) + 3, 300))

    left, right = st.columns(2)
    lap_rows = {r.id: r for r in laps.itertuples()}  # itertuples keeps per-column dtypes (ints stay ints)
    lap_ids = list(lap_rows)
    valid_ids = [i for i, r in lap_rows.items() if r.valid and not r.pit_lap and not r.out_lap]
    default = lap_ids.index(valid_ids[-1]) if valid_ids else len(lap_ids) - 1  # newest clean lap
    lap_id = left.selectbox("Lap to analyse", lap_ids, index=default,
                            format_func=lambda i: _lap_label(lap_rows[i]))
    refs = repo.reference_laps(session["track_name"], include_demo=session["source"] == "demo")
    refs = refs[refs.id != lap_id]
    if refs.empty:
        right.selectbox("Reference lap", ["—"], disabled=True)
        st.info("No reference lap available. A reference must be a valid, fully recorded, non-pit lap on the "
                f"same track ({session['track_name'] or 'unknown'}). Complete another clean lap to compare.")
        _single_lap(repo, laps, lap_id)
        _session_sections(repo, sid, laps)
        return
    ref_rows = {r.id: r for r in refs.itertuples()}
    ref_ids = list(ref_rows)
    ref_id = right.selectbox(
        "Reference lap", ref_ids, index=0,
        format_func=lambda i: (lambda r: f"{c.fmt_lap(r.lap_time_ms)} — lap {r.lap_number}, session #{r.session_id}"
                               + (f", {r.driver_name or 'imported'}" if r.imported else "")
                               + (" (fastest)" if i == ref_ids[0] else ""))(ref_rows[i]),
        help="Valid, fully recorded laps on this track, fastest first, including imported laps from other drivers")
    lap, ref = lap_rows[lap_id], ref_rows[ref_id]
    lap_df, ref_df = repo.lap_samples(lap_id), repo.lap_samples(ref_id)
    with st.spinner("Aligning laps…"):
        cmp = compare_laps(lap_df, ref_df, lap_time_ms=int(lap.lap_time_ms),
                           ref_time_ms=int(ref.lap_time_ms),
                           lap_sectors=(lap.sector1_ms, lap.sector2_ms, lap.sector3_ms),
                           ref_sectors=(ref.sector1_ms, ref.sector2_ms, ref.sector3_ms))

    m = st.columns(6)
    m[0].metric("Lap", c.fmt_lap(lap.lap_time_ms))
    m[1].metric("Reference", c.fmt_lap(ref.lap_time_ms))
    m[2].metric("Lap delta", f"{c.fmt_delta(cmp.lap_time_delta_s)} s", help="Game timing. Positive = slower.")
    for i in range(3):
        m[3 + i].metric(f"S{i + 1} delta", f"{c.fmt_delta(cmp.sector_deltas_s[i])} s",
                        help="Game sector timing. Positive = slower.")
    if not lap.valid:
        st.warning("The analysed lap was invalidated by the game; treat its comparison with caution.")

    for reason in cmp.reasons:
        st.warning(f"Comparison inconclusive: {reason}")
    if cmp.frame is not None:
        st.plotly_chart(c.comparison_chart(cmp.frame, f"Lap {lap.lap_number}", f"Reference (lap {ref.lap_number})"),
                        config={"displayModeBar": False, "responsive": True})
    track_map = TrackMap.from_samples(ref_df) or TrackMap.from_samples(lap_df)
    corners = track_map.corners if track_map else []
    if cmp.conclusive and track_map is not None:
        _delta_map(track_map, cmp.frame)
    if cmp.conclusive:
        st.subheader("Segments to review")
        st.caption("Time difference per 200 m segment (measured). Observed differences describe what was "
                   "different in that segment; they are correlations, not proven causes. Corner numbers are "
                   "detected from the reference lap's speed trace.")
        rows = [{"Segment": f"{s.start_m:.0f}–{s.end_m:.0f} m", "Corner": corners_in(corners, s.start_m, s.end_m) or "—",
                 "Time vs ref (s)": round(s.time_delta_s, 3),
                 "Observed differences": "; ".join(s.observations()) or "No notable input difference"}
                for s in cmp.segments[:5] if s.time_delta_s > 0.02]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)
        else:
            st.success("No segment lost more than 0.02 s to the reference.")
    _inputs(lap_df, ref_df)
    _setup(repo, lap_id, ref_id)
    _session_sections(repo, sid, laps)


def _inputs(lap_df, ref_df) -> None:
    a, r = lap_input_stats(lap_df), lap_input_stats(ref_df)
    if not a or not r:
        return
    st.subheader("Inputs")
    cols = st.columns(4)
    for col, (key, label, helptext) in zip(cols, (
            ("full_throttle_pct", "Full throttle", "Share of the lap at 98% throttle or more"),
            ("braking_pct", "Braking", "Share of the lap at 5% brake or more"),
            ("coasting_pct", "Coasting", "Share of the lap off both pedals (under 5%) above 50 km/h"),
            ("brake_steer_pct", "Braking while turning", "Of the time on the brakes, the share with 10% or more "
                                                          "steering input: a trail-braking indicator")), strict=True):
        if a[key] is None:
            col.metric(label, "—", help=helptext)
            continue
        diff = None if r[key] is None else a[key] - r[key]
        col.metric(label, f"{a[key]:.1f}%", None if diff is None else f"{diff:+.1f} pts vs reference",
                   delta_color="off", help=helptext)
    st.caption("Measured from the recorded samples of both laps. They describe how you drove, not whether it "
               "was right: more coasting or trail braking can be faster or slower depending on the corner.")


def _setup(repo, lap_id, ref_id) -> None:
    rows = setup_diff(repo.setup(lap_id), repo.setup(ref_id))
    st.subheader("Setup")
    if rows is None:
        st.caption("One of these laps has no recorded setup (laps from before version 1.1, or the game didn't "
                   "send the car-setups packet).")
    elif not rows:
        st.caption("Both laps were driven on the same setup, as the game reported it at the line.")
    else:
        st.dataframe(pd.DataFrame(rows, columns=["Setting", "This lap", "Reference"]), hide_index=True)
        st.caption("As the game reported each setup at the line crossing.")


def _session_sections(repo, sid, laps) -> None:
    rows = [SimpleNamespace(lap_number=int(r.lap_number), lap_time_ms=int(r.lap_time_ms),
                            tyre_age_laps=None if pd.isna(r.tyre_age_laps) else int(r.tyre_age_laps),
                            tyre_compound=None if pd.isna(r.tyre_compound) else r.tyre_compound,
                            timed=bool(r.valid and not r.pit_lap and not r.out_lap and r.lap_time_ms > 0))
            for r in laps.itertuples()]
    found = stints(rows)
    if any(len(s.laps) >= 2 for s in found):
        st.subheader("Tyre life")
        left, right = st.container(key="split_tyre_life").columns([1.6, 1])
        left.plotly_chart(c.tyre_life_chart(found), config={"displayModeBar": False, "responsive": True})
        right.dataframe(pd.DataFrame({
            "Stint": range(1, len(found) + 1), "Compound": [s.compound or "—" for s in found],
            "Laps": [len(s.laps) for s in found],
            "Trend (s per lap)": [None if s.slope_s_per_lap is None else round(s.slope_s_per_lap, 3) for s in found]}),
            hide_index=True)
        right.caption("Trend is a straight line through each stint's timed laps (3 or more), against the tyre age the "
                      "game reported. It includes everything else that changed lap to lap, above all the car getting "
                      "lighter as fuel burns off, so it is not tyre wear alone.")
    events = repo.events(sid)
    if not events.empty:
        with st.expander(f"Race control ({len(events)} messages)"):
            st.dataframe(pd.DataFrame({"Lap": events.lap_number, "Message": events.message}), hide_index=True)


def _delta_map(track_map, frame) -> None:
    """Where on the circuit time was gained or lost: the running delta's slope, per point of the outline."""
    d = frame["distance_m"].to_numpy(float)
    rate = np.gradient(frame["delta_s"].to_numpy(float), d) * 100  # seconds per 100 m
    rate = np.convolve(np.nan_to_num(rate), np.ones(9) / 9, mode="same")  # 45 m smoothing
    per_point = np.interp(track_map.distance_m, d, rate, left=np.nan, right=np.nan)
    st.subheader("Where the time went")
    left, right = st.container(key="split_delta_map").columns([1.4, 1])
    with left:
        st.plotly_chart(c.track_map_chart(track_map, color=per_point, color_label="s / 100 m", height=420),
                        config={"displayModeBar": False, "responsive": True})
    right.caption("Green: gaining on the reference. Red: losing to it. Colour is the slope of the running delta "
                  "(seconds per 100 m), from distance-aligned telemetry. Numbered badges are detected corners.")
    if track_map.corners:
        right.dataframe(pd.DataFrame({"Corner": [k.number for k in track_map.corners],
                                      "At (m)": [round(k.apex_m) for k in track_map.corners],
                                      "Ref minimum (km/h)": [round(k.min_speed_kph) for k in track_map.corners]}),
                        hide_index=True, height=min(35 * (len(track_map.corners) + 1) + 3, 360))


def _laps_table(laps: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Lap": laps.lap_number, "Time": laps.lap_time_ms.map(c.fmt_lap),
        "S1": laps.sector1_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "S2": laps.sector2_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "S3": laps.sector3_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "Valid": laps.valid.astype(bool), "Pit/out lap": (laps.pit_lap | laps.out_lap).astype(bool),
        "Full data": laps.samples_complete.astype(bool), "Top speed (km/h)": laps.max_speed_kph,
        "Tyre": [f"{t}, {int(a)} lap{'' if int(a) == 1 else 's'}" if isinstance(t, str) and pd.notna(a) else "—"
                 for t, a in zip(laps.tyre_compound, laps.tyre_age_laps, strict=True)],
        "Fuel used (kg)": laps.fuel_used_kg.map(lambda v: f"{v:.2f}" if pd.notna(v) else "—"),
    })


def _single_lap(repo, laps, lap_id) -> None:
    stats = lap_input_stats(repo.lap_samples(lap_id))
    if stats:
        cols = st.columns(4)
        cols[0].metric("Top speed", f"{stats['max_speed_kph']:.0f} km/h")
        cols[1].metric("Mean sampled speed", f"{stats['mean_speed_kph']:.0f} km/h")
        cols[2].metric("Full throttle", f"{stats['full_throttle_pct']:.0f}%")
        cols[3].metric("Braking", f"{stats['braking_pct']:.0f}%")
