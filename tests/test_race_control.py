"""Race control log: the game's own event packets, named and filtered."""
from conftest import FakeEngine, wait_until

from analysis.race_control import RaceControl
from telemetry.packet_models import (
    CollisionDetails,
    EventData,
    FastestLapDetails,
    OvertakeDetails,
    PenaltyDetails,
    SafetyCarDetails,
    VehicleDetails,
)

NAMES = {0: "YOU", 3: "VERSTAPPEN", 7: "NORRIS"}


def rc_event(rc, code, details=None, t=100.0, lap=5):
    return rc.on_event(EventData(code, details), session_time=t, lap=lap, names=NAMES, player=0)


def test_penalty_for_another_driver_is_logged_not_spoken():
    rc = RaceControl()
    e = rc_event(rc, "PENA", PenaltyDetails(4, 7, 3, 255, 5, 5, 0))
    assert e.message == "VERSTAPPEN: 5 s time penalty, corner cutting, gained time"
    assert not e.involves_player and e.speech is None and rc.entries[-1] is e and e.lap == 5


def test_your_penalty_is_spoken():
    e = rc_event(RaceControl(), "PENA", PenaltyDetails(4, 17, 0, 255, 5, 5, 0))
    assert e.involves_player and e.message == "You: 5 s time penalty, pit lane speeding"
    assert e.speech == "5 second time penalty. Pit lane speeding."
    e = rc_event(RaceControl(), "PENA", PenaltyDetails(5, 7, 0, 255, 255, 5, 0))
    assert e.message == "You: warning, corner cutting, gained time" and e.speech == "Warning. Corner cutting, gained time."


def test_lap_invalidations_are_left_to_the_lap_call():
    rc = RaceControl()
    for kind in range(10, 16):
        assert rc_event(rc, "PENA", PenaltyDetails(kind, 26, 0, 255, 255, 5, 0)) is None
    assert rc_event(rc, "PENA", PenaltyDetails(3, 15, 3, 255, 255, 5, 0)) is None  # a reminder
    assert not rc.entries


def test_unknown_codes_are_named_by_number_not_guessed():
    e = rc_event(RaceControl(), "PENA", PenaltyDetails(4, 99, 3, 255, 10, 5, 0))
    assert e.message == "VERSTAPPEN: 10 s time penalty, infringement 99"


def test_overtakes_and_collisions_only_when_you_are_involved():
    rc = RaceControl()
    assert rc_event(rc, "OVTK", OvertakeDetails(3, 7)) is None
    assert rc_event(rc, "OVTK", OvertakeDetails(0, 7)).message == "You passed NORRIS"
    assert rc_event(rc, "OVTK", OvertakeDetails(3, 0)).message == "VERSTAPPEN passed you"
    assert rc_event(rc, "COLL", CollisionDetails(3, 7)) is None
    assert rc_event(rc, "COLL", CollisionDetails(7, 0)).message == "Contact with NORRIS"


def test_other_events():
    rc = RaceControl()
    assert rc_event(rc, "FTLP", FastestLapDetails(7, 81.234)).message == "Fastest lap: NORRIS 1:21.234"
    assert rc_event(rc, "RTMT", VehicleDetails(3)).message == "VERSTAPPEN retired"
    assert rc_event(rc, "RDFL").speech == "Red flag."
    assert rc_event(rc, "DTSV", VehicleDetails(7)).message == "NORRIS served a drive-through"
    assert rc_event(rc, "SCAR", SafetyCarDetails(1, 1)).message == "Safety car in this lap"
    assert rc_event(rc, "SCAR", SafetyCarDetails(1, 0)) is None  # deployment comes from the session status
    assert rc_event(rc, "BUTN") is None and rc_event(rc, "SPTP") is None
    assert rc_event(rc, "RTMT", VehicleDetails(15)).message == "Car 16 retired"  # no name yet


def test_safety_car_periods_from_the_session_status():
    rc = RaceControl()
    assert rc.on_safety_car(0, 10.0, 1) is None                     # first reading: nothing changed
    e = rc.on_safety_car(1, 20.0, 2)
    assert e.message == "Safety car deployed" and e.speech == "Safety car, safety car."
    assert rc.on_safety_car(1, 21.0, 2) is None
    assert rc.on_safety_car(0, 90.0, 4).message == "Safety car period over"
    assert rc.on_safety_car(2, 95.0, 4).speech == "Virtual safety car deployed."
    assert rc.on_safety_car(0, 99.0, 5).message == "Virtual safety car period over"


