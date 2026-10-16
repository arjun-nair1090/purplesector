"""Race gaps, fuel/tyre strategy, track map and corners, radio on request, lap import, overlay."""
import json
import socket
import urllib.request
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from conftest import FakeEngine, wait_until

from analysis.coach import rule_segment_loss
from analysis.gaps import GapTracker
from analysis.lap_tracker import CompletedLap
from analysis.strategy import LapResources, projection, race_laps_remaining
from analysis.track_map import Corner, TrackMap, corners_in, detect_corners, plane_axes
from config.settings import Settings
from demo.simulator import GEOMETRY, TRACK_LENGTH, DemoSimulator
from storage.database import init_db
from storage.session_repository import SessionRepository
from telemetry.packet_models import ButtonDetails, EventData, LapData, ParticipantData, udp_action_bit


def car(pos, total, active=True):
    return LapData(car_position=pos, total_distance=total, result_status=2 if active else 1)


def cars(*items):
    return tuple(items) + (LapData(),) * (22 - len(items))


# ---------------- gaps ----------------

def drive(gt, seconds, speeds, starts, hz=60):
    """Cars at constant speed (m/s); returns the final time."""
    t = 0.0
    for k in range(int(seconds * hz)):
        t = k / hz
        totals = [s + v * t for s, v in zip(starts, speeds, strict=True)]
        order = sorted(range(len(totals)), key=lambda i: -totals[i])
        pos = {i: p + 1 for p, i in enumerate(order)}
        gt.on_lap_data(t, cars(*(car(pos[i], d) for i, d in enumerate(totals))))
    return t


def test_gap_is_time_since_leader_passed_followers_distance():
    gt = GapTracker()
    # player (0) at 50 m/s; car 1 starts 100 m ahead at the same speed = 2.0 s ahead; car 2 is 1.5 s behind
    drive(gt, 10, speeds=(50, 50, 50), starts=(0, 100, -75))
    v = gt.view(0)
    assert v["ahead"].car_index == 1 and v["ahead"].gap_s == pytest.approx(2.0, abs=0.02)
    assert v["behind"].car_index == 2 and v["behind"].gap_s == pytest.approx(1.5, abs=0.02)
    assert v["ahead"].position == 1 and v["behind"].position == 3


def test_gap_unknown_until_leader_was_seen_at_that_distance():
    gt = GapTracker()
    drive(gt, 1, speeds=(50, 50), starts=(0, 100))  # the leader was first seen 100 m up the road
    assert gt.view(0)["ahead"].gap_s is None


def test_gap_trend_between_line_crossings():
    gt = GapTracker()
    hz = 60
    for k in range(int(30 * hz)):
        t = k / hz
        gt.on_lap_data(t, cars(car(2, 50 * t), car(1, 100 + 51 * t)))  # leader 1 m/s faster: pulls away
        if k in (10 * hz, 20 * hz):
            gt.on_player_lap(0)
    g = gt.view(0)["ahead"]
    assert g.change_per_lap_s > 0.15  # gap grew over the last "lap"


def test_flashback_rewinds_traces():
    gt = GapTracker()
    drive(gt, 10, speeds=(50, 50), starts=(0, 100))
    gt.on_lap_data(4.0, cars(car(2, 200.0), car(1, 300.0)))  # rewound to t=4
    assert gt.view(0)["ahead"].gap_s == pytest.approx(2.0, abs=0.05)
    assert all(t <= 4.0 for _, times in gt._traces.values() for t in times)


def test_inactive_cars_are_not_neighbours_and_names_fall_back():
    gt = GapTracker()
    gt.set_names([ParticipantData(name=b"ME"), ParticipantData(name=b"", race_number=44)])
    gt.on_lap_data(0.0, cars(car(2, 0.0), car(1, 50.0), car(3, -10.0, active=False)))
    v = gt.view(0)
    assert v["ahead"].name == "Car #44" and v["behind"] is None


