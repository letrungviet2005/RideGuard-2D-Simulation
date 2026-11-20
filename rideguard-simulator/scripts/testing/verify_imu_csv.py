"""Opt-in application replay, no flash operations. Saves actual TX/RX evidence."""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import json
from pathlib import Path
import time
from scripts.testing.hardware_acceptance import wait
from rideguard_sim.protocol import BAUD
from rideguard_sim.session import Session
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.timing import TimingTrace
from scripts.testing.accept_csv_flow import percentiles


def run(port,radar,imu,output,speed):
    s=Session(output)
    s.load_replay(radar,'LEFT')
    s.load_imu(imu,profile='user_table_v1',mount='identity')
    s.playback_speed=speed
    s.link=SerialLink(port,BAUD);link=s.link;link.trace=TimingTrace(131072)
    try:
        assert s.wait_link_ready(8) and not link.error,link.error
        s.start(0);wait(s,lambda:s.state=='RUNNING' and s.pending is None)
        start=time.perf_counter();deadline=start+max(s.replay.frames[-1].t_ms/1000,s.imu_recording.samples[-1].t_us/1e6)/speed+10
        while s.state=='RUNNING' and not s.replay_finished:
            assert time.perf_counter()<deadline,'Replay exceeded wall deadline'
            s.step(.005)
            target=start+s.world.t/speed
            while time.perf_counter()<target:
                s.poll();time.sleep(.001)
            s.poll()
            assert not link.error and s.state!='ERROR',(link.error,s.events[-5:])
        wait(s,lambda:s.state=='STOPPED' and s.pending is None)
        s.log.flush()
        events=list(link.trace.events)
        rx=[e for e in events if e['stage']=='receive' and e['type']=='RESULT' and e['session']==s.envelope.session]
        tx=[e for e in events if e['stage']=='write' and e['session']==s.envelope.session]
        diags=[e['diagnostics'] for e in rx if e.get('diagnostics')]
        imu_sent=[e for e in tx if e['type']=='IMU']
        report=dict(port=port,baud=BAUD,speed=speed,source='SYNTHETIC IMU + existing radar CSV; synchronization UNVERIFIED',
            wall_seconds=time.perf_counter()-start,source_imu_count=len(s.imu_recording.samples),sent_imu_count=len(imu_sent),
            source_span_us=s.imu_recording.span_us,source_rate_hz=s.imu_recording.rate_hz,
            result_count=len(rx),result_gap_ms=percentiles([(b['pc_ns']-a['pc_ns'])/1e6 for a,b in zip(rx,rx[1:])]),
            sequence_gaps=sum(max(0,b['seq']-a['seq']-1) for a,b in zip(rx,rx[1:])),
            parser_errors=max((d['parser_errors'] for d in diags),default=None),
            queue_peak=max((e.get('depth',0) for e in events if e['stage']=='enqueue'),default=0),
            trace_dropped=link.trace.dropped,log=str(s.log_path))
        Path(output,'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        Path(output,'timing.json').write_text(json.dumps(events),encoding='utf-8')
        print(json.dumps(report),flush=True)
        assert len(imu_sent)==len(s.imu_recording.samples),report
        assert report['parser_errors']==0 and report['sequence_gaps']==0 and link.trace.dropped==0,report
    finally:s.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True)
    p.add_argument('--radar',required=True)
    p.add_argument('--imu',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--speed',type=float,default=.5)
    a=p.parse_args();run(a.port,a.radar,a.imu,a.out,a.speed)
