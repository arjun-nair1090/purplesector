"""Recorded sessions: browse, open in analysis, export, delete."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from service import get_service
from ui import components as c


def render() -> None:
    svc = get_service()
    repo = svc.repo
    st.html('<div class="ps-bar"><span class="ps-brand">Session history</span>'
            f'<span class="ps-meta">Stored locally in {c.esc(repo.db_path)}</span></div>')
    sessions = repo.list_sessions()
    if sessions.empty:
        st.info("No sessions recorded yet. Sessions are saved automatically in Live mode while 'Record sessions' "
                "is on. Each completed lap is written with its telemetry.")
        return
    table = pd.DataFrame({
        "ID": sessions.id,
        "Started": sessions.started_at.str.replace("T", " "),
        "Source": sessions.source.map({"live": "Live (F1 22)", "demo": "Demo (synthetic)"}),
        "Track": sessions.track_name.fillna("unknown"),
        "Session": sessions.session_type.fillna("—"),
        "Laps": sessions.laps,
        "Best valid lap": sessions.best_lap_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
        "Status": sessions.status.map({"complete": "Complete", "recording": "Recording",
                                       "interrupted": "Interrupted (app closed)"}),
    })
    st.dataframe(table, hide_index=True, height=min(35 * (len(table) + 1) + 3, 360))

    ids = sessions.id.tolist()
    sid = st.selectbox("Selected session", ids, format_func=lambda i: f"#{i} — "
                       + table[table.ID == i].iloc[0].Started + " — " + table[table.ID == i].iloc[0].Track)
    live_recording = svc.recording and svc.writer.session_id(svc.recording_key) == sid
    laps = repo.laps(sid)
    if laps.empty:
        st.caption("This session has no completed laps.")
    else:
        st.dataframe(pd.DataFrame({
            "Lap": laps.lap_number, "Time": laps.lap_time_ms.map(c.fmt_lap),
            "S1": laps.sector1_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
            "S2": laps.sector2_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
            "S3": laps.sector3_ms.map(lambda v: c.fmt_lap(v) if pd.notna(v) else "—"),
            "Valid": laps.valid.astype(bool), "Full data": laps.samples_complete.astype(bool),
            "Samples": laps.sample_count,
        }), hide_index=True)

    a, b, d, e = st.columns(4)
    if a.button("Open in analysis", type="primary", width="stretch", disabled=laps.empty):
        st.session_state["analysis_session_id"] = sid
        st.switch_page(st.session_state["_pages"]["analysis"])
    export_dir = svc.settings.data_path / "exports"
    for col, kind in ((b, "CSV"), (d, "JSON")):
        if col.button(f"Export {kind}", width="stretch", disabled=laps.empty):
            try:
                path = repo.export_csv(sid, export_dir) if kind == "CSV" else repo.export_json(sid, export_dir)
                st.session_state["last_export"] = str(path)
            except Exception as exc:
                st.error(f"Export failed: {exc}")
    last = st.session_state.get("last_export")
    if last and f"_session{sid}_" in Path(last).name:  # only for the session on screen
        p = Path(last)
        if p.exists():
            st.success(f"Exported {p.name} to {p.parent}")
            st.download_button(f"Download {p.name}", p.read_bytes(), file_name=p.name,
                               mime="text/csv" if p.suffix == ".csv" else "application/json")
    with e.popover("Delete session", width="stretch", disabled=live_recording, key=f"delete_popover_{sid}",
                   help="Unavailable while this session is still recording" if live_recording else None):
        st.write(f"Delete session #{sid} and all its laps and telemetry? This cannot be undone.")
        confirm = st.checkbox("Yes, delete it permanently", key=f"confirm_del_{sid}")
        if st.button("Delete session", type="primary", disabled=not confirm, key=f"del_{sid}"):
            repo.delete_session(sid)
            st.session_state.pop("analysis_session_id", None)
            st.toast(f"Session #{sid} deleted")
            st.rerun()