def test_demo_all_car_packets_survive_the_wire():
    """Simulator packets encoded with the spec layouts and decoded again keep every car."""
    from telemetry import packet_builder as pb
    from telemetry.decoder import decode_packet
    from telemetry.packet_models import PacketId
    seen = set()
    for p in DemoSimulator().step():
        if p.cars is None:
            continue
        prefix = bytes([p.num_active_cars]) if p.packet_id == PacketId.PARTICIPANTS else b""
        got = decode_packet(pb.build_cars(p.header, dict(enumerate(p.cars)), prefix=prefix))
        seen.add(p.packet_id)
        assert got.num_active_cars == p.num_active_cars
        for a, b in zip(got.cars[:4], p.cars[:4], strict=True):
            if p.packet_id == PacketId.MOTION:
                assert a.position == pytest.approx(b.position, abs=1e-3)  # float32 on the wire
            elif p.packet_id == PacketId.LAP_DATA:
                assert (a.car_position, a.result_status) == (b.car_position, b.result_status)
                assert a.total_distance == pytest.approx(b.total_distance, abs=1e-3)
            else:
                assert a.name == b.name.ljust(48, bytes(1))
    assert seen == {PacketId.MOTION, PacketId.LAP_DATA, PacketId.PARTICIPANTS}


def test_demo_race_ends_at_the_flag_and_restarts(monkeypatch):
    import demo.simulator as ds
    monkeypatch.setattr(ds, "RACE_LAPS", 1)
    sim = DemoSimulator("quick_lap")
    first_uid, codes = sim.session_uid, []
    for _ in range(60 * 20):
        for p in sim.step():
            if p.packet_id.name == "EVENT":
                codes.append((p.header.session_uid, p.data.code))
    assert (first_uid, "CHQF") in codes and (first_uid, "SEND") in codes
    assert sim.session_uid != first_uid and ds.is_synthetic_uid(sim.session_uid)
    assert (sim.session_uid, "SSTA") in codes and sim.lap == 1


# ---------------- fuel / tyres ----------------

def test_lap_resources_measure_between_line_crossings():
    r = LapResources()
    assert r.line_crossed(50.0, (1.0, 1.0, 1.0, 1.0)) == (None, None)  # first crossing: nothing to compare
    assert r.line_crossed(48.2, (2.0, 2.4, 1.5, 1.6)) == (pytest.approx(1.8), pytest.approx(1.4))
    assert r.line_crossed(49.0, (0.1, 0.1, 0.1, 0.1)) == (None, None)  # fuel rose (flashback), new tyres


def mk(n, fuel=None, wear=None, pit=False):
    return CompletedLap(n, 90000, 30000, 30000, 30000, valid=True, samples_complete=True, pit_lap=pit, out_lap=False,
                        fuel_used_kg=fuel, tyre_wear_pct=wear)


def test_projection_from_recent_measured_laps():
    snap = SimpleNamespace(fuel_kg=20.0, total_laps=30, lap_number=21, lap_distance_m=2150.0, track_length_m=4300)
    laps = [mk(1, 5.0), mk(2, 2.0, 1.5), mk(3, 2.0, 1.5), mk(4, 1.0, 0.5, pit=True), mk(5, 2.0, 1.5)]
    p = projection(laps, snap)
    assert p["fuel_per_lap_kg"] == pytest.approx(2.0) and p["fuel_laps_measured"] == 3  # pit lap left out
    assert p["race_laps_left"] == pytest.approx(9.5)
    assert p["fuel_laps_left"] == pytest.approx(10.0) and p["fuel_margin_laps"] == pytest.approx(0.5)
    assert p["wear_per_lap_pct"] == pytest.approx(1.5)


def test_projection_without_measurements_is_empty_not_invented():
    snap = SimpleNamespace(fuel_kg=20.0, total_laps=0, lap_number=3, lap_distance_m=10.0, track_length_m=4300)
    p = projection([mk(1)], snap)
    assert p["fuel_per_lap_kg"] is None and p["fuel_margin_laps"] is None and race_laps_remaining(snap) is None


