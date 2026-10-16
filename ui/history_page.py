"""Recorded sessions: progress per track, browse, open in analysis, export, import, delete."""
from __future__ import annotations

import json
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
                "is on. Each completed lap is written with its telemetry. You can also import a friend's "
                "exported session below.")
        _import(repo)
        return
    _progress(repo, sessions)
    source = sessions.source.map({"live": "Live (F1 22)", "demo": "Demo (synthetic)"})
    imported = sessions.imported.astype(bool)
    source = source.where(~imported, "Imported · " + sessions.driver_name.fillna("other driver"))
    table = pd.DataFrame({
        "ID": sessions.id,
        "Started": sessions.started_at.str.replace("T", " "),
        "Source": source,
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
            "Fuel used (kg)": laps.fuel_used_kg.map(lambda v: f"{v:.2f}" if pd.notna(v) else "—"),
            "Tyre wear (%)": laps.tyre_wear_pct.map(lambda v: f"{v:.2f}" if pd.notna(v) else "—"),
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
    _import(repo)


def _progress(repo, sessions) -> None:
    """Best valid lap per session on one track: are you actually getting faster?"""
    own = sessions[(sessions.imported == 0) & sessions.best_lap_ms.notna() & sessions.track_name.notna()]
    if own.empty:
        return
    tracks = (own.groupby(["track_name", "source"]).size().reset_index(name="n")
              .sort_values("n", ascending=False))
    keys = list(zip(tracks.track_name, tracks.source, strict=True))
    st.subheader("Progress")
    a, b = st.columns([1, 3])
    track, source = a.selectbox("Track", keys, format_func=lambda k: k[0] + (" (demo)" if k[1] == "demo" else ""))
    df = repo.progress(track, source)
    if len(df) < 2:
        b.info(f"One session with a valid lap at {track} so far. The chart appears from your second session.")
        return
    first, best = df.best_lap_ms.iloc[0], df.best_lap_ms.min()
    gained = (best - first) / 1000
    a.metric("Personal best", c.fmt_lap(best),
             f"{gained:+.3f} s vs first session" if abs(gained) >= 0.0005 else None,
             delta_color="inverse")
    if abs(gained) < 0.0005:
        a.caption("Same as your first session here.")
    a.caption(f"{len(df)} sessions with a valid lap. Your laps only; imported laps never count.")
    with b:
        st.plotly_chart(c.progress_chart(df), config={"displayModeBar": False, "responsive": True})


def _import(repo) -> None:
    if done := st.session_state.pop("import_done", None):
        st.success(done)
    with st.expander("Import a friend's lap"):
        st.caption("Load another driver's PurpleSector JSON export (History → Export JSON on their PC). Their laps "
                   "show up as reference laps on Session analysis for the same track, labelled with their name. "
                   "They never count as your personal best.")
        up = st.file_uploader("Session export (.json)", type=["json"], key="import_file")
        name = st.text_input("Driver name", max_chars=48, placeholder="e.g. Sam")
        if st.button("Import", disabled=up is None, type="primary"):
            try:
                sid = repo.import_session(json.loads(up.getvalue().decode("utf-8-sig")), name)
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(f"Import failed: {exc}")
                return
            s = repo.get_session(sid)
            st.session_state["import_done"] = (f"Imported session #{sid}: {s['driver_name']} at {s['track_name']}. "
                                               "Open Session analysis and pick one of your laps on that track to "
                                               "compare.")
            st.session_state.pop("import_file", None)  # clear the uploader
            st.rerun()  # so the session table above lists it
