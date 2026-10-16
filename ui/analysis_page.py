"""Session analysis: compare any recorded lap with a reference lap on the same track."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from analysis.lap_comparison import compare_laps
from analysis.statistics import lap_input_stats
from service import get_service
from ui import components as c


def _session_label(row) -> str:
    src = "Demo" if row.source == "demo" else "Live"
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
    st.html('<div class="ps-bar"><span class="ps-brand">Session analysis</span>'
            '<span class="ps-meta">Lap and sector deltas come from game timing. Running delta and segment '
            'findings come from distance-aligned recorded telemetry.</span></div>')
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
        return
    ref_rows = {r.id: r for r in refs.itertuples()}
    ref_ids = list(ref_rows)
    ref_id = right.selectbox(
        "Reference lap", ref_ids, index=0,
        format_func=lambda i: (lambda r: f"{c.fmt_lap(r.lap_time_ms)} — lap {r.lap_number}, session #{r.session_id}"
                               + (" (fastest)" if i == ref_ids[0] else ""))(ref_rows[i]),
        help="Valid, fully recorded laps on this track, fastest first")
    lap, ref = lap_rows[lap_id], ref_rows[ref_id]
    with st.spinner("Aligning laps…"):
        cmp = compare_laps(repo.lap_samples(lap_id), repo.lap_samples(ref_id), lap_time_ms=int(lap.lap_time_ms),
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
                        config={"displayModeBar": False})
    if cmp.conclusive:
        st.subheader("Segments to review")
        st.caption("Time difference per 200 m segment (measured). Observed differences describe what was "
                   "different in that segment; they are correlations, not proven causes.")
        rows = [{"Segment": f"{s.start_m:.0f}–{s.end_m:.0f} m", "Time vs ref (s)": round(s.time_delta_s, 3),
                 "Observed differences": "; ".join(s.observations()) or "No notable input difference"}
                for s in cmp.segments[:5] if s.time_delta_s > 0.02]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)
        else:
            st.success("No segment lost more than 0.02 s to the reference.")


def _laps_table(laps: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Lap": laps.lap_number, "Time": laps.lap_time_ms.map(c.fmt_lap),
        "S1": laps.sector1_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "S2": laps.sector2_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "S3": laps.sector3_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "Valid": laps.valid.astype(bool), "Pit/out lap": (laps.pit_lap | laps.out_lap).astype(bool),
        "Full data": laps.samples_complete.astype(bool), "Top speed (km/h)": laps.max_speed_kph,
    })


def _single_lap(repo, laps, lap_id) -> None:
    stats = lap_input_stats(repo.lap_samples(lap_id))
    if stats:
        cols = st.columns(4)
        cols[0].metric("Top speed", f"{stats['max_speed_kph']:.0f} km/h")
        cols[1].metric("Mean sampled speed", f"{stats['mean_speed_kph']:.0f} km/h")
        cols[2].metric("Full throttle", f"{stats['full_throttle_pct']:.0f}%")
        cols[3].metric("Braking", f"{stats['braking_pct']:.0f}%")
