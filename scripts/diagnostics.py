"""PurpleSector diagnostics. Run from the project root:

    .venv\\Scripts\\python scripts\\diagnostics.py check            # environment, port, voice, database
    .venv\\Scripts\\python scripts\\diagnostics.py listen [-s 15]   # print what the game is sending (app must be closed)
    .venv\\Scripts\\python scripts\\diagnostics.py send [-s 30]     # send synthetic loopback packets to the app
    .venv\\Scripts\\python scripts\\diagnostics.py report FILE      # summarise a packet capture offline
    .venv\\Scripts\\python scripts\\diagnostics.py replay FILE [-x 2]  # resend a capture to the app at its pace

`send` encodes demo-simulator frames into real F1 22-format UDP packets. The app
recognises their session id, labels them "synthetic loopback test" and never records them.
Captures (data/captures/*.pscap.gz) are recorded from the Live page's Diagnostics panel
while you drive. A replayed capture is a real game session, so the app records it like one.
"""
from __future__ import annotations

import argparse
import importlib
import socket
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings  # noqa: E402
from demo.simulator import DemoSimulator  # noqa: E402
from telemetry import packet_builder as pb  # noqa: E402
from telemetry.decoder import PacketError, UnsupportedFormatError, decode_packet  # noqa: E402
from telemetry.packet_models import PacketId  # noqa: E402


def check(settings) -> int:
    ok = True
    print(f"Python {sys.version.split()[0]} at {sys.executable}")
    for mod in ("streamlit", "plotly", "pandas", "numpy", "comtypes"):
        try:
            m = importlib.import_module(mod)
            print(f"  [ok] {mod} {getattr(m, '__version__', '')}")
        except ImportError as exc:
            ok = False
            print(f"  [missing] {mod}: {exc}. Run: .venv\\Scripts\\python -m pip install -r requirements.txt")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((settings.udp_host, settings.udp_port))
        print(f"  [ok] UDP {settings.udp_host}:{settings.udp_port} is free")
    except OSError:
        print(f"  [info] UDP {settings.udp_host}:{settings.udp_port} is in use: expected while PurpleSector is "
              "running, otherwise another telemetry app holds it.")
    finally:
        s.close()
    try:
        from voice import engines
        engines.make_synth("sapi:")
        print("  [ok] Windows speech (SAPI) available")
        spec, why = engines.resolve(settings.voice_name, settings.data_path / "voices")
        if spec.startswith("piper:"):
            engines.make_synth(spec)
            print(f"  [ok] engineer's voice {settings.voice_name} loads")
        elif why:
            print(f"  [info] {why}")
    except Exception as exc:
        print(f"  [warn] voice unavailable: {exc} (the app still works without voice)")
    db = settings.data_path / "purplesector.db"
    print(f"  [info] data directory {settings.data_path} ({'database exists' if db.exists() else 'no database yet'})")
    return 0 if ok else 1


def listen(settings, seconds: float) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((settings.udp_host, settings.udp_port))
    except OSError as exc:
        print(f"Cannot bind {settings.udp_host}:{settings.udp_port}: {exc.strerror}. Close PurpleSector first.")
        return 1
    sock.settimeout(0.5)
    types, bad, formats, years = Counter(), Counter(), Counter(), Counter()
    last = None
    end = time.monotonic() + seconds
    print(f"Listening on {settings.udp_host}:{settings.udp_port} for {seconds:.0f} s ...")
    while time.monotonic() < end:
        try:
            data = sock.recv(2048)
        except TimeoutError:
            continue
        try:
            p = decode_packet(data)
            types[p.packet_id.name] += 1
            years[p.header.packet_format] += 1
            if p.packet_id == PacketId.CAR_TELEMETRY:
                last = p
        except UnsupportedFormatError as exc:
            formats[exc.packet_format] += 1
        except PacketError as exc:
            bad[type(exc).__name__] += 1
    sock.close()
    if not (types or bad or formats):
        print("No packets received. Check the game: UDP Telemetry On, IP/port as above, and that you are on track.")
        return 1
    print("Decoded packet types:", dict(types))
    print("UDP format:", ", ".join(f"{y} ({n} packets)" for y, n in sorted(years.items())))
    if formats:
        print("Unsupported UDP format received:", dict(formats), "-> set UDP Format to 2022, 2023, 2024 or 2025")
    if bad:
        print("Rejected:", dict(bad))
    if last:
        d = last.data
        print(f"Player car {last.header.player_car_index}: {d.speed} km/h, gear {d.gear}, {d.engine_rpm} rpm, "
              f"throttle {d.throttle:.0%}, brake {d.brake:.0%}")
    return 0


