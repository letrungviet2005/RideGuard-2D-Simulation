"""Explicit opt-in real board test; never synthesizes ESP32 output."""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
from pathlib import Path
import time
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.session import Session
from rideguard_sim.protocol import BAUD


def wait(session,predicate,timeout=4):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        session.poll()
        if predicate():return
        time.sleep(.01)
    raise AssertionError(f'Timeout: {session.state}, {session.events[-3:]}')


def run(port,log_dir):
    session=Session(log_dir)
    session.link=SerialLink(port,BAUD)
    levels=set();motions=set();count=0
    try:
        assert session.wait_link_ready(8),'Serial link never became ready'
        session.start()
        wait(session,lambda:session.pending is None and session.state=='RUNNING')
        start=time.monotonic()
        while session.world.t<6:
            deadline=start+session.world.t+.01
            session.step()
            while time.monotonic()<deadline:
                session.poll();time.sleep(.001)
            result=session.visible_result()
            if result:
                levels.add(result['left']['level']);motions.add(result['motion']);count+=1
            assert session.state=='RUNNING',session.events[-3:]
        assert {'MONITOR','HIGH'}<=levels,(levels,motions)
        session.command('PAUSE','PAUSED')
        wait(session,lambda:session.pending is None and session.state=='PAUSED')
        wait(session,lambda:session.visible_result() and session.visible_result()['left']['reason_code']=='PAUSED')
        session.command('RESUME','RUNNING')
        wait(session,lambda:session.pending is None and session.state=='RUNNING')
        # No fresh samples after resume: must become UNKNOWN, never safe.
        wait(session,lambda:session.visible_result() and session.visible_result()['left']['level']=='UNKNOWN')
        session.command('SESSION_STOP','STOPPED')
        wait(session,lambda:session.pending is None and session.state=='STOPPED')
        path=session.log_path
        session.disconnect()
        assert session.visible_result() is None
        print(f'PASS real ESP32: left levels={sorted(levels)}, motions={sorted(motions)}, observations={count}; pause/resume/stop ACK + disconnect freshness. Log: {path}')
    finally:
        session.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',required=True)
    parser.add_argument('--log-dir',default=str(Path(__file__).resolve().parents[4]/'.local/simulator-logs'))
    args=parser.parse_args();run(args.port,args.log_dir)