# ---------------- track map / corners ----------------

def speed_lap(dips=((800, 90), (1700, 150), (2600, 200)), length=3200, hz=60):
    """Samples over distance with speed dipping to each (apex m, km/h)."""
    rows, d, t = [], 0.0, 0.0
    while d < length:
        v = 300.0
        for apex, low in dips:
            v = min(v, low + abs(d - apex) * 0.5)
        rows.append((t, int(t * 1000), d, v, 100.0, 0.0, 0.0, 6, 10000))
        t += 1 / hz
        d += v / 3.6 / hz
    return pd.DataFrame(rows, columns=["session_time", "lap_ms", "lap_distance", "speed", "throttle", "brake",
                                       "steer", "gear", "rpm"])


def test_corners_detected_at_speed_minima_in_order():
    cs = detect_corners(speed_lap())
    assert [c.number for c in cs] == [1, 2, 3]
    assert [round(c.apex_m, -1) for c in cs] == [800, 1700, 2600]
    assert cs[0].min_speed_kph == pytest.approx(90, abs=3)


def test_gentle_lifts_are_not_corners():
    assert detect_corners(speed_lap(dips=((800, 292),))) == []


def test_corner_names_for_a_segment():
    cs = [Corner(1, 800, 90), Corner(2, 950, 120), Corner(3, 2000, 150)]
    assert corners_in(cs, 600, 800) is None and corners_in(cs, 800, 1000) == "Corners 1–2"
    assert corners_in(cs, 1800, 2000) is None and corners_in(cs, 1900, 2100) == "Corner 3"


def test_plane_uses_the_two_widest_axes():
    xyz = np.array([[0, 0, 0], [500, 2, 10], [900, -3, 700], [100, 1, 800]], float)
    assert plane_axes(xyz) == (0, 2)


def demo_lap_frame():
    df = speed_lap(dips=())
    df = df[df.lap_distance < TRACK_LENGTH]
    pos = GEOMETRY[df.lap_distance.astype(int).to_numpy() % TRACK_LENGTH]
    return df.assign(pos_x=pos[:, 0], pos_y=pos[:, 1], pos_z=pos[:, 2])


def test_track_map_from_positions():
    m = TrackMap.from_samples(demo_lap_frame())
    assert m.axes == (0, 2)  # the demo's synthetic elevation is y
    assert m.point_at(1000.0) == pytest.approx((GEOMETRY[1000][0], GEOMETRY[1000][2]), abs=1.0)
    assert TrackMap.from_samples(speed_lap()) is None  # recorded before positions existed


def test_segment_loss_names_the_corner():
    def lap(n, slow_at):
        rows, t, d = [], 0.0, 0.0
        while d < 3000:
            v = 40.0 if slow_at <= d < slow_at + 100 else 60.0
            rows.append((t, int(t * 1000), d, v * 3.6, 100.0, 0.0, 0.0, 6, 10000))
            t, d = t + 1 / 60, d + v / 60
        return CompletedLap(n, int(t * 1000), None, None, None, valid=True, samples_complete=True, pit_lap=False,
                            out_lap=False, samples=rows)
    best, cur = lap(1, -999), lap(2, 1250)
    e = rule_segment_loss(cur, best, 0.15, [Corner(4, 1290.0, 140.0)])
    assert "Corner 4" in e.message and "at Corner 4" in e.spoken
    assert "Corner" not in rule_segment_loss(cur, best, 0.15).message


# ---------------- import ----------------

def own_lap(n, ms, positions=True):
    rows = [(i / 60, int(i * 1000 / 60), i * 1.0, 200.0, 100.0, 0.0, 0.0, 7, 11000)
            + ((float(i), 0.0, float(-i)) if positions else ()) for i in range(200)]
    return CompletedLap(n, ms, 30000, 30000, ms - 60000, valid=True, samples_complete=True, pit_lap=False,
                        out_lap=False, samples=rows, fuel_used_kg=1.7)