def test_service_logs_speaks_and_records_race_control(tmp_path, monkeypatch):
    import service as svc
    from config.settings import Settings
    from demo.simulator import DemoSimulator
    from storage.session_repository import SessionRepository
    from telemetry.packet_models import Packet, PacketId
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    engine = FakeEngine()
    app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path), record_demo=True, voice_min_interval_s=0),
                         engine_factory=lambda: engine)
    app.start()
    sim = DemoSimulator("quick_lap")
    while sim.lap < 2:
        for p in sim.step():
            app.ingest(p, "demo")
    hdr = sim.step()[0].header
    from dataclasses import replace
    pen = Packet(replace(hdr, packet_id=int(PacketId.EVENT), frame_identifier=hdr.frame_identifier + 1),
                 PacketId.EVENT, EventData("PENA", PenaltyDetails(4, 17, 0, 255, 5, 2, 0)))
    app.ingest(pen, "demo")
    assert app.race_control.entries[-1].message == "You: 5 s time penalty, pit lane speeding"
    assert wait_until(lambda: "5 second time penalty. Pit lane speeding." in engine.spoken)
    assert wait_until(lambda: app.writer.session_id(app.recording_key) is not None)  # the writer thread creates it
    sid = app.writer.session_id(app.recording_key)
    app.shutdown()
    assert "You: 5 s time penalty, pit lane speeding" in SessionRepository(app.repo.db_path).events(sid).message.tolist()


def test_a_failing_race_control_rule_never_escapes_ingest(tmp_path, monkeypatch):
    import service as svc
    from config.settings import Settings
    from demo.simulator import DemoSimulator
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path)), engine_factory=FakeEngine)

    def boom(*a, **k):
        raise RuntimeError("bad event")
    monkeypatch.setattr(app.race_control, "on_event", boom)
    monkeypatch.setattr(app.race_control, "on_safety_car", boom)
    sim = DemoSimulator("standard")
    for _ in range(120):
        for p in sim.step():
            app.ingest(p, "demo")  # must not raise
    assert app.state.snapshot().session_type == "Race"
    app.shutdown()


def test_penalties_carry_the_lap_the_game_gives():
    e = rc_event(RaceControl(), "PENA", PenaltyDetails(4, 7, 3, 255, 5, 9, 0), lap=12)
    assert e.lap == 9


def test_a_flashback_takes_back_what_race_control_said_after_the_rewind_point():
    rc = RaceControl()
    rc_event(rc, "FTLP", FastestLapDetails(7, 81.2), t=100.0)
    rc_event(rc, "PENA", PenaltyDetails(4, 7, 3, 255, 5, 5, 0), t=150.0)
    rc.on_safety_car(0, 90.0, 1)
    rc.on_safety_car(1, 160.0, 5)
    rc.rewind(120.0)
    assert [e.code for e in rc.entries] == ["FTLP"]
    assert rc.on_safety_car(1, 165.0, 5).message == "Safety car deployed"  # said again when it happens again


def test_service_rewinds_race_control_on_a_flashback(tmp_path, monkeypatch):
    from dataclasses import replace

    import service as svc
    from config.settings import Settings
    from demo.simulator import DemoSimulator
    from storage.session_repository import SessionRepository
    from telemetry.packet_models import FlashbackDetails, Packet, PacketId
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="demo", data_dir=str(tmp_path), record_demo=True), engine_factory=FakeEngine)
    app.start()
    sim = DemoSimulator("quick_lap")
    while sim.lap < 2:
        for p in sim.step():
            app.ingest(p, "demo")
    hdr = sim.step()[0].header
    t = hdr.session_time

    def event(code, details, dt, frame):
        h = replace(hdr, packet_id=int(PacketId.EVENT), session_time=t + dt, frame_identifier=hdr.frame_identifier + frame)
        app.ingest(Packet(h, PacketId.EVENT, EventData(code, details)), "demo")
    event("PENA", PenaltyDetails(4, 7, 2, 255, 5, 2, 0), 5.0, 1)
    assert any(e.code == "PENA" for e in app.race_control.entries)
    event("FLBK", FlashbackDetails(hdr.frame_identifier, t + 1.0), 6.0, 2)
    assert not any(e.code == "PENA" for e in app.race_control.entries)
    assert wait_until(lambda: app.writer.session_id(app.recording_key) is not None)
    sid = app.writer.session_id(app.recording_key)
    app.shutdown()
    assert "PENA" not in SessionRepository(app.repo.db_path).events(sid).code.tolist()
