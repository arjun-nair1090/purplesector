"""PurpleSector — F1 22 telemetry dashboard and race engineer. Run: streamlit run app.py"""
import streamlit as st

from service import get_service
from ui import analysis_page, dashboard, history_page, settings_page
from ui.components import CSS

st.set_page_config(page_title="PurpleSector", page_icon="🟪", layout="wide", initial_sidebar_state="expanded")
st.html(CSS)
get_service()  # starts the receiver/workers once per process

pages = {
    "live": st.Page(dashboard.render, title="Live", icon=":material/speed:", default=True),
    "analysis": st.Page(analysis_page.render, title="Session analysis", icon=":material/timeline:", url_path="analysis"),
    "history": st.Page(history_page.render, title="History", icon=":material/history:", url_path="history"),
    "settings": st.Page(settings_page.render, title="Settings", icon=":material/tune:", url_path="settings"),
}
st.session_state["_pages"] = pages
nav = st.navigation(list(pages.values()), position="top")
dashboard.sidebar()
nav.run()
