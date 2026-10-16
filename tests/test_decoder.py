import struct

import pytest

from telemetry import packet_builder as pb
from telemetry.decoder import (
    HEADER_SIZE,
    IgnoredPacketError,
    MalformedPacketError,
    TruncatedPacketError,
    UnsupportedFormatError,
    decode_header,
    decode_packet,
)
from telemetry.packet_models import (
    PACKET_SIZES,
    ButtonDetails,
    CarDamageData,
    CarMotionData,
    CarStatusData,
    CarTelemetryData,
    FastestLapDetails,
    FlashbackDetails,
    LapData,
    LapHistoryData,
    PacketId,
    ParticipantData,
    SessionData,
    SessionHistoryHeader,
    record_struct,
)


def test_record_sizes_match_f1_22_spec():
    assert HEADER_SIZE == 24
    sizes = {CarMotionData: 60, CarTelemetryData: 60, LapData: 43, CarStatusData: 47, CarDamageData: 42, ParticipantData: 56,
             LapHistoryData: 11, SessionHistoryHeader: 7}
    for cls, n in sizes.items():
        assert record_struct(cls).size == n, cls.__name__
    # whole-packet totals from the spec
    assert HEADER_SIZE + 22 * 60 + 3 == PACKET_SIZES[PacketId.CAR_TELEMETRY] == 1347
    assert HEADER_SIZE + 22 * 60 + 30 * 4 == PACKET_SIZES[PacketId.MOTION] == 1464  # + 30 player-only floats
    assert HEADER_SIZE + 22 * 43 + 2 == PACKET_SIZES[PacketId.LAP_DATA] == 972
    assert HEADER_SIZE + 22 * 47 == PACKET_SIZES[PacketId.CAR_STATUS] == 1058
    assert HEADER_SIZE + 22 * 42 == PACKET_SIZES[PacketId.CAR_DAMAGE] == 948
    assert HEADER_SIZE + 1 + 22 * 56 == PACKET_SIZES[PacketId.PARTICIPANTS] == 1257
    assert HEADER_SIZE + 7 + 100 * 11 + 8 * 3 == PACKET_SIZES[PacketId.SESSION_HISTORY] == 1155


def test_header_fields_at_spec_offsets():
    raw = pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, session_uid=0x1122334455667788, session_time=12.5,
                                    frame=77, player=3))
    assert struct.unpack_from("<H", raw, 0)[0] == 2022
    assert raw[5] == PacketId.CAR_TELEMETRY
    assert struct.unpack_from("<Q", raw, 6)[0] == 0x1122334455667788
    assert struct.unpack_from("<f", raw, 14)[0] == 12.5
    assert struct.unpack_from("<I", raw, 18)[0] == 77
    assert raw[22] == 3
    h = decode_header(raw)
    assert (h.packet_format, h.packet_id, h.session_uid, h.frame_identifier, h.player_car_index) == \
        (2022, 6, 0x1122334455667788, 77, 3)


def test_car_telemetry_hand_built_bytes_for_player_index():
    """Build car 5's record by hand from the spec field order, independent of the shared layout."""
    player = 5
    buf = bytearray(PACKET_SIZES[PacketId.CAR_TELEMETRY])
    struct.pack_into("<HBBBBQfIBB", buf, 0, 2022, 1, 0, 1, 6, 42, 1.0, 1, player, 255)
    o = HEADER_SIZE + player * 60
    struct.pack_into("<HfffBbHBBH", buf, o, 287, 0.75, -0.5, 0.25, 10, 7, 11250, 1, 80, 0x3FF)
    struct.pack_into("<4H", buf, o + 22, 500, 510, 600, 610)        # brake temps
    struct.pack_into("<4B4B", buf, o + 30, 90, 91, 95, 96, 100, 101, 102, 103)
    struct.pack_into("<H", buf, o + 38, 110)                          # engine temp
    struct.pack_into("<4f", buf, o + 40, 21.5, 21.6, 23.0, 23.1)      # pressures
    # a different car with other values must not be picked up
    struct.pack_into("<H", buf, HEADER_SIZE + 0 * 60, 99)
    p = decode_packet(bytes(buf))
    d = p.data
    assert p.packet_id == PacketId.CAR_TELEMETRY
    assert d.speed == 287 and d.throttle == 0.75 and d.steer == -0.5 and d.brake == 0.25
    assert d.clutch == 10 and d.gear == 7 and d.engine_rpm == 11250 and d.drs == 1
    assert d.brakes_temperature == (500, 510, 600, 610)
    assert d.tyres_surface_temperature == (90, 91, 95, 96)
    assert d.tyres_inner_temperature == (100, 101, 102, 103)
    assert d.engine_temperature == 110
    assert d.tyres_pressure == pytest.approx((21.5, 21.6, 23.0, 23.1))