@pytest.fixture
def repo(tmp_path):
    init_db(tmp_path / "x.db")
    return SessionRepository(tmp_path / "x.db")


def test_export_import_round_trip_is_never_your_best(repo, tmp_path):
    friend = repo.create_session(1, "live", "Spa")
    repo.save_lap(friend, own_lap(1, 104000), own_lap(1, 104000).samples)
    repo.save_lap(friend, own_lap(2, 103500, positions=False), own_lap(2, 103500, positions=False).samples)
    doc = json.loads(repo.export_json(friend, tmp_path).read_text(encoding="utf-8"))
    repo.delete_session(friend)
    mine = repo.create_session(2, "live", "Spa")
    repo.save_lap(mine, own_lap(1, 106000), own_lap(1, 106000).samples)

    sid = repo.import_session(doc, "  Fast   Friend ")
    s = repo.get_session(sid)
    assert s["imported"] == 1 and s["driver_name"] == "Fast Friend" and s["track_name"] == "Spa"
    assert repo.best_lap_ms("Spa", "live") == 106000          # the friend's 1:43.5 is not your PB
    assert repo.progress("Spa", "live").best_lap_ms.tolist() == [106000]
    refs = repo.reference_laps("Spa")
    assert refs.lap_time_ms.tolist()[0] == 103500 and refs.driver_name.iloc[0] == "Fast Friend"
    samples = repo.lap_samples(int(refs.id.iloc[0]))
    assert len(samples) == 200 and samples.pos_x.isna().all()  # lap 2 had no positions
    assert repo.laps(sid).fuel_used_kg.tolist() == [1.7, 1.7]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(format="something-else"), "Not a PurpleSector"),
    (lambda d: d["session"].update(track_name=""), "no track name"),
    (lambda d: d["session"].update(source="live'); DROP TABLE laps;--"), "source"),
    (lambda d: d["laps"][0].update(lap_time_ms=-5), "lap"),
    (lambda d: d["laps"][0].update(lap_time_ms="fast"), "lap"),
    (lambda d: d["laps"].append(dict(d["laps"][0])), "duplicate"),
    (lambda d: d["samples"]["rows"][0].__setitem__(0, 99), "belongs to no lap"),
    (lambda d: d["samples"]["rows"][0].__setitem__(4, float("inf")), "samples"),
    (lambda d: d["samples"].update(columns=["lap_number", "evil"]), "columns"),
])
def test_import_rejects_bad_files(repo, tmp_path, mutate, message):
    sid = repo.create_session(1, "live", "Spa")
    repo.save_lap(sid, own_lap(1, 104000), own_lap(1, 104000).samples)
    doc = json.loads(repo.export_json(sid, tmp_path).read_text(encoding="utf-8"))
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        repo.import_session(doc, "x")
    assert len(repo.list_sessions()) == 1  # nothing half-written


# ---------------- service: demo race end to end ----------------

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def race(tmp_path_factory):
    import service as svc
    svc_save, svc.save_settings = svc.save_settings, (lambda s: s.validate())
    tmp = tmp_path_factory.mktemp("race")
    engine = FakeEngine()
    app = svc.AppService(Settings(mode="demo", data_dir=str(tmp), radio_button=2, overlay_port=free_port(),
                                  coach_cooldown_s=0, voice_min_interval_s=0),
                         engine_factory=lambda: engine)
    app.start()
    sim = DemoSimulator("quick_lap")
    while sim.lap < 3 or sim.lap_ms < 3000:  # lap 1 (partial) and lap 2 complete
        for p in sim.step():
            app.ingest(p, "demo")
    yield app, sim, engine
    app.shutdown()
    svc.save_settings = svc_save


