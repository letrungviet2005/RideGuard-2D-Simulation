"""Real COM acceptance: expectations live here, never in sensor messages."""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import json
from pathlib import Path
import time
from scripts.testing.hardware_acceptance import wait
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
    # Drive the session on the simulation clock, but bound the run with wall
    # time as well: ``world.t`` stops advancing whenever the session is
    # waiting for an ACK, which would otherwise spin here forever.
    wall_deadline=start+duration/speed+5
    while session.world.t<duration:
        if time.monotonic()>wall_deadline:
            break
        deadline=start+(session.world.t+.01)/speed
        session.step()
        while time.monotonic()<deadline:
            session.poll();time.sleep(.001)
        r=session.visible_result()
        if r and r['seq']>last_seq:
            results.append(r);last_seq=r['seq']
        # A replay ends itself at EOF, so the session can legitimately stop
        # before the requested duration.
        if session.state!='RUNNING' or session.replay_finished:
            break
    if session.state=='RUNNING' and session.pending is None:
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

    A *startup* frame is one that arrives before every sensor has been seen
    valid at least once.  That is decided from the order of the frames, not
    from the current ``valid`` flags: a flag says "is it valid now", and the
    first RESULT legitimately shows a sensor as not-yet-valid even though
    another sensor already is.  Once every sensor has been valid, a later
    UNKNOWN is a real observation and is kept.
    """
    keep=[]
    ever_valid=False
    for r in results:
        if r['left']['level']!='UNKNOWN':
            sensors=r.get('sensors') or {}
            if any(sensors.get(name,{}).get('valid') for name in ('IMU','LEFT','RIGHT')):
                ever_valid=True
            keep.append(r);continue
        reason=r['left']['reason_code']
        if reason=='NO_SESSION':
            continue
        if not ever_valid and reason in ('IMU_STALE','RADAR_STALE','LINK_STALE'):
            # Still starting up: not every sensor has been seen yet.
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
                # A HIGH frame must either be turning towards that side, or be
                # inside the hold window: the firmware keeps HIGH for
                # ``hold_ms`` after the yaw falls back below the threshold,
                # so a turning-towards frame can be followed by held frames
                # whose motion has already returned to STRAIGHT.
                hold_ms=session.rules.get('hold_ms',350)
                for frame in live:
                    for side,turn in (('left','LEFT'),('right','RIGHT')):
                        if frame[side]['level']!='HIGH':
                            continue
                        if frame['motion']==turn:
                            continue
                        # Held frame: there must be an earlier, turning-towards
                        # HIGH frame within the hold window on this side.
                        origin=[f for f in live
                                if f[side]['level']=='HIGH' and f['motion']==turn
                                and f['t_ms']<=frame['t_ms']
                                and frame['t_ms']-f['t_ms']<=hold_ms]
                        assert origin,('HIGH without a turning-towards cause',frame)
            if index==1:assert levels['left']==levels['right']=={'NONE'},levels
            if index==2:assert levels['left']=={'MONITOR'} and levels['right']=={'NONE'},levels
            if index==3:assert levels['right']=={'MONITOR'} and levels['left']=={'NONE'},levels
            if index in (0, 4, 5):
                # A warning must mean the vehicle is actually angled towards
                # the warned side.  During a lane change the yaw *rate* flips
                # halfway through while the heading offset keeps its sign, so
                # warning on the opposite side there would flag a vehicle
                # that is merely straightening up.
                for frame in live:
                    heading=frame.get('heading_deg')
                    assert heading is not None,('firmware thiếu heading_deg',frame)
                    if frame['left']['level']=='HIGH':
                        assert heading>0,('HIGH trái khi xe không nghiêng trái',frame)
                    if frame['right']['level']=='HIGH':
                        assert heading<0,('HIGH phải khi xe không nghiêng phải',frame)
            if index==5:
                # The ego changes lane to the left, so the heading offset is
                # positive for the whole manoeuvre.  A right-side warning
                # could only come from the rate flipping back, which is
                # exactly the false alarm this rule removes.
                assert all(r['right']['level']!='HIGH' for r in live), \
                    [r for r in live if r['right']['level']=='HIGH'][:3]
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
        # Recordings live next to the tool: <repo>/tools/samples/.
        session.replay=load(Path(__file__).parents[3]/'samples/radar_recorded_excerpt.csv','LEFT')
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
    parser.add_argument('--log-dir',default=str(Path(__file__).resolve().parents[4]/'.local/simulator-logs'))
    args=parser.parse_args();run(args.port,args.log_dir)