def test_reverse_gear_is_signed():
    raw = pb.build_packet(pb.header(PacketId.CAR_TELEMETRY), CarTelemetryData(gear=-1))
    assert decode_packet(raw).data.gear == -1


def test_lap_data_hand_built():
    player = 2
    buf = bytearray(PACKET_SIZES[PacketId.LAP_DATA])
    struct.pack_into("<HBBBBQfIBB", buf, 0, 2022, 1, 0, 1, 2, 1, 1.0, 1, player, 255)
    o = HEADER_SIZE + player * 43
    struct.pack_into("<IIHHfff", buf, o, 91234, 45000, 28000, 0, 2100.5, 15100.5, 0.0)
    struct.pack_into("<14B", buf, o + 24, 4, 3, 0, 1, 1, 1, 0, 0, 0, 0, 6, 1, 2, 0)
    d = decode_packet(bytes(buf)).data
    assert d.last_lap_time_ms == 91234 and d.current_lap_time_ms == 45000
    assert d.sector1_time_ms == 28000 and d.sector2_time_ms == 0
    assert d.lap_distance == pytest.approx(2100.5)
    assert d.car_position == 4 and d.current_lap_num == 3 and d.sector == 1 and d.current_lap_invalid == 1
    assert d.grid_position == 6 and d.driver_status == 1


def test_session_packet_and_safety_car_offset():
    s = SessionData(weather=3, track_temperature=-2, air_temperature=18, total_laps=5, track_length=5891,
                    session_type=10, track_id=7, safety_car_status=2)
    raw = pb.build_session(pb.header(PacketId.SESSION), s)
    assert raw[24 + 19 + 105] == 2  # m_safetyCarStatus after 21 five-byte marshal zones
    d = decode_packet(raw).data
    assert (d.weather, d.track_temperature, d.track_length, d.session_type, d.track_id, d.safety_car_status) == \
        (3, -2, 5891, 10, 7, 2)


def test_session_pit_window_at_spec_offset():
    """Ideal/latest pit lap and rejoin position sit at bytes 613..615; 16 bytes of assists etc. follow."""
    s = SessionData(pit_window_ideal_lap=18, pit_window_latest_lap=23, pit_rejoin_position=7)
    raw = pb.build_session(pb.header(PacketId.SESSION), s)
    assert raw[613:616] == bytes((18, 23, 7)) and len(raw) - 616 == 16
    d = decode_packet(raw).data
    assert (d.pit_window_ideal_lap, d.pit_window_latest_lap, d.pit_rejoin_position) == (18, 23, 7)


def test_lap_data_keeps_every_car():
    raw = pb.build_cars(pb.header(PacketId.LAP_DATA, player=1),
                        {0: LapData(car_position=1, total_distance=900.0, result_status=2),
                         1: LapData(car_position=2, total_distance=850.0, result_status=2),
                         21: LapData(car_position=3, total_distance=10.0, result_status=2)})
    p = decode_packet(raw)
    assert len(p.cars) == 22 and p.data is p.cars[1]
    assert [c.car_position for c in (p.cars[0], p.cars[1], p.cars[21])] == [1, 2, 3]
    assert p.cars[21].total_distance == 10.0 and p.cars[5].result_status == 0


def test_motion_positions_hand_built():
    buf = bytearray(PACKET_SIZES[PacketId.MOTION])
    struct.pack_into("<HBBBBQfIBB", buf, 0, 2022, 1, 0, 1, 0, 1, 1.0, 1, 3, 255)
    struct.pack_into("<3f", buf, HEADER_SIZE + 3 * 60, -120.5, 4.25, 880.0)
    struct.pack_into("<3f", buf, HEADER_SIZE + 7 * 60, 15.0, 0.0, -40.0)
    p = decode_packet(bytes(buf))
    assert p.data.position == (-120.5, 4.25, 880.0)
    assert p.cars[7].position == (15.0, 0.0, -40.0)


def test_participants_keep_all_names_and_active_count():
    raw = pb.build_cars(pb.header(PacketId.PARTICIPANTS), {0: ParticipantData(name=b"ME"),
                                                           4: ParticipantData(name=b"RIVAL", race_number=44)},
                        prefix=bytes([20]))
    p = decode_packet(raw)
    assert p.num_active_cars == 20 and p.cars[4].name.rstrip(bytes(1)) == b"RIVAL" and p.cars[4].race_number == 44