def test_demo_race_has_gaps_to_both_neighbours(race):
    app, _, _ = race
    g = app.gaps_view()
    assert g["race"] and g["ahead"] and g["behind"]
    assert 0 < g["ahead"].gap_s < 10 and 0 < g["behind"].gap_s < 10
    assert g["ahead"].name in ("M. DUVAL", "T. OKAFOR", "L. BRANDT")
    assert g["ahead"].change_per_lap_s is not None  # two line crossings seen


def test_demo_race_strategy_is_measured(race):
    app, sim, _ = race
    s = app.strategy_view()
    lap2 = next(lap for lap in app.tracker.laps if lap.lap_number == 2)
    assert lap2.fuel_used_kg == pytest.approx(1.72, abs=0.1) and lap2.tyre_wear_pct > 1
    assert app.tracker.laps[0].fuel_used_kg is None  # joined mid-lap: never measured
    assert s["fuel_source"] == "measured" and s["fuel_margin"] is not None
    assert (s["pit_ideal"], s["pit_latest"], s["pit_rejoin"]) == (14, 19, 4)


def test_demo_race_map_has_every_car(race):
    app, _, _ = race
    v = app.map_view()
    assert v["map"] is not None and v["source"] == "lap 2"
    roles = sorted(r for _, _, r in v["dots"])
    assert roles == ["ahead", "behind", "other", "player"]


def test_radio_check_and_wheel_button(race):
    app, _, engine = race
    n = len(engine.spoken)
    assert app.radio_check()
    assert wait_until(lambda: len(engine.spoken) > n)
    assert "Gap ahead" in engine.spoken[-1] and "Fuel" in engine.spoken[-1]
    assert app.coach.events[-1].message.startswith("Radio:")

    from telemetry.packet_models import Packet, PacketHeader, PacketId
    hdr = PacketHeader(packet_format=2022, packet_id=3, session_uid=app.session_uid, frame_identifier=1)

    def press(bits):
        app.ingest(Packet(hdr, PacketId.EVENT, EventData("BUTN", ButtonDetails(bits))), "demo")
    before = app._radio_n
    press(udp_action_bit(1))                         # a different UDP action: ignored
    press(udp_action_bit(2))                         # pressed: one call
    press(udp_action_bit(2) | 0x0001)                # still held: no repeat
    press(0)
    press(udp_action_bit(2))                         # pressed again
    assert app._radio_n == before + 2


def test_overlay_endpoint_serves_live_state(race):
    app, _, _ = race
    url = f"http://127.0.0.1:{app.settings.overlay_port}/overlay.json"
    with urllib.request.urlopen(url, timeout=2) as r:
        d = json.loads(r.read())
    assert d["source"] == "demo" and d["gear"] and d["ahead"]["position"] >= 1
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(url.replace("overlay.json", "settings"), timeout=2)


def test_new_settings_are_validated():
    assert "radio_button" in Settings(radio_button=13).validate()
    assert "overlay_port" in Settings(overlay_port=20777).validate()  # clashes with the telemetry port
    assert not {"radio_button", "overlay_port"} & set(Settings(radio_button=12, overlay_port=8777).validate())


# ---------------- review fixes ----------------

def test_no_neighbours_until_the_player_has_a_position():
    gt = GapTracker()
    gt.on_lap_data(0.0, cars(car(0, 0.0), car(1, 50.0), car(2, -10.0)))  # player's position not set yet
    assert gt.neighbours(0) == (None, None)


def test_overlay_port_cannot_be_shared_by_two_apps():
    import overlay
    port = free_port()
    first = overlay.serve(port, dict)
    try:
        assert first is not None
        assert overlay.serve(port, dict) is None  # Windows SO_REUSEADDR would let this bind silently
    finally:
        first.shutdown()
        first.server_close()


def _flashback_service(tmp_path, monkeypatch):
    import service as svc
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    return svc.AppService(Settings(mode="demo", data_dir=str(tmp_path)), engine_factory=FakeEngine)


