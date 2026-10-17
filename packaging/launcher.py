"""Entry point of the packaged PurpleSector.exe (PyInstaller).

    PurpleSector.exe              start the dashboard and open it in the browser
    PurpleSector.exe --overlay    the always-on-top overlay (the dashboard starts it)
    PurpleSector.exe report FILE  scripts/diagnostics.py: check, listen, send, report FILE, replay FILE
"""
import os
import runpy
import sys
from pathlib import Path


def _bundled_modules():  # never called: lists what app.py imports at run time, so PyInstaller packs it
    import overlay  # noqa: F401
    import service  # noqa: F401
    from telemetry import packet_builder  # noqa: F401  (used by the diagnostics commands)
    from ui import analysis_page, dashboard, history_page, settings_page  # noqa: F401


DIAGNOSTICS = ("check", "listen", "send", "report", "replay")


def main() -> int:
    if "--overlay" in sys.argv:
        sys.argv.remove("--overlay")
        import overlay
        overlay.main()
        return 0
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] in DIAGNOSTICS:
        sys.argv[0] = "diagnostics"
        runpy.run_path(str(root / "scripts" / "diagnostics.py"), run_name="__main__")
        return 0
    os.chdir(root)  # Streamlit reads .streamlit/config.toml and static/ from here
    from streamlit.web import cli
    sys.argv = ["streamlit", "run", str(root / "app.py"), "--global.developmentMode=false"]
    if not os.environ.get("STREAMLIT_SERVER_HEADLESS"):
        sys.argv.append("--server.headless=false")  # open the dashboard in the browser
    return cli.main()


if __name__ == "__main__":
    sys.exit(main())
