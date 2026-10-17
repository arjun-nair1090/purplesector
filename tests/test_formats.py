"""F1 23 / F1 24 / F1 25 UDP formats (2023, 2024, 2025) next to F1 22's 2022.

Sizes are checked against the totals EA publishes for each year; hand-built packets are
packed with struct directly from the spec's field order, independently of the layouts.
"""
import struct

import pytest

from telemetry import packet_builder as pb
from telemetry.decoder import IgnoredPacketError, MalformedPacketError, UnsupportedFormatError, decode_packet
from telemetry.formats import FORMATS
from telemetry.packet_models import (
    CarDamageData,
    CarSetupData,
    CarStatusData,
    CollisionDetails,
    LapData,
    LapHistoryData,
    OvertakeDetails,
    PacketId,
    ParticipantData,
    PenaltyDetails,
    SafetyCarDetails,
    SessionData,
    SessionHistoryHeader,
    VehicleDetails,
    WeatherForecastSample,
)

SPEC_SIZES = {  # bytes per packet id, from each year's published specification
    2022: [1464, 632, 972, 40, 1257, 1102, 1347, 1058, 1015, 1191, 948, 1155],
    2023: [1349, 644, 1131, 45, 1306, 1107, 1352, 1239, 1020, 1218, 953, 1460, 231, 217],
    2024: [1349, 753, 1285, 45, 1350, 1133, 1352, 1239, 1020, 1306, 953, 1460, 231, 237, 101],
    2025: [1349, 753, 1285, 45, 1284, 1133, 1352, 1239, 1042, 954, 1041, 1460, 231, 273, 101, 1131],
}
YEARS = tuple(SPEC_SIZES)


def test_every_year_is_supported():
    assert set(FORMATS) == set(YEARS)


@pytest.mark.parametrize("year", YEARS)
def test_sizes_match_each_years_spec(year):
    f = FORMATS[year]
    assert [f.sizes[PacketId(i)] for i in range(len(SPEC_SIZES[year]))] == SPEC_SIZES[year]
    h = f.header_size
    assert h == (24 if year == 2022 else 29)
    # the parts PurpleSector decodes add up to the published totals
    assert h + 22 * f.layouts[LapData].size + 2 == f.sizes[PacketId.LAP_DATA]
    assert h + 22 * f.layouts[CarStatusData].size == f.sizes[PacketId.CAR_STATUS]
    assert h + 22 * f.layouts[CarDamageData].size == f.sizes[PacketId.CAR_DAMAGE]
    assert h + 1 + 22 * f.layouts[ParticipantData].size == f.sizes[PacketId.PARTICIPANTS]
    assert h + 22 * f.layouts[CarSetupData].size + (4 if year >= 2024 else 0) == f.sizes[PacketId.CAR_SETUPS]
    assert h + 7 + 100 * f.layouts[LapHistoryData].size + 8 * 3 == f.sizes[PacketId.SESSION_HISTORY]
    assert h + 4 + 12 == f.sizes[PacketId.EVENT]
    # session: pit window after the forecast block; what follows is assists, units, rules
    assert f.session_pit_window_offset == {2022: 613, 2023: 618, 2024: 682, 2025: 682}[year]
    assert f.sizes[PacketId.SESSION] - f.session_pit_window_offset - 3 == {2022: 16, 2023: 23, 2024: 68, 2025: 68}[year]


def _header_2024(pid, player=0, fmt=2024):
    return struct.pack("<HBBBBBQfIIBB", fmt, fmt % 100, 1, 2, 1, pid, 99, 10.0, 5, 5, player, 255)


def test_f1_24_lap_data_hand_built_with_minute_parts():
    player = 3
    buf = bytearray(1285)
    buf[:29] = _header_2024(PacketId.LAP_DATA, player)
    o = 29 + player * 57
    # last, current, S1 ms+min, S2 ms+min, delta front ms+min, delta leader ms+min, lap dist, total, SC delta
    struct.pack_into("<IIHBHBHBHBfff", buf, o, 92_100, 70_000, 2_345, 1, 0, 0, 812, 0, 4_400, 1, 3_200.5, 9_000.0, 0.0)
    # position, lap, pit status, stops, sector, invalid, penalties, warnings, corner cutting, DT, SG, grid, status, result
    struct.pack_into("<14B", buf, o + 32, 5, 3, 0, 0, 1, 0, 0, 2, 1, 0, 0, 8, 1, 2)
    p = decode_packet(bytes(buf))
    d = p.data
    assert p.header.packet_format == 2024 and p.header.game_year == 24 and p.header.player_car_index == player
    assert d.last_lap_time_ms == 92_100 and d.current_lap_time_ms == 70_000
    assert d.sector1_time_ms == 62_345  # 1:02.345, not 2.345
    assert d.sector2_time_ms == 0
    assert d.delta_to_car_in_front_ms == 812 and d.delta_to_race_leader_ms == 64_400
    assert d.lap_distance == pytest.approx(3_200.5)
    assert (d.car_position, d.current_lap_num, d.sector, d.warnings, d.grid_position, d.driver_status,
            d.result_status) == (5, 3, 1, 2, 8, 1, 2)


