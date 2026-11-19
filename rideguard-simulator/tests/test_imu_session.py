import queue
import time
from pathlib import Path

import pytest

from rideguard_sim.session import Session
from rideguard_sim.replay import load
from rideguard_sim import imu_import
from test_replay import SAMPLE

IMU_SAMPLE = Path(__file__).parents[2] / 'samples' / 'generated' / 'imu_bmi160_synthetic.csv'


class FakeTransport:
    """Tests transport state only; not firmware or hardware evidence."""
    def __init__(self):
        self.error = None
        self.rx = queue.Queue()
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def close(self):
        pass


def test_recorded_imu_is_sent_instead_of_synthetic(tmp_path):
    s = Session(tmp_path)
    s.replay = load(SAMPLE, 'LEFT')
    s.load_imu(IMU_SAMPLE, mount='identity')
    try:
        s.start()
        # The radar excerpt is 378 ms long; the IMU log is 10 s.
        for _ in range(400):
            s.step()
        assert 'IMU' in s.last_sent
        message = s.last_sent['IMU']
        # Every IMU field is present and within the firmware's domain.
        for field in ('ax_mps2', 'ay_mps2', 'az_mps2', 'gx_dps', 'gy_dps', 'gz_dps'):
            assert field in message
        assert abs(message['az_mps2']) <= 200
        assert abs(message['gz_dps']) <= 2000
        # timestamp_ms must equal t_ms for the firmware's ordering check.
        assert message['timestamp_ms'] == message['t_ms']
    finally:
        s.close()


def test_recorded_imu_timestamps_increase(tmp_path):
    s = Session(tmp_path)
    s.replay = load(SAMPLE, 'LEFT')
    s.load_imu(IMU_SAMPLE, mount='identity')
    try:
        s.start()
        for _ in range(400):
            s.step()
        # Offline mode records the messages in the session log.
        assert s.last_sent['IMU']['seq'] > 10
        assert s.last_sent['IMU']['timestamp_ms'] == s.last_sent['IMU']['t_ms']
        # Radar EOF must not silently truncate the longer IMU recording.
        assert s.last_sent['IMU']['t_ms'] == 4000
        assert not s.replay_finished
        for _ in range(610):
            s.step()
        assert s.replay_finished and s.state=='STOPPED'
        assert s.imu_index==len(s.imu_recording.samples)
    finally:
        s.close()


def test_imu_offset_shifts_the_clock(tmp_path):
    s = Session(tmp_path)
    s.replay = load(SAMPLE, 'LEFT')
    s.load_imu(IMU_SAMPLE, mount='identity', offset_ms=5000)
    try:
        s.start()
        for _ in range(100):
            s.step()
        # With a +5000 ms offset no sample is due yet, because the replay
        # clock starts at 0.
        assert 'IMU' not in s.last_sent
    finally:
        s.close()


def test_clear_imu_returns_to_synthetic(tmp_path):
    s = Session(tmp_path)
    s.load_imu(IMU_SAMPLE, mount='identity')
    s.clear_imu()
    assert s.imu_recording is None
    assert s.imu_offset_ms == 0
    try:
        s.start()
        for _ in range(100):
            s.step()
        assert 'IMU' in s.last_sent
    finally:
        s.close()


def test_loaded_imu_is_recorded_in_the_log(tmp_path):
    s = Session(tmp_path)
    s.replay = load(SAMPLE, 'LEFT')
    s.load_imu(IMU_SAMPLE, mount='xyz<-x,z,y')
    try:
        s.start()
        text = s.log_path.read_text(encoding='utf-8')
        assert 'RECORDED_IMU_SOURCE' in text
        assert 'xyz<-x,z,y' in text
    finally:
        s.close()


def test_synthetic_imu_still_works_when_no_recording(tmp_path):
    s = Session(tmp_path)
    s.replay = load(SAMPLE, 'LEFT')
    s.replay_imu = True
    try:
        s.start()
        for _ in range(100):
            s.step()
        assert 'IMU' in s.last_sent
        assert s.imu_recording is None
    finally:
        s.close()
