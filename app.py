"""PurpleSector — F1 22 telemetry dashboard and race engineer. Run: streamlit run app.py"""
import streamlit as st

from config.defaults import PROJECT_ROOT
from service import get_service
from ui import analysis_page, dashboard, history_page, settings_page
from ui.components import CSS

st.set_page_config(page_title="PurpleSector", page_icon=str(PROJECT_ROOT / "assets" / "icon.png"), layout="wide",
                   initial_sidebar_state="expanded")
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

# Plotly only re-measures on a window resize; opening/closing the sidebar changes the content width
# without one, leaving charts at a stale width. Re-dispatch a resize whenever the main area changes size.
st.iframe("""<script>
const w = window.parent;
if (!w.__psResizeHook) {
  w.__psResizeHook = true;
  const main = w.document.querySelector('[data-testid="stMain"]');
  let settle;
  const fire = () => w.dispatchEvent(new Event('resize'));
  // fire now, and again once the sidebar's slide animation has finished
  if (main) new ResizeObserver(() => { fire(); clearTimeout(settle); settle = setTimeout(fire, 400); }).observe(main);
}
</script>""", height=1)  # st.iframe rejects 0; CSS (ps-hook) takes the 1 px container out of the layout