def test_button_event_and_udp_action_bits():
    from telemetry.packet_models import udp_action_bit
    assert udp_action_bit(1) == 0x00100000 and udp_action_bit(12) == 0x80000000
    raw = pb.build_event(pb.header(PacketId.EVENT), "BUTN", ButtonDetails(udp_action_bit(3) | 0x0001))
    d = decode_packet(raw).data
    assert d.code == "BUTN" and d.details.button_status & udp_action_bit(3)


def test_event_packets():
    d = decode_packet(pb.build_event(pb.header(PacketId.EVENT), "SSTA")).data
    assert d.code == "SSTA" and d.details is None
    d = decode_packet(pb.build_event(pb.header(PacketId.EVENT), "FTLP", FastestLapDetails(4, 81.5))).data
    assert d.details.vehicle_idx == 4 and d.details.lap_time == pytest.approx(81.5)
    d = decode_packet(pb.build_event(pb.header(PacketId.EVENT), "FLBK", FlashbackDetails(900, 33.0))).data
    assert d.details.flashback_frame_identifier == 900


def test_event_with_garbage_code_is_rejected():
    raw = bytearray(pb.build_event(pb.header(PacketId.EVENT), "SSTA"))
    raw[24:28] = b"\xff\x00\x01\x02"
    with pytest.raises(MalformedPacketError):
        decode_packet(bytes(raw))


def test_participants_status_damage_history():
    h = pb.header(PacketId.PARTICIPANTS, player=1)
    raw = pb.build_packet(h, ParticipantData(team_id=3, name=b"ALONSO"), prefix=b"\x14")
    assert decode_packet(raw).data.name.rstrip(b"\0") == b"ALONSO"
    raw = pb.build_packet(pb.header(PacketId.CAR_STATUS), CarStatusData(fuel_in_tank=12.5, actual_tyre_compound=17,
                                                                        vehicle_fia_flags=-1))
    d = decode_packet(raw).data
    assert d.fuel_in_tank == 12.5 and d.actual_tyre_compound == 17 and d.vehicle_fia_flags == -1
    raw = pb.build_packet(pb.header(PacketId.CAR_DAMAGE), CarDamageData(tyres_wear=(1.0, 2.0, 3.0, 4.0),
                                                                        front_left_wing_damage=30))
    d = decode_packet(raw).data
    assert d.tyres_wear == (1.0, 2.0, 3.0, 4.0) and d.front_left_wing_damage == 30
    laps = [LapHistoryData(90000, 30000, 30000, 30000, 0x0F), LapHistoryData(89000, 29000, 30000, 30000, 0x0F)]
    raw = pb.build_history(pb.header(PacketId.SESSION_HISTORY), SessionHistoryHeader(car_idx=0, num_laps=2,
                                                                                     best_lap_time_lap_num=2), laps)
    d = decode_packet(raw).data
    assert len(d.laps) == 2 and d.laps[1].lap_time_ms == 89000 and d.header.best_lap_time_lap_num == 2


def test_rejects_wrong_format():
    raw = pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, fmt=2021))
    with pytest.raises(UnsupportedFormatError) as e:
        decode_packet(raw)
    assert e.value.packet_format == 2021


@pytest.mark.parametrize("pid", [PacketId.CAR_TELEMETRY, PacketId.LAP_DATA, PacketId.SESSION, PacketId.EVENT])
def test_rejects_truncated(pid):
    raw = pb.build_packet(pb.header(pid))
    with pytest.raises(TruncatedPacketError):
        decode_packet(raw[:-1])


def test_rejects_short_header_and_unknown_id():
    with pytest.raises(TruncatedPacketError):
        decode_packet(b"\xe6\x07\x01")
    raw = bytearray(pb.build_packet(pb.header(PacketId.CAR_TELEMETRY)))
    raw[5] = 42
    with pytest.raises(MalformedPacketError):
        decode_packet(bytes(raw))


def test_unused_packet_types_are_ignored_not_decoded():
    for pid in (PacketId.CAR_SETUPS, PacketId.FINAL_CLASSIFICATION, PacketId.LOBBY_INFO):
        with pytest.raises(IgnoredPacketError):
            decode_packet(pb.build_packet(pb.header(pid)))


def test_invalid_player_index():
    raw = pb.build_packet(pb.header(PacketId.CAR_TELEMETRY, player=0))
    raw = raw[:22] + bytes([255]) + raw[23:]
    with pytest.raises(MalformedPacketError):
        decode_packet(raw)


def test_packet_id_routing():
    expected = {PacketId.CAR_TELEMETRY: CarTelemetryData, PacketId.LAP_DATA: LapData,
                PacketId.CAR_STATUS: CarStatusData, PacketId.CAR_DAMAGE: CarDamageData}
    for pid, cls in expected.items():
        assert isinstance(decode_packet(pb.build_packet(pb.header(pid), cls())).data, cls)