def send(settings, seconds: float) -> int:
    sim = DemoSimulator("standard", run_index=0xFFFF)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = ("127.0.0.1" if settings.udp_host == "0.0.0.0" else settings.udp_host, settings.udp_port)
    print(f"Sending synthetic F1 22-format packets to {target[0]}:{target[1]} for {seconds:.0f} s ...")
    sent, start = 0, time.perf_counter()
    for frame in range(int(seconds * 60)):
        for p in sim.step():
            sock.sendto(pb.encode(p), target)
            sent += 1
        time.sleep(max(0.0, start + (frame + 1) / 60 - time.perf_counter()))
    sock.close()
    print(f"Sent {sent} packets. The Live page should have shown 'Connected' and a synthetic loopback label.")
    return 0


def report_cmd(path: Path) -> int:
    from telemetry.capture import report
    r = report(path)
    print(f"{path.name}: {r['valid']} valid packets over {r['duration_s']:.0f} s of game time, "
          f"{r['malformed']} malformed, {r['ignored']} unused")
    print("UDP format:", r["formats"] or "none", "| unsupported:", r["unsupported"] or "none")
    print("Sessions:", r["sessions"], "|", ", ".join(r["session_types"]) or "no session packet",
          "|", ", ".join(r["tracks"]) or "no track")
    print("Packet types:", dict(sorted(r["packet_types"].items())))
    for e in r["errors"]:
        print("  rejected:", e)
    if not r["laps"]:
        print("No completed laps.")
    for lap in r["laps"]:
        flags = [f for f, on in (("invalid", not lap["valid"]), ("pit", lap["pit"]),
                                 ("partial data", not lap["samples_complete"])) if on]
        print(f"  lap {lap['lap']:>3}  {lap['time']:>9}  {lap['samples']:>5} samples  {', '.join(flags)}")
    return 0 if r["valid"] else 1


def replay(settings, path: Path, speed: float) -> int:
    from telemetry.capture import read_capture
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = ("127.0.0.1" if settings.udp_host == "0.0.0.0" else settings.udp_host, settings.udp_port)
    print(f"Replaying {path.name} to {target[0]}:{target[1]} at {speed:g}x ...")
    start, sent = time.perf_counter(), 0
    for t, data in read_capture(path):
        time.sleep(max(0.0, start + t / speed - time.perf_counter()))
        sock.sendto(data, target)
        sent += 1
    sock.close()
    print(f"Sent {sent} datagrams.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check", "listen", "send", "report", "replay"))
    ap.add_argument("file", nargs="?", type=Path, help="capture file (report, replay)")
    ap.add_argument("-s", "--seconds", type=float, default=15)
    ap.add_argument("-x", "--speed", type=float, default=1.0, help="replay speed")
    args = ap.parse_args()
    settings, errors = load_settings()
    for k, v in errors.items():
        print(f"[settings] {k}: {v}")
    if args.command in ("report", "replay") and (args.file is None or not args.file.exists()):
        ap.error(f"{args.command} needs an existing capture file")
    return {"check": lambda: check(settings), "listen": lambda: listen(settings, args.seconds),
            "send": lambda: send(settings, args.seconds), "report": lambda: report_cmd(args.file),
            "replay": lambda: replay(settings, args.file, args.speed)}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