def test_flashback_across_the_line_does_not_fake_fuel_use(tmp_path, monkeypatch):
    from telemetry.packet_models import CarDamageData, CarStatusData, Packet, PacketHeader, PacketId

    app = _flashback_service(tmp_path, monkeypatch)
    t = [0.0]

    def send(pid, data, cars_=None):
        t[0] += 0.02
        hdr = PacketHeader(packet_format=2022, packet_id=int(pid), session_uid=0xDE40_0000_0042, session_time=t[0],
                           frame_identifier=int(t[0] * 1000))
        app.ingest(Packet(hdr, pid, data, cars_), "demo")

    def lap_data(num, ms, last=0):
        d = LapData(last_lap_time_ms=last, current_lap_time_ms=ms, current_lap_num=num, car_position=1,
                    result_status=2, driver_status=1)
        send(PacketId.LAP_DATA, d, cars(d))

    def fuel(kg):
        send(PacketId.CAR_STATUS, CarStatusData(fuel_in_tank=kg))
        send(PacketId.CAR_DAMAGE, CarDamageData(tyres_wear=(1.0, 1.0, 1.0, 1.0)))

    fuel(50.0)
    lap_data(1, 0)
    lap_data(2, 10, last=90000)          # line 1: measuring starts
    fuel(48.0)
    lap_data(3, 10, last=90000)          # lap 2 used 2.0 kg
    assert app.tracker.laps[-1].fuel_used_kg == pytest.approx(2.0)
    fuel(49.9)                           # flashback back into lap 2 restores fuel
    lap_data(2, 89000)
    fuel(48.1)
    lap_data(3, 10, last=89900)          # lap 2 re-driven: only 1.8 kg since the rewind point, not a full lap
    assert [lp.lap_number for lp in app.tracker.laps] == [1, 2]
    assert app.tracker.laps[-1].fuel_used_kg is None
    app.shutdown()


def test_track_map_is_never_drawn_from_a_pit_lap(tmp_path, monkeypatch):
    app = _flashback_service(tmp_path, monkeypatch)
    df = demo_lap_frame()
    rows = list(df.itertuples(index=False, name=None))
    pit = CompletedLap(1, 90000, None, None, None, valid=True, samples_complete=True, pit_lap=True, out_lap=False,
                       samples=rows)
    app._update_pace(pit)
    assert app.track_map is None
    clean = CompletedLap(2, 90000, None, None, None, valid=True, samples_complete=True, pit_lap=False,
                         out_lap=False, samples=rows)
    app._update_pace(clean)
    assert app.track_map is not None and app.track_map_source == "lap 2"
    app.shutdown()


def test_laps_undone_by_a_flashback_are_dropped_from_the_recording(tmp_path, monkeypatch):
    """Otherwise a lap the game took back could become a "PB (recorded)" next session."""
    from storage.session_repository import SessionRepository
    app = _flashback_service(tmp_path, monkeypatch)
    app.settings.record_demo = True
    app.writer.start()
    sim = DemoSimulator("quick_lap")
    while sim.lap < 3:
        for p in sim.step():
            app.ingest(p, "demo")
    sid = app.writer.session_id(app.recording_key)
    repo = SessionRepository(app.repo.db_path)
    assert wait_until(lambda: repo.laps(sid).lap_number.tolist() == [1, 2])
    from telemetry.packet_models import Packet, PacketId
    last = next(p for p in sim.step() if p.packet_id == PacketId.LAP_DATA)
    rewound = LapData(**{**{f: getattr(last.data, f) for f in last.data.__dataclass_fields__},
                         "current_lap_num": 2, "current_lap_time_ms": 80000})
    app.ingest(Packet(last.header, PacketId.LAP_DATA, rewound, cars(rewound)), "demo")
    assert wait_until(lambda: repo.laps(sid).lap_number.tolist() == [1])
    app.shutdown()
