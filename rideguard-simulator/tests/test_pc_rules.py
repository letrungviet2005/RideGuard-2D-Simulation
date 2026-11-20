"""PC engine, thresholds, manual stream identity and real C++ header parity."""
import json
import os
import subprocess
import time
from types import SimpleNamespace
import queue
import pytest
from rideguard_sim.protocol import DEFAULT_RULES,Envelope
from rideguard_sim.pc_rules import PcRules,validate_rules,YELLOW_UNSUPPORTED
from rideguard_sim.ml.stream import Event
from rideguard_sim.session import Session


def configured(**kw):return dict(DEFAULT_RULES,yellow_mode='distance',yellow_m=15,near_m=10,**kw)


@pytest.mark.parametrize('side',['LEFT','RIGHT'])
@pytest.mark.parametrize('distance',[16,15,12,10,8])
@pytest.mark.parametrize('turn',[0,8,-8])
def test_yellow_red_both_sides_require_confirmed_turn(side,distance,turn):
    clock=[0.];engine=PcRules(configured(),'test',lambda:clock[0]);env=Envelope();env.session='test'
    for t in range(0,810,10):
        clock[0]=t/1000
        for message in (env.make('IMU',t,timestamp_ms=t,ax_mps2=0,ay_mps2=0,az_mps2=9.80665,gx_dps=0,gy_dps=0,gz_dps=turn),
                        *[env.make('RADAR_FRAME',t,radar_id=r,targets=[dict(track_id=31,y_m=distance,angle_deg=0)] if r==side else []) for r in ('LEFT','RIGHT')]):
            engine.ingest(Event.from_message(message))
        result=engine.result()
    toward=turn==(8 if side=='LEFT' else -8)
    expected='HIGH' if toward and distance<=10 else 'MONITOR' if distance<=15 else 'NONE'
    key=side.lower();assert result[key]['level']==expected
    assert result[('RIGHT' if side=='LEFT' else 'LEFT').lower()]['level']=='NONE'
    if distance>15:assert result[key]['reason_code']=='TARGET_OUTSIDE_YELLOW'
    assert result['rule_source']=='pc' and result['type']=='RULE_PC'
    clock[0]+=1.1
    assert engine.result()[key]['level']=='UNKNOWN'


def test_invalid_thresholds_and_legacy_detection():
    bad=configured();bad['yellow_m']=9
    with pytest.raises(ValueError,match='đỏ'):validate_rules(bad)
    engine=PcRules(dict(DEFAULT_RULES),'s')
    for radar in ('LEFT','RIGHT'):
        engine.ingest(Event('s','RADAR_FRAME',1,0,json.dumps(dict(radar_id=radar,targets=[]))))
    assert engine.result()['left']['reason_code']=='IMU_STALE'


def test_manual_override_is_single_point_and_reset_never_carries_turn(tmp_path):
    s=Session(tmp_path);s.rule_source='pc';s.imu_mode='manual';s.start(2)
    assert s.pending is None and s.manual_gz==0
    s.manual_gz=8;observed=[];s.sensor_observer=lambda events,t:observed.extend(events)
    for _ in range(20):s.step(.01)
    imu=[e for e in observed if e.kind=='IMU']
    assert imu and all(e.wire_fields()['gz_dps']==8 for e in imu)
    assert s.world.ego.heading==0 # gyro override is not a trajectory command
    assert s.pc_rules.sensor_count==len(observed)
    import hashlib
    h=hashlib.sha256()
    for event in observed:h.update(event.canonical())
    assert h.hexdigest()==s.pc_rules.stream_hash.hexdigest()
    count=len(s.live_imu_rows);s.command('PAUSE','PAUSED');s.manual_gz=-5;s.step(.01)
    assert len(s.live_imu_rows)==count and s.pc_rules.result()['left']['reason_code']=='PAUSED'
    s.command('RESUME','RUNNING');assert s.pc_rules.result()['left']['level']=='UNKNOWN'
    s.start(2);assert s.manual_gz==0
    s.imu_recording=object();s.manual_gz=50
    message=s.send('IMU',t_ms=100,gz_dps=1,ax_mps2=0,ay_mps2=0,az_mps2=9.80665,gx_dps=0,gy_dps=0,timestamp_ms=100)
    assert message['gz_dps']==1 # recorded data is never overwritten by manual mode
    s.close()


