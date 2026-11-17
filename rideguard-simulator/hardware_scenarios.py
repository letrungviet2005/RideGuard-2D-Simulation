"""Real COM acceptance: expectations live here, never in sensor messages."""
import argparse
import json
from pathlib import Path
import time
from hardware_acceptance import wait
from rideguard_sim.session import Session
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.protocol import BAUD, budget_bytes_per_second
from rideguard_sim.replay import load
from rideguard_sim import scenarios


def exercise(session,index,duration=6,speed=1):
    # Refuse configurations the UART cannot carry: dropped bytes desynchronise
    # the JSON framing, which the board correctly reports as RX_DESYNC.
    budget=budget_bytes_per_second(session.radar.config.period_ms,
                                   session.radar.config.max_targets,speed,
                                   session.imu_period_ms)
    limit=BAUD/10*.8
    if budget>limit:
        raise ValueError(f'Serial budget exceeded at {speed}x: {budget:.0f} B/s > {limit:.0f} B/s')
    session.start(index)
    wait(session,lambda:session.pending is None and session.state=='RUNNING')
    results=[];last_seq=0;start=time.monotonic()
    while session.world.t<duration:
        deadline=start+(session.world.t+.01)/speed
        session.step()
        while time.monotonic()<deadline:
            session.poll();time.sleep(.001)
        r=session.visible_result()
        if r and r['seq']>last_seq:
            results.append(r);last_seq=r['seq']
        assert session.state=='RUNNING',session.events[-3:]
    session.command('SESSION_STOP','STOPPED')
    wait(session,lambda:session.pending is None and session.state=='STOPPED')
    assert results,'No real RESULT frames'
    return results,str(session.log_path)


def settled(results):
    """Drop frames where UNKNOWN is legitimate rather than a fault.

    The board is conservative on purpose: before the first IMU/radar samples
    arrive it reports UNKNOWN/IMU_STALE, and after SESSION_STOP it reports
    UNKNOWN/NO_SESSION. Both are correct behaviour, so they must not be
    mistaken for a sensor fault in the middle of a run.

    A startup frame is one where no sensor has ever been valid; a teardown
    frame carries NO_SESSION. Everything else is a real observation and is
    kept, including mid-run IMU_STALE / RADAR_STALE.
    """
    keep=[]
    for r in results:
        if r['left']['level']!='UNKNOWN':
            keep.append(r);continue
        reason=r['left']['reason_code']
        sensors=r.get('sensors') or {}
        never_valid=not any(sensors.get(name,{}).get('valid') for name in ('IMU','LEFT','RIGHT'))
        if reason=='NO_SESSION' or (never_valid and reason in ('IMU_STALE','RADAR_STALE','LINK_STALE')):
            continue
        keep.append(r)
    return keep


def run(port,log_dir):
    session=Session(log_dir);session.link=SerialLink(port,BAUD)
    report=[]
    try:
        # Let the board finish booting so its ROM handshake noise cannot
        # corrupt the first protocol line.
        assert session.wait_link_ready(8),'Serial link never became ready'
        for index,name in enumerate(scenarios.NAMES):
            results,path=exercise(session,index,10 if index==6 else 6)
            live=settled(results)
            assert live,'No settled RESULT frames'
            levels={side:{r[side]['level'] for r in live} for side in ('left','right')}
            if index in (0,8,9):assert 'HIGH' in levels['left'],levels
            if index==5:
                # Both sides carry a target and the ego changes lane left, so the
                # quintic profile turns left first and counter-steers right at
                # the end. HIGH therefore belongs to whichever side the ego is
                # turning towards once a target is inside the near zone.
                assert 'HIGH' in levels['left']|levels['right'],levels
                for frame in live:
                    if frame['left']['level']=='HIGH':
                        assert frame['motion']=='LEFT',frame
                    if frame['right']['level']=='HIGH':
                        assert frame['motion']=='RIGHT',frame
            if index==1:assert levels['left']==levels['right']=={'NONE'},levels
            if index==2:assert levels['left']=={'MONITOR'} and levels['right']=={'NONE'},levels
            if index==3:assert levels['right']=={'MONITOR'} and levels['left']=={'NONE'},levels
            if index==4:
                # Initial outward part; return steering is separately visible.
                assert all(r['left']['level']!='HIGH' for r in live if 2000<r['t_ms']<3900)
            if index==6:assert {'NONE','MONITOR'}<=levels['left']|levels['right'],levels
            if index==7:assert 'HIGH' not in levels['left']|levels['right'],levels
            if index in (8,9):
                # Genuine mid-run sensor loss: the tail must be UNKNOWN for both sides.
                assert results[-1]['left']['level']==results[-1]['right']['level']=='UNKNOWN'
                assert results[-1]['left']['reason_code']!= 'NO_SESSION'
            report.append(dict(scenario=name,result_frames=len(results),levels={k:sorted(v) for k,v in levels.items()},log=path))
            print(f'PASS {index}: {name} ({len(results)} RESULT)',flush=True)
        for speed in (.5,2):
            # Choose the shortest IMU and radar periods that still fit the
            # UART budget at this playback speed, so the variants test timing
            # rather than an overrun.
            period=session.radar.config.period_ms
            for candidate in (65,100,125,150,200,250):
                session.radar.config.period_ms=candidate
                if budget_bytes_per_second(candidate,session.radar.config.max_targets,speed)<=BAUD/10*.8:
                    period=candidate
                    break
            session.radar.config.period_ms=period
            imu_period=10
            for candidate in (10,20,25,50,100):
                if budget_bytes_per_second(period,session.radar.config.max_targets,speed,candidate)<=BAUD/10*.8:
                    imu_period=candidate
                    break
            session.imu_period_ms=imu_period
            results,path=exercise(session,0,6,speed)
            assert 'HIGH' in {r['left']['level'] for r in settled(results)}
            report.append(dict(scenario=f'primary {speed}x',radar_period_ms=period,
                               imu_period_ms=imu_period,
                               result_frames=len(results),log=path))
            print(f'PASS primary {speed}x (radar {period} ms, imu {imu_period} ms)',flush=True)
        session.imu_period_ms=10
        session.replay=load(Path(__file__).parent/'samples/radar_recorded_excerpt.csv','LEFT')
        results,path=exercise(session,0,1.5)
        assert any(any(t['track_id']==31 for t in r['targets']) for r in results)
        assert all(r['left']['level']=='UNKNOWN' for r in results)
        report.append(dict(scenario='recorded excerpt, missing IMU/right radar',result_frames=len(results),log=path))
        output=Path(log_dir)/'hardware-scenarios-summary.json'
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'PASS {len(report)} runs; summary {output}')
    finally:session.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',required=True)
    parser.add_argument('--log-dir',default=str(Path(__file__).resolve().parents[2]/'.local/simulator-logs'))
    args=parser.parse_args();run(args.port,args.log_dir)