def test_f1_23_lap_data_sector_minutes_and_deltas():
    buf = bytearray(1131)
    buf[:29] = _header_2024(PacketId.LAP_DATA, fmt=2023)
    struct.pack_into("<IIHBHBHHfff", buf, 29, 0, 61_000, 1_000, 1, 59_000, 0, 1_234, 9_876, 10.0, 10.0, 0.0)
    d = decode_packet(bytes(buf)).data
    assert d.sector1_time_ms == 61_000 and d.sector2_time_ms == 59_000
    assert d.delta_to_car_in_front_ms == 1_234 and d.delta_to_race_leader_ms == 9_876


def test_f1_22_has_no_game_deltas():
    d = decode_packet(pb.build_packet(pb.header(PacketId.LAP_DATA), LapData(lap_distance=5.0))).data
    assert d.delta_to_car_in_front_ms is None and d.delta_to_race_leader_ms is None


@pytest.mark.parametrize("year", YEARS)
def test_round_trip_every_decoded_type(year):
    hdr = pb.header(PacketId.LAP_DATA, fmt=year, player=2)
    d = decode_packet(pb.build_packet(hdr, LapData(sector1_time_ms=65_432, sector2_time_ms=31_000,
                                                   current_lap_num=4, car_position=7))).data
    assert (d.sector1_time_ms, d.sector2_time_ms, d.current_lap_num, d.car_position) == (65_432, 31_000, 4, 7) \
        if year >= 2023 else (d.sector1_time_ms, d.current_lap_num) == (65_432, 4)
    d = decode_packet(pb.build_packet(pb.header(PacketId.CAR_STATUS, fmt=year), CarStatusData(
        fuel_in_tank=21.5, ers_store_energy=2.5e6, vehicle_fia_flags=3))).data
    assert (d.fuel_in_tank, d.ers_store_energy, d.vehicle_fia_flags) == (21.5, 2.5e6, 3)
    d = decode_packet(pb.build_packet(pb.header(PacketId.CAR_DAMAGE, fmt=year), CarDamageData(
        tyres_wear=(1.5, 2.5, 3.5, 4.5), rear_wing_damage=12, engine_seized=1))).data
    assert d.tyres_wear == (1.5, 2.5, 3.5, 4.5) and d.rear_wing_damage == 12 and d.engine_seized == 1
    p = decode_packet(pb.build_cars(pb.header(PacketId.PARTICIPANTS, fmt=year),
                                    {0: ParticipantData(name=b"ME", team_id=2), 5: ParticipantData(name=b"RIVAL")},
                                    prefix=bytes([20])))
    assert p.num_active_cars == 20 and p.cars[5].name.rstrip(b"\0") == b"RIVAL" and p.data.team_id == 2
    d = decode_packet(pb.build_packet(pb.header(PacketId.CAR_SETUPS, fmt=year), CarSetupData(
        front_wing=7, rear_wing=4, brake_bias=56, front_left_tyre_pressure=23.5, fuel_load=12.0,
        engine_braking=60))).data
    assert (d.front_wing, d.rear_wing, d.brake_bias, d.front_left_tyre_pressure, d.fuel_load) == (7, 4, 56, 23.5, 12.0)
    assert d.engine_braking == (60 if year >= 2024 else None)
    laps = [LapHistoryData(95_000, 61_500, 20_000, 13_500, 0x0F)]
    d = decode_packet(pb.build_history(pb.header(PacketId.SESSION_HISTORY, fmt=year),
                                       SessionHistoryHeader(car_idx=4, num_laps=1, best_lap_time_lap_num=1), laps)).data
    assert d.header.car_idx == 4 and d.laps[0].sector1_time_ms == 61_500 and d.laps[0].lap_time_ms == 95_000


