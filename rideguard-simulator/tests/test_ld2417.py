"""Tests for the LD2417 binary decoder.

The bytes in :func:`test_official_example` are copied verbatim from the
example frame in the official ``HLK-LD2417串口协议说明V1.0.4.pdf`` so the
field offsets and scaling are pinned to the vendor document rather than to
a hand-written interpretation.
"""
import pytest

from rideguard_sim.ld2417 import (
    DIR_LEFT,
    DIR_RIGHT,
    Frame,
    Ld2417Error,
    StreamDecoder,
    decode_frame,
    decode_target,
    to_protocol,
)

# Official example frame:
#   AA AA 03 0D 02 6A 02 D7 0B 02 0F 23 00
#          0E 02 80 00 2A 07 00 0F 11 00 1F
#          01 1D 02 87 1A 01 0B EE FF 55 55
OFFICIAL = bytes.fromhex(
    'AA AA 03'
    '0D 02 6A 02 D7 0B 02 0F 23 00'
    '0E 02 80 00 2A 07 00 0F 11 00 1F'
    '01 1D 02 87 1A 01 0B EE FF'
    '55 55'
)


def test_official_example():
    decoder = StreamDecoder()
    frames = decoder.feed(OFFICIAL)
    assert len(frames) == 1
    frame = frames[0]
    assert len(frame.targets) == 3

    # Target 1: id 13, right lane, 6.18 m, 30.31 km/h, not high speed,
    # associated, 15 frames, +35 degrees.
    first = frame.targets[0]
    assert first.track_id == 13
    assert first.dir == DIR_RIGHT
    assert first.y_m == pytest.approx(6.18)
    assert first.v_kmh == pytest.approx(30.31)
    assert first.high_speed == 0
    assert first.associated == 1
    assert first.duration_frames == 15
    assert first.angle_deg == 35

    # Target 2: id 14, right lane, 1.28 m, 18.34 km/h, not high speed,
    # not associated, 15 frames, +17 degrees.
    second = frame.targets[1]
    assert second.track_id == 14
    assert second.dir == DIR_RIGHT
    assert second.y_m == pytest.approx(1.28)
    assert second.v_kmh == pytest.approx(18.34)
    assert second.high_speed == 0
    assert second.associated == 0
    assert second.duration_frames == 15
    assert second.angle_deg == 17

    # Target 3: id 31, left lane, 5.41 m, 67.91 km/h, high speed,
    # not associated, 11 frames, -18 degrees.
    third = frame.targets[2]
    assert third.track_id == 31
    assert third.dir == DIR_LEFT
    assert third.y_m == pytest.approx(5.41)
    assert third.v_kmh == pytest.approx(67.91)
    assert third.high_speed == 1
    assert third.associated == 0
    assert third.duration_frames == 11
    assert third.angle_deg == -18


def test_negative_angle_is_signed():
    # id=11, dir=1, y=1.00 m, v=0, state=0, angle=-18 degrees
    payload = bytes.fromhex('0B 01 64 00 00 00 00 00 EE FF')
    target = decode_target(payload)
    assert target.angle_deg == -18
    assert target.dir == DIR_LEFT


def test_zero_distance_rejected():
    # The radar never reports 0 m; the protocol bounds start at 1 mm.
    payload = bytes.fromhex('0B 01 00 00 00 00 00 00 EE FF')
    with pytest.raises(Ld2417Error):
        decode_target(payload)


def test_state_bit_layout():
    # state = 0x0F02 -> high speed 0, associated 1, duration 15
    state = 0x0F02
    payload = bytes([1, 1]) + (100).to_bytes(2, 'little') \
        + (500).to_bytes(2, 'little') + state.to_bytes(2, 'little') \
        + (0).to_bytes(2, 'little', signed=True)
    target = decode_target(payload)
    assert target.high_speed == (state & 1)
    assert target.associated == ((state >> 1) & 1)
    assert target.duration_frames == ((state >> 8) & 0xFF)


def test_invalid_direction_rejected():
    payload = bytes.fromhex('0D 03 6A 02 D7 0B 02 0F 23 00')
    with pytest.raises(Ld2417Error):
        decode_target(payload)


def test_wrong_target_length_rejected():
    with pytest.raises(Ld2417Error):
        decode_target(bytes(9))


def test_frame_count_mismatch_rejected():
    with pytest.raises(Ld2417Error):
        decode_frame(bytes.fromhex('02 0D 02 6A 02 D7 0B 02 0F 23 00'))


def test_zero_target_frame_rejected():
    with pytest.raises(Ld2417Error):
        decode_frame(b'\x00')


def test_too_many_targets_rejected():
    with pytest.raises(Ld2417Error):
        decode_frame(bytes([5]) + bytes(50))


def test_stream_resyncs_after_garbage():
    decoder = StreamDecoder()
    frames = decoder.feed(b'\x00\xFF\x13' + OFFICIAL)
    assert len(frames) == 1
    assert len(frames[0].targets) == 3


def test_stream_handles_split_reads():
    decoder = StreamDecoder()
    for index in range(len(OFFICIAL)):
        frames = decoder.feed(OFFICIAL[index:index + 1])
        if index < len(OFFICIAL) - 1:
            assert frames == []
    assert len(frames) == 1


def test_stream_rejects_truncated_frame_without_inventing_target():
    decoder = StreamDecoder()
    frames = decoder.feed(OFFICIAL[:-3])
    assert frames == []


def test_stream_drops_frame_with_bad_tail():
    broken = OFFICIAL[:-2] + b'\x00\x00'
    decoder = StreamDecoder()
    frames = decoder.feed(broken)
    assert frames == []


def test_to_protocol_maps_direction_to_radar_id():
    decoder = StreamDecoder()
    frame = decoder.feed(OFFICIAL)[0]
    left = to_protocol(frame.targets[2])
    right = to_protocol(frame.targets[0])
    assert left['radar_id'] == 'LEFT'
    assert right['radar_id'] == 'RIGHT'
    assert left['y_m'] == pytest.approx(5.41)
    assert left['angle_deg'] == pytest.approx(-18.0)


def test_frame_lane_helpers():
    decoder = StreamDecoder()
    frame = decoder.feed(OFFICIAL)[0]
    assert [t.track_id for t in frame.left] == [31]
    assert [t.track_id for t in frame.right] == [13, 14]