def test_hardware_no_fallback_and_extended_ack_required(tmp_path):
    s=Session(tmp_path);s.rule_source='esp32';s.rules=configured()
    with pytest.raises(ValueError,match='Firmware này'):s.start()
    assert s.pc_rules is None
    class Link:
        baud=921600;error=None;rx=queue.Queue()
        def send(self,message):pass
        def close(self):pass
    s.link=Link();s.capabilities={'yellow_distance_v1':True};s.start()
    pending=s.pending
    s.link.rx.put((time.monotonic(),dict(protocol_version=1,type='ACK',session_id=s.envelope.session,
        command='SESSION_START',seq=pending[1]))) # generic old ACK is insufficient
    s.poll();assert s.link is None and s.pc_rules is None and s.result is None
    s.close()


HOST=os.environ.get('RIDEGUARD_RULE_HOST')
@pytest.mark.skipif(not HOST,reason='Compile firmware/tests/rule_host.cpp and set RIDEGUARD_RULE_HOST')
@pytest.mark.parametrize('mode',['detected','distance'])
def test_same_stream_matches_actual_firmware_headers_at_transitions(mode):
    clock=[0.];rules=configured();rules['yellow_mode']=mode
    env=Envelope();env.session='host-parity';engine=PcRules(rules,env.session,lambda:clock[0])
    wire=[dict(wall_ms=0,message=env.make('SESSION_START',rules=rules),evaluate=False)]
    expected=[]
    for t in range(0,6000,10):
        clock[0]=t/1000;distance=[16,15,12,10,8][min(4,t//1000)]
        yaw=0 if t<1000 else 8 if t<3000 else -8
        messages=[env.make('IMU',t,timestamp_ms=t,ax_mps2=0,ay_mps2=0,az_mps2=9.80665,gx_dps=0,gy_dps=0,gz_dps=yaw)]
        for radar in ('LEFT','RIGHT'):
            if t>=5500 and radar=='RIGHT':continue # missing radar
            messages.append(env.make('RADAR_FRAME',t,radar_id=radar,
                targets=[dict(track_id=31 if radar=='LEFT' else 32,y_m=distance,angle_deg=0,
                              dir=1,v_kmh=0,high_speed=0,associated=1,duration_frames=1)]))
        for i,message in enumerate(messages):
            engine.ingest(Event.from_message(message))
            evaluate=i==len(messages)-1 and t%50==0
            wire.append(dict(wall_ms=t,message=message,evaluate=evaluate))
            if evaluate:expected.append(engine.result())
    run=subprocess.run([HOST],input='\n'.join(json.dumps(row) for row in wire)+'\n',
        text=True,encoding='utf-8',capture_output=True,check=True)
    actual=[json.loads(line) for line in run.stdout.splitlines()]
    assert len(actual)==len(expected)==120
    for native,pc in zip(actual,expected):
        assert 'host_error' not in native
        assert native['t_ms']==pc['t_ms'] and native['motion']==pc['motion']
        assert native['filtered_yaw_dps']==pytest.approx(pc['filtered_yaw_dps'],abs=1e-4)
        assert native['heading_deg']==pytest.approx(pc['heading_deg'],abs=.001)
        for side in ('left','right'):
            for key in ('level','reason_code','radar_id'):assert native[side][key]==pc[side][key],(native,pc)
            assert native[side]['track_id']==(pc[side]['track_id'] if pc[side]['track_id'] is not None else -1)
