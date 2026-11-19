"""Real-time CSV acceptance; never uploads/backs up/erases firmware."""
import hashlib
import json
from pathlib import Path
import time
from .dataset_replay import DatasetReplay
from .inference import load_trusted,Engine
from .stream import stream_digest
from .bridge import match_result
from ..session import Session


def percentiles(values):
    if not values:return None
    values=sorted(values)
    return dict(p50=values[int((len(values)-1)*.5)],p95=values[int((len(values)-1)*.95)],max=max(values))


def replay(repo,config_path,session_id,port,output):
    bundle=load_trusted(repo,config_path)
    replay=DatasetReplay(repo/bundle['config']['dataset'],session_id)
    output=Path(output)
    if output.exists():raise ValueError('Evidence output exists; choose a new directory')
    output.mkdir(parents=True)
    session=Session(output);session.dataset=replay;session.playback_speed=.5
    session.radar.config.max_targets=3
    rules=[];ai=[];rx_times=[];hash_seen=hashlib.sha256();count=0
    try:
        if port:
            from ..serial_link import SerialLink
            session.link=SerialLink(port,921600)
            if not session.wait_link_ready(8):raise ValueError('Serial not ready; firmware not verified')
        session.start()
        engine=Engine(bundle,session.envelope.session)
        def observe(events,t_us):
            nonlocal count
            for event in events:hash_seen.update(event.canonical());count+=1
            for result in engine.advance(events,t_us):ai.append(result);session.record('AI',result)
        def receive(result):rules.append(result);rx_times.append(time.monotonic())
        session.sensor_observer=observe;session.result_observer=receive
        deadline=time.monotonic()+4
        while session.pending and time.monotonic()<deadline:session.poll();time.sleep(.005)
        if port and (session.pending or session.state!='RUNNING'):
            raise ValueError('SESSION_START not ACKed; no ESP32 acceptance, no flash attempted')
        started=time.monotonic();next_step=started
        while session.state=='RUNNING' and not session.replay_finished:
            session.poll()
            if session.state!='RUNNING':raise ValueError('Board/transport stopped session: '+str(session.events[-3:]))
            now=time.monotonic()
            if now-next_step>.25:raise RuntimeError('Replay scheduling stall >250ms; stop rather than flood old data')
            if now>=next_step:
                session.step(.005);next_step+=.01
            else:time.sleep(min(.002,next_step-now))
        drain=time.monotonic()+.5
        while time.monotonic()<drain:session.poll();time.sleep(.005)
        matches=[match_result(r,ai,session.envelope.session) for r in rules]
        matches=[m for m in matches if m]
        for m in matches:session.record('COMPARISON',dict(type='MATCH',**m))
        expected=stream_digest(replay.events)
        if count!=len(replay.events) or hash_seen.hexdigest()!=expected or engine.digest.hexdigest()!=expected:
            raise AssertionError('Common stream count/hash mismatch')
        trace=list(session.link.trace.events) if session.link else []
        with (output/'timing.jsonl').open('w',encoding='utf-8') as handle:
            for row in trace:handle.write(json.dumps(row)+'\n')
        writes=[e for e in trace if e['stage']=='write' and e['type'] in ('IMU','RADAR_FRAME')]
        parser_errors=[r.get('diag',{}).get('parser_errors') for r in rules]
        sensor_seqs=rules[-1].get('sensors') if rules else None
        result=dict(session=session_id,port=port,baud=921600,wall_duration_s=time.monotonic()-started,
            simulation_duration_s=session.world.t,speed=.5,common_sensor_events=count,
            expected_sha256=expected,pc_sha256=hash_seen.hexdigest(),ai_sha256=engine.digest.hexdigest(),
            imu_samples=sum(e.kind=='IMU' for e in replay.events),empty_frames=replay.empty,dropped_not_sent=replay.dropped,
            ai_results=len(ai),esp32_results=len(rules),matched=len(matches),disagreements=sum(not m['left_agree'] or not m['right_agree'] for m in matches),
            result_pc_gap_ms=percentiles([(b-a)*1000 for a,b in zip(rx_times,rx_times[1:])]),
            ai_inference_ms=percentiles([r['inference_ms'] for r in ai]),
            actual_sensor_writes=len(writes),trace_dropped=session.link.trace.dropped if session.link else 0,
            last_board_sensor_sequences=sensor_seqs,parser_errors_max=max((v for v in parser_errors if v is not None),default=None),
            limitations='Synthetic only; stream hash proves same host-side event payloads, not every UART byte processed. Hardware identity only application ACK; no flash/hash verification.')
        (output/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2),flush=True)
    finally:session.close()