@pytest.mark.parametrize("year", YEARS)
def test_session_forecast_and_names(year):
    race = 15 if year >= 2024 else 10
    samples = (WeatherForecastSample(race, 0, 1, 30, 0, 22, 0, 10),
               WeatherForecastSample(race, 10, 3, 27, 1, 20, 1, 60))
    s = SessionData(session_type=race, track_id=31, safety_car_status=1, pit_window_ideal_lap=12,
                    forecast=samples, forecast_accuracy=1)
    d = decode_packet(pb.build_session(pb.header(PacketId.SESSION, fmt=year), s)).data
    assert d.session_type_name == "Race" and d.safety_car_status == 1 and d.pit_window_ideal_lap == 12
    assert d.forecast == samples and d.forecast_accuracy == 1


def test_f1_24_session_type_numbering():
    for code, name in ((10, "Sprint Shootout 1"), (15, "Race"), (18, "Time Trial")):
        d = decode_packet(pb.build_session(pb.header(PacketId.SESSION, fmt=2024), SessionData(session_type=code))).data
        assert d.session_type_name == name
    d = decode_packet(pb.build_session(pb.header(PacketId.SESSION, fmt=2023), SessionData(session_type=10))).data
    assert d.session_type_name == "Race"


def test_forecast_count_is_bounded_by_the_packet():
    raw = bytearray(pb.build_session(pb.header(PacketId.SESSION), SessionData()))
    raw[FORMATS[2022].session_safety_car_offset + 2] = 255  # claims more samples than fit
    assert len(decode_packet(bytes(raw)).data.forecast) == 56


@pytest.mark.parametrize("year", YEARS)
def test_race_control_event_details(year):
    def ev(code, details):
        return decode_packet(pb.build_event(pb.header(PacketId.EVENT, fmt=year), code, details)).data

    d = ev("PENA", PenaltyDetails(penalty_type=4, infringement_type=7, vehicle_idx=3, other_vehicle_idx=255, time=5,
                                  lap_num=9, places_gained=0))
    assert (d.details.penalty_type, d.details.infringement_type, d.details.vehicle_idx, d.details.time,
            d.details.lap_num) == (4, 7, 3, 5, 9)
    assert ev("RTMT", VehicleDetails(6)).details.vehicle_idx == 6
    assert ev("RCWN", VehicleDetails(1)).details.vehicle_idx == 1
    assert ev("OVTK", OvertakeDetails(2, 8)).details == OvertakeDetails(2, 8)
    assert ev("SCAR", SafetyCarDetails(2, 0)).details == SafetyCarDetails(2, 0)
    assert ev("COLL", CollisionDetails(0, 4)).details == CollisionDetails(0, 4)


@pytest.mark.parametrize("year", (2023, 2024, 2025))
def test_newer_packet_types_are_ignored(year):
    for pid in range(12, len(SPEC_SIZES[year])):
        with pytest.raises(IgnoredPacketError):
            decode_packet(pb.build_packet(pb.header(PacketId(pid), fmt=year)))


def test_newer_packet_ids_are_malformed_in_2022():
    raw = bytearray(pb.build_packet(pb.header(PacketId.CAR_TELEMETRY)))
    raw[5] = 13
    with pytest.raises(MalformedPacketError):
        decode_packet(bytes(raw))


def test_older_formats_stay_unsupported():
    with pytest.raises(UnsupportedFormatError):
        decode_packet(pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, fmt=2021)))


@pytest.mark.parametrize("year", YEARS)
def test_truncated_per_year(year):
    raw = pb.build_packet(pb.header(PacketId.LAP_DATA, fmt=year))
    assert len(raw) == SPEC_SIZES[year][PacketId.LAP_DATA]
    with pytest.raises(Exception, match="expected"):
        decode_packet(raw[:-1])


def test_f1_24_race_switches_on_race_features(tmp_path, monkeypatch):
    """Session type 15 is a race in F1 24: gaps must appear, as they do for type 10 in F1 22."""
    from conftest import FakeEngine

    import service as svc
    from config.settings import Settings
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    app = svc.AppService(Settings(mode="live", data_dir=str(tmp_path)), engine_factory=FakeEngine)
    hdr = pb.header(PacketId.SESSION, fmt=2024, session_uid=77, frame=1)
    app.ingest(decode_packet(pb.build_session(hdr, SessionData(session_type=15, track_id=31))), "live")
    snap = app.state.snapshot()
    assert snap.session_type == "Race" and snap.track_name == "Las Vegas"
    assert app.gaps_view()["race"] is True
    app.shutdown()
