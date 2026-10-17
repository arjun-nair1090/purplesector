"""Raw packet capture: recording what the game sends, replaying it, reporting on it offline."""
import gzip
import socket

from conftest import wait_until

from demo.simulator import DemoSimulator
from telemetry import packet_builder as pb
from telemetry.capture import CaptureWriter, read_capture, report
from telemetry.connection import ConnectionMonitor
from telemetry.packet_models import CarTelemetryData, PacketId
from telemetry.receiver import UdpReceiver


def test_round_trip_keeps_bytes_and_order(tmp_path):
    path = tmp_path / "a.pscap.gz"
    w = CaptureWriter(path)
    for d in (b"one", b"\x00" * 1500, b"three"):
        w.write(d)
    w.close()
    got = list(read_capture(path))
    assert [d for _, d in got] == [b"one", b"\x00" * 1500, b"three"]
    assert [t for t, _ in got] == sorted(t for t, _ in got)
    assert w.count == 3 and w.closed


def test_a_capture_cut_off_mid_record_reads_up_to_the_cut(tmp_path):
    """The app was killed while capturing: everything before the damage still replays."""
    path = tmp_path / "cut.pscap.gz"
    w = CaptureWriter(path)
    for i in range(200):
        w.write(bytes([i % 256]) * 300)
    w.close()
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 2])
    got = list(read_capture(path))
    assert 0 < len(got) < 200
    assert all(d == bytes([i % 256]) * 300 for i, (_, d) in enumerate(got))


def test_not_a_capture_is_refused(tmp_path):
    path = tmp_path / "x.gz"
    with gzip.open(path, "wb") as f:
        f.write(b"hello")
    try:
        list(read_capture(path))
        raise AssertionError("should have refused")
    except ValueError as exc:
        assert "not a PurpleSector capture" in str(exc)


def test_writes_after_close_are_ignored(tmp_path):
    w = CaptureWriter(tmp_path / "c.pscap.gz")
    w.close()
    w.write(b"late")
    assert w.count == 0


def test_receiver_captures_every_datagram_even_rejected_ones(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    raw = []
    rx = UdpReceiver("127.0.0.1", port, ConnectionMonitor(), lambda p: None)
    rx.on_datagram = raw.append
    assert rx.start()
    try:
        good = pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, frame=1), CarTelemetryData(speed=100))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(good, ("127.0.0.1", port))
            s.sendto(b"\x00\x01junk", ("127.0.0.1", port))
        assert wait_until(lambda: len(raw) == 2)
        assert raw == [good, b"\x00\x01junk"]
    finally:
        rx.stop()


def test_report_summarises_a_session_offline(tmp_path):
    path = tmp_path / "s.pscap.gz"
    w = CaptureWriter(path)
    sim = DemoSimulator("quick_lap", run_index=0xFFFF)
    while sim.lap < 3:
        for p in sim.step():
            w.write(pb.encode(p))
    w.write(b"\x00\x01junk")
    w.close()
    r = report(path)
    assert r["formats"] == {2022: r["valid"]} and r["malformed"] == 1
    assert r["packet_types"]["LAP_DATA"] > 1000 and r["sessions"] == 1
    assert [lap["lap"] for lap in r["laps"]] == [1, 2]
    assert r["laps"][1]["samples_complete"] and r["laps"][1]["time"].startswith("1:")
    assert r["session_types"] == ["Race"] and r["duration_s"] > 60


def test_service_captures_live_datagrams_to_the_data_folder(tmp_path, monkeypatch):
    from conftest import FakeEngine

    import service as svc
    from config.settings import Settings
    ports = []
    for _ in range(2):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            ports.append(s.getsockname()[1])
    port = ports[0]
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="live", data_dir=str(tmp_path), udp_port=port, overlay_port=ports[1],
                                  voice_enabled=False),
                         engine_factory=FakeEngine)
    app.start()
    try:
        path = app.start_capture()
        assert path.parent == tmp_path / "captures" and app.start_capture() is None  # one at a time
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"\x00\x01raw", ("127.0.0.1", port))
        assert wait_until(lambda: app.capture.count == 1)
        app.stop_capture()
        assert app.capture is None and [d for _, d in read_capture(path)] == [b"\x00\x01raw"]
    finally:
        app.shutdown()


def test_capture_stops_at_its_size_limit(tmp_path):
    w = CaptureWriter(tmp_path / "big.pscap.gz", max_bytes=1000)
    for _ in range(10):
        w.write(b"x" * 300)
    w.close()
    assert w.full and w.count == 3 and len(list(read_capture(tmp_path / "big.pscap.gz"))) == 3
