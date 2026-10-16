"""Integration: real UDP sockets on localhost, synthetic spec-sized packets."""
import socket
import threading

from conftest import FakeEngine, wait_until

from config.settings import Settings
from telemetry import packet_builder as pb
from telemetry.connection import ConnectionMonitor, ConnectionState
from telemetry.packet_models import CarTelemetryData, LapData, PacketId
from telemetry.receiver import UdpReceiver


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send(port, *datagrams):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for d in datagrams:
        s.sendto(d, ("127.0.0.1", port))
    s.close()


def test_receiver_decodes_and_counts():
    port, got = _free_port(), []
    mon = ConnectionMonitor()
    rx = UdpReceiver("127.0.0.1", port, mon, got.append)
    assert rx.start()
    try:
        assert mon.state() == ConnectionState.WAITING  # socket open is not "connected"
        _send(port, pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, frame=1), CarTelemetryData(speed=200)),
              b"\x00\x01garbage", pb.build_packet(pb.header(PacketId.CAR_SETUPS)),
              pb.build_packet(pb.header(PacketId.LAP_DATA, frame=1), LapData(current_lap_num=2)))
        assert wait_until(lambda: mon.packets_total == 4)
        assert mon.state() == ConnectionState.CONNECTED
        assert [p.packet_id for p in got] == [PacketId.CAR_TELEMETRY, PacketId.LAP_DATA]
        assert got[0].data.speed == 200
        assert mon.malformed == 1 and mon.ignored == 1 and mon.packets_valid == 2
    finally:
        rx.stop()
    assert not rx.running and mon.state() == ConnectionState.DISCONNECTED


def test_unsupported_format_state():
    port = _free_port()
    mon = ConnectionMonitor()
    rx = UdpReceiver("127.0.0.1", port, mon, lambda p: None)
    rx.start()
    try:
        _send(port, pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, fmt=2021)))
        assert wait_until(lambda: mon.unsupported == 1)
        assert mon.state() == ConnectionState.UNSUPPORTED_FORMAT
    finally:
        rx.stop()


def test_connection_goes_stale():
    t = [0.0]
    mon = ConnectionMonitor(clock=lambda: t[0], stale_s=2.0)
    mon.set_running(True)
    mon.record_valid("CAR_TELEMETRY")
    assert mon.state() == ConnectionState.CONNECTED
    t[0] = 2.5
    assert mon.state() == ConnectionState.RECONNECTING


def test_port_conflict_reports_error_and_restart_works():
    port = _free_port()
    mon1, mon2 = ConnectionMonitor(), ConnectionMonitor()
    a = UdpReceiver("127.0.0.1", port, mon1, lambda p: None)
    b = UdpReceiver("127.0.0.1", port, mon2, lambda p: None)
    assert a.start()
    try:
        assert not b.start()
        assert mon2.state() == ConnectionState.RECEIVER_ERROR and "already in use" in mon2.error
    finally:
        a.stop()
    # port released cleanly: can bind again, repeatedly
    for _ in range(3):
        assert b.start()
        b.stop()
    assert not any(t.name == "udp-receiver" for t in threading.enumerate())


def test_handler_exception_does_not_kill_receiver():
    port, calls = _free_port(), []

    def boom(p):
        calls.append(p)
        raise RuntimeError("handler bug")
    mon = ConnectionMonitor()
    rx = UdpReceiver("127.0.0.1", port, mon, boom)
    rx.start()
    try:
        pkt = pb.build_packet(pb.header(PacketId.CAR_TELEMETRY), CarTelemetryData())
        _send(port, pkt, pkt)
        assert wait_until(lambda: len(calls) == 2)
        assert rx.running
    finally:
        rx.stop()


def test_service_live_path_end_to_end(tmp_path, monkeypatch):
    """UDP -> receiver -> service -> state; then a clean receiver restart via settings."""
    import service as svc
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    port = _free_port()
    app = svc.AppService(Settings(udp_port=port, data_dir=str(tmp_path)), engine_factory=FakeEngine)
    app.start()
    try:
        _send(port, pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, frame=5), CarTelemetryData(speed=250,
                                                                                                 throttle=0.5)))
        assert wait_until(lambda: app.state.snapshot().speed_kph == 250)
        snap = app.state.snapshot()
        assert snap.source == "live" and snap.throttle_pct == 50.0 and snap.lap_number is None
        new_port = _free_port()
        assert app.update_settings(udp_port=new_port) == {}
        _send(new_port, pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, frame=6, session_uid=9),
                                        CarTelemetryData(speed=111)))
        assert wait_until(lambda: app.state.snapshot().speed_kph == 111)
    finally:
        app.shutdown()
