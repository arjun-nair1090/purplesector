"""Always-on-top overlay: live delta, gear, gaps and the last radio call over the game.

Two halves in one file:
- serve(): a read-only JSON endpoint on 127.0.0.1, started by the app's service.
- main(): a small borderless tkinter window that polls it. Run `python overlay.py`
  (or press "Open overlay" in the sidebar). Drag it anywhere; right-click to close.

Windows only draws it over F1 22 when the game runs in borderless or windowed mode;
exclusive fullscreen covers every other window.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)
PATH = "/overlay.json"


def serve(port: int, payload) -> ThreadingHTTPServer | None:
    """Start the endpoint on a daemon thread. Returns None (logged) if the port is taken."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            if self.path != PATH:
                self.send_error(404)
                return
            try:
                body = json.dumps(payload()).encode()
            except Exception:
                log.exception("overlay payload failed")
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # 10 requests a second would flood the log

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        log.warning("Overlay endpoint unavailable on port %d: %s", port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="overlay-http", daemon=True).start()
    return server


# ---------------- window ----------------

GREEN, RED, TEXT, MUTED, PURPLE, BG = "#39D98A", "#FF5C5C", "#E9ECF2", "#8D94A3", "#A66CFF", "#171A21"
FONT = "Bahnschrift"


def _fmt_delta(d) -> tuple[str, str]:
    if d is None:
        return "—", MUTED
    if abs(d) < 0.0005:
        return "0.000", TEXT
    return f"{d:+.3f}".replace("-", "−"), GREEN if d < 0 else RED


def _fmt_gap(g) -> str:
    if not g or g.get("gap_s") is None:
        return "—"
    trend = g.get("change_per_lap_s")
    t = "" if trend is None or abs(trend) < 0.05 else f" ({trend:+.1f}/lap)".replace("-", "−")
    return f"P{g['position']} {g['gap_s']:.1f}s{t}"


def main() -> None:
    import tkinter as tk

    ap = argparse.ArgumentParser(description="PurpleSector always-on-top overlay")
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}{PATH}"

    try:  # crisp text on scaled displays (Windows); harmless elsewhere
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.title("PurpleSector overlay")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.95)
    root.configure(bg=BG, highlightthickness=2, highlightbackground=PURPLE)
    root.geometry("+40+40")

    delta = tk.Label(root, text="—", font=(FONT, 34, "bold"), fg=MUTED, bg=BG, width=7)
    ref = tk.Label(root, text="waiting for PurpleSector", font=(FONT, 9), fg=MUTED, bg=BG)
    gear = tk.Label(root, text="—", font=(FONT, 34, "bold"), fg=PURPLE, bg=BG, width=2)
    info = tk.Label(root, text="", font=(FONT, 11), fg=TEXT, bg=BG, justify="left", anchor="w")
    call = tk.Label(root, text="", font=(FONT, 10), fg=MUTED, bg=BG, wraplength=330, justify="left", anchor="w")
    delta.grid(row=0, column=0, padx=(10, 0), pady=(6, 0), sticky="w")
    gear.grid(row=0, column=1, rowspan=2, padx=10)
    ref.grid(row=1, column=0, padx=12, sticky="w")
    info.grid(row=2, column=0, columnspan=2, padx=12, pady=(4, 0), sticky="w")
    call.grid(row=3, column=0, columnspan=2, padx=12, pady=(2, 8), sticky="w")

    drag = {}

    def press(e):
        drag.update(x=e.x_root - root.winfo_x(), y=e.y_root - root.winfo_y())

    def move(e):
        root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    for w in (root, delta, ref, gear, info, call):
        w.bind("<ButtonPress-1>", press)
        w.bind("<B1-Motion>", move)
        w.bind("<Button-3>", lambda e: root.destroy())

    latest: dict = {}

    def poll():  # network off the Tk thread; the UI reads whatever arrived last
        import time
        while True:
            try:
                with urllib.request.urlopen(url, timeout=0.5) as r:
                    latest["data"], latest["error"] = json.loads(r.read()), None
            except Exception as exc:
                latest["error"] = exc
            time.sleep(0.1)

    threading.Thread(target=poll, daemon=True).start()

    def render():
        d = latest.get("data")
        if latest.get("error") is not None or d is None:
            delta.config(text="—", fg=MUTED)
            ref.config(text="PurpleSector is not running" if latest.get("error") else "connecting…")
            gear.config(text="—")
            info.config(text="")
            call.config(text="")
        else:
            text, color = _fmt_delta(d.get("delta_s"))
            delta.config(text=text, fg=color)
            ref.config(text=("vs " + d["delta_ref"]) if d.get("delta_ref") else "Δ after a full clean lap")
            gear.config(text=d.get("gear") or "—")
            lines = []
            if d.get("source") != "live":
                lines.append(f"{d.get('source', '').upper()} · synthetic data")
            if d.get("ahead") or d.get("behind"):
                lines.append(f"Ahead {_fmt_gap(d.get('ahead'))}   Behind {_fmt_gap(d.get('behind'))}")
            if d.get("fuel_margin_laps") is not None:
                lines.append(f"Fuel {d['fuel_margin_laps']:+.1f} laps ({d.get('fuel_source')})".replace("-", "−"))
            info.config(text="\n".join(lines))
            call.config(text=d.get("last_call") or "")
        root.after(100, render)

    render()
    root.mainloop()


if __name__ == "__main__":
    main()
