"""PurpleSector diagnostics. Run from the project root:

    .venv\\Scripts\\python scripts\\diagnostics.py check            # environment, port, voice, database
    .venv\\Scripts\\python scripts\\diagnostics.py listen [-s 15]   # print what F1 22 is sending (app must be closed)
    .venv\\Scripts\\python scripts\\diagnostics.py send [-s 30]     # send synthetic loopback packets to the app

`send` encodes demo-simulator frames into real F1 22-format UDP packets. The app
recognises their session id, labels them "synthetic loopback test" and never records them.
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
        from voice.speaker import default_engine_factory
        default_engine_factory()
        print("  [ok] Windows speech (SAPI) available")
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
    types, bad, formats = Counter(), Counter(), Counter()
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
            if p.packet_id == PacketId.CAR_TELEMETRY:
                last = p
        except UnsupportedFormatError as exc:
            formats[exc.packet_format] += 1
        except PacketError as exc:
            bad[type(exc).__name__] += 1
    sock.close()
    if not (types or bad or formats):
        print("No packets received. Check F1 22: UDP Telemetry On, IP/port as above, and that you are on track.")
        return 1
    print("Decoded packet types:", dict(types))
    if formats:
        print("Wrong UDP format received:", dict(formats), "-> set UDP Format to 2022 in F1 22")
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
            if p.packet_id == PacketId.SESSION:
                raw = pb.build_session(p.header, p.data)
            elif p.packet_id == PacketId.EVENT:
                raw = pb.build_event(p.header, p.data.code)
            elif p.cars is not None:  # lap data, motion, participants: every car, as the game sends them
                prefix = bytes([p.num_active_cars or 0]) if p.packet_id == PacketId.PARTICIPANTS else b""
                raw = pb.build_cars(p.header, dict(enumerate(p.cars)), prefix=prefix)
            else:
                raw = pb.build_packet(p.header, p.data)
            sock.sendto(raw, target)
            sent += 1
        time.sleep(max(0.0, start + (frame + 1) / 60 - time.perf_counter()))
    sock.close()
    print(f"Sent {sent} packets. The Live page should have shown 'Connected' and a synthetic loopback label.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check", "listen", "send"))
    ap.add_argument("-s", "--seconds", type=float, default=15)
    args = ap.parse_args()
    settings, errors = load_settings()
    for k, v in errors.items():
        print(f"[settings] {k}: {v}")
    return {"check": lambda: check(settings), "listen": lambda: listen(settings, args.seconds),
            "send": lambda: send(settings, args.seconds)}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
