from dataclasses import replace

import pytest
from conftest import FakeEngine, wait_until

from config.settings import Settings
from demo.simulator import INTERRUPT_AT_S, INTERRUPT_FOR_S, DemoSimulator
from telemetry import packet_builder as pb
from telemetry.decoder import decode_packet
from telemetry.packet_models import CarTelemetryData, PacketId, pack_record


def run(sim, seconds):
    out = []
    for _ in range(int(seconds * 60)):
        out.extend(sim.step())
    return out


def test_deterministic():
    a, b = run(DemoSimulator(), 30), run(DemoSimulator(), 30)
    assert [(p.packet_id, p.data) for p in a] == [(p.packet_id, p.data) for p in b]


def test_signals_are_coherent():
    pk = [p.data for p in run(DemoSimulator(), 90) if p.packet_id == PacketId.CAR_TELEMETRY]
    assert max(p.speed for p in pk) > 280 and min(p.speed for p in pk) < 110
    assert all(not (p.brake > 0 and p.throttle > 0) for p in pk)  # never both pedals
    assert all(1 <= p.gear <= 8 and 4000 <= p.engine_rpm <= 12000 for p in pk)
    assert all(-1 <= p.steer <= 1 for p in pk)


def test_demo_records_pass_through_real_decoder():
    p = next(p for p in DemoSimulator().step() if p.packet_id == PacketId.CAR_TELEMETRY)
    raw = pb.build_packet(p.header, p.data)
    got = decode_packet(raw).data
    assert replace(got, tyres_pressure=(), throttle=0, steer=0, brake=0) ==         replace(p.data, tyres_pressure=(), throttle=0, steer=0, brake=0)
    assert got.tyres_pressure == pytest.approx(p.data.tyres_pressure, rel=1e-6)  # float32 on the wire
    assert got.throttle == pytest.approx(p.data.throttle, rel=1e-6)
    assert len(pack_record(p.data)) == 60


def test_lap_completes_with_sectors():
    laps = [p.data for p in run(DemoSimulator(), 95) if p.packet_id == PacketId.LAP_DATA]
    assert laps[-1].current_lap_num == 2 and 80000 < laps[-1].last_lap_time_ms < 95000
    assert any(lp.sector1_time_ms and lp.sector2_time_ms for lp in laps)


def test_quick_lap_scenario_completes_fast():
    laps = [p.data for p in run(DemoSimulator("quick_lap"), 20) if p.packet_id == PacketId.LAP_DATA]
    assert laps[0].current_lap_time_ms > 60000 and laps[-1].current_lap_num == 2


def test_interruption_scenario_drops_packets():
    sim = DemoSimulator("interruption")
    counts = []
    for _ in range(int((INTERRUPT_AT_S + INTERRUPT_FOR_S + 2) * 60)):
        n = len(sim.step())
        counts.append((sim.t, n))
    silent = [t for t, n in counts if n == 0]
    assert silent and min(silent) >= INTERRUPT_AT_S and max(silent) < INTERRUPT_AT_S + INTERRUPT_FOR_S
    assert counts[-1][1] > 0


def test_service_demo_mode_isolated_from_live(tmp_path, monkeypatch):
    import service as svc
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="demo", demo_speed=8, data_dir=str(tmp_path)), engine_factory=FakeEngine)
    app.start()
    try:
        app.start_demo()
        assert wait_until(lambda: app.state.snapshot().speed_kph is not None)
        snap = app.state.snapshot()
        assert snap.source == "demo" and "synthetic" in snap.track_name
        # a live packet arriving while in demo mode must be ignored
        live = decode_packet(pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, session_uid=1, frame=999999),
                                             CarTelemetryData(speed=1)))
        app.ingest(live, "live")
        assert app.state.snapshot().session_uid != 1
        assert not app.recording  # demo sessions are not saved unless explicitly enabled
        app.stop_demo()
        assert app.update_settings(mode="live", udp_port=_free_port()) == {}
        assert app.state.snapshot().speed_kph is None and app.state.snapshot().source == "live"
    finally:
        app.shutdown()


def test_demo_recording_is_labelled(tmp_path, monkeypatch):
    import service as svc
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="demo", demo_speed=8, record_demo=True, demo_scenario="quick_lap",
                                  data_dir=str(tmp_path)), engine_factory=FakeEngine)
    app.start()
    try:
        app.start_demo()
        assert wait_until(lambda: app.writer.laps_written >= 1, timeout=15)
    finally:
        app.shutdown()
    s = app.repo.list_sessions().iloc[0]
    assert s.source == "demo" and s.status == "complete" and s.laps >= 1


def _free_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_demo_telemetry_follows_the_lap_profile():
    """Pedals and steering must change around the lap (they once froze at the start-line values)."""
    from demo.simulator import DemoSimulator
    sim = DemoSimulator("standard")
    tel = [p.data for _ in range(60 * 60) for p in sim.step() if p.packet_id == PacketId.CAR_TELEMETRY]
    assert max(t.brake for t in tel) > 0.5 and min(t.throttle for t in tel) < 0.5
    assert max(abs(t.steer) for t in tel) > 0.2
