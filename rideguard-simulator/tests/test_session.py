import queue
import time
from rideguard_sim.session import Session
from rideguard_sim.replay import load
from test_replay import SAMPLE


class FakeTransport:
    """Tests transport state only; not firmware or hardware evidence."""
    def __init__(self):
        self.error=None;self.rx=queue.Queue();self.sent=[]
    def send(self,message):self.sent.append(message)
    def close(self):pass


def test_control_waits_for_matching_ack(tmp_path):
    s=Session(tmp_path);s.link=FakeTransport()
    try:
        s.start();s.step()
        assert s.state=='STOPPED' and s.world.t==0
        ack=dict(protocol_version=1,session_id=s.envelope.session,type='ACK',command='SESSION_START',seq=99)
        s.link.rx.put((time.monotonic(),ack));s.poll()
        assert s.pending
        ack['seq']=1;s.link.rx.put((time.monotonic(),ack));s.poll()
        assert s.state=='RUNNING' and s.pending is None
    finally:s.close()


def test_stream_clocks_and_reset(tmp_path):
    s=Session(tmp_path)
    try:
        s.start()
        for _ in range(100):s.step()
        assert s.last_sent['IMU']['seq']==100
        assert s.last_sent['RADAR_FRAMELEFT']['seq']==16
        old=s.envelope.session;s.start()
        assert s.envelope.session!=old and s.last_sent.keys()=={'SESSION_START'}
    finally:s.close()


def test_replay_does_not_invent_imu_empty_frames_or_ego_motion(tmp_path):
    s=Session(tmp_path);s.replay=load(SAMPLE,'LEFT')
    try:
        s.start()
        for _ in range(100):s.step()
        assert s.world.ego.x==0 and 'IMU' not in s.last_sent
        assert 'RADAR_FRAMERIGHT' not in s.last_sent
        assert s.last_sent['RADAR_FRAMELEFT']['seq']==7
        assert s.last_sent['RADAR_FRAMELEFT']['t_ms']==378
    finally:s.close()


def test_disconnect_and_expired_result_clear_display(tmp_path):
    s=Session(tmp_path);s.link=FakeTransport()
    s.result={'left':{'level':'HIGH'}};s.result_wall=time.monotonic()-2
    assert s.visible_result() is None
    s.result_wall=time.monotonic();assert s.visible_result()
    s.disconnect();assert s.visible_result() is None
