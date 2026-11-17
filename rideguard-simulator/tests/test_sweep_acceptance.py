"""Acceptance-helper tests. These assert on recorded/board RESULT frames.

They do not prove firmware behaviour on their own; hardware_scenarios.py
exercises the real ESP32 over USB Serial.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from hardware_scenarios import settled


def result(t_ms, level, reason, valid=('IMU', 'LEFT', 'RIGHT')):
    sensors = {name: {'valid': name in valid} for name in ('IMU', 'LEFT', 'RIGHT')}
    return {'t_ms': t_ms, 'sensors': sensors,
            'left': {'level': level, 'reason_code': reason},
            'right': {'level': level, 'reason_code': reason}}


def test_startup_unknown_is_legitimate_and_dropped():
    startup = result(0, 'UNKNOWN', 'IMU_STALE', valid=())
    steady = result(100, 'MONITOR', 'TARGET_IN_ZONE')
    assert settled([startup, steady]) == [steady]


def test_teardown_unknown_is_legitimate_and_dropped():
    steady = result(100, 'NONE', 'EMPTY_VALID_FRAMES')
    teardown = result(6010, 'UNKNOWN', 'NO_SESSION')
    assert settled([steady, teardown]) == [steady]


def test_mid_run_sensor_loss_is_kept():
    steady = result(100, 'MONITOR', 'TARGET_IN_ZONE')
    lost = result(400, 'UNKNOWN', 'RADAR_STALE', valid=('IMU',))
    assert settled([steady, lost]) == [steady, lost]


def test_mid_run_imu_stale_is_kept():
    steady = result(100, 'MONITOR', 'TARGET_IN_ZONE')
    lost = result(400, 'UNKNOWN', 'IMU_STALE', valid=('LEFT', 'RIGHT'))
    assert settled([steady, lost]) == [steady, lost]
