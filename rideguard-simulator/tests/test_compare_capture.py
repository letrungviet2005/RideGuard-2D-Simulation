"""Tests for the capture comparison tool.

The tool reproduces the firmware's motion state machine on a recorded IMU
log, so the tests check that it agrees with the rules in
``firmware/include/sim_state.hpp``: an 80 ms low-pass on the yaw rate and a
200 ms confirmation before a turn is reported.
"""
import pytest

from scripts.hardware.compare_capture import motion_states, yaw_series
from scripts.data.make_synthetic_capture import SCENARIOS, generate, write_files
from rideguard_sim import imu_import


def load_synthetic(tmp_path, name):
    imu_rows, radar_rows = generate(name, name)
    radar_path, imu_path = write_files(tmp_path, name, imu_rows, radar_rows)
    return imu_import.load(str(imu_path), mount='identity', rebase=False)


def test_straight_run_is_never_a_turn(tmp_path):
    recording = load_synthetic(tmp_path, 'straight')
    states = motion_states(recording)
    assert states
    assert all(state == 'STRAIGHT' for _, state in states)


def test_left_turn_is_reported_as_left(tmp_path):
    recording = load_synthetic(tmp_path, 'left_turn')
    states = motion_states(recording)
    labels = {state for _, state in states}
    assert 'LEFT' in labels
    assert 'RIGHT' not in labels


def test_right_turn_is_reported_as_right(tmp_path):
    recording = load_synthetic(tmp_path, 'right_turn')
    states = motion_states(recording)
    labels = {state for _, state in states}
    assert 'RIGHT' in labels
    assert 'LEFT' not in labels


def test_road_vibration_is_not_mistaken_for_a_turn(tmp_path):
    """A rough road shakes the gyro, but it must not produce a turn.

    The 200 ms confirmation is what protects the warning here: the filtered
    yaw crosses the threshold only briefly, so the candidate never holds
    long enough to be confirmed.
    """
    recording = load_synthetic(tmp_path, 'bumpy')
    raw = [abs(sample.gz_dps) for sample in recording.samples]
    assert max(raw) > 2.0, 'the synthetic vibration must actually shake the gyro'

    states = motion_states(recording)
    assert all(state == 'STRAIGHT' for _, state in states)


def test_confirm_delay_delays_the_first_turn_report(tmp_path):
    """A turn must be confirmed for 200 ms before it is reported.

    The firmware only switches ``motion`` to LEFT once the candidate has
    held for ``confirm_ms``.  The first reported LEFT therefore has to come
    at least 200 ms after the filtered yaw first crossed the threshold.
    """
    recording = load_synthetic(tmp_path, 'left_turn')
    states = motion_states(recording)
    first_left = next(stamp for stamp, state in states if state == 'LEFT')

    # Recompute the filtered yaw to find when the threshold was crossed.
    series = yaw_series(recording)
    first_above = next(stamp for stamp, value in series if value > 2.0)

    assert first_left >= first_above + 200
    # And the turn must not be reported instantly at the very first sample.
    assert first_left > 0


def test_yaw_series_starts_at_zero_and_tracks_the_input(tmp_path):
    recording = load_synthetic(tmp_path, 'left_turn')
    series = yaw_series(recording)
    assert series[0][1] == 0.0
    assert max(value for _, value in series) > 5.0


def test_every_synthetic_scenario_produces_both_files(tmp_path):
    for name in SCENARIOS:
        imu_rows, radar_rows = generate(name, name)
        radar_path, imu_path = write_files(tmp_path, name, imu_rows, radar_rows)
        assert radar_path.exists()
        assert imu_path.exists()
        assert len(imu_rows) > 1000
        assert len(radar_rows) > 100
