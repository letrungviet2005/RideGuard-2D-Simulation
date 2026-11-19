"""Real Serial acceptance at 1x; never substitutes generated RESULT data."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from hardware_acceptance import wait
from rideguard_sim.protocol import BAUD
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.session import Session


def percentiles(values):
    values = sorted(values)
    if not values:
        return None
    return dict(n=len(values), p50=values[math.ceil(.50*len(values))-1],
                p95=values[math.ceil(.95*len(values))-1], max=values[-1])


def exercise(session, name, scenario, duration, csv_pair=False):
    link = session.link
    link.trace.events.clear(); link.trace.dropped = 0
    session.replay = None; session.clear_imu()
    sources = []
    if csv_pair:
        samples = Path(__file__).resolve().parent.parent/'samples/generated'
        radar = samples/'left_approach_45s_dual.csv'
        imu = samples/'imu_bmi160_45s.csv'
        session.load_replay(radar, 'LEFT')
        session.load_imu(imu, mount='identity', offset_ms=0)
        sources = [dict(file=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (radar, imu)]
    session.start(scenario)
    wait(session, lambda: session.pending is None and session.state == 'RUNNING')
    start = time.perf_counter()
    while session.world.t < duration and session.state == 'RUNNING' and not session.replay_finished:
        if time.perf_counter()-start > duration+5:
            raise AssertionError('Run exceeded wall deadline')
        session.step()
        target = start+session.world.t
        while time.perf_counter() < target:
            session.poll(); time.sleep(.001)
        session.poll()
        if session.state == 'ERROR' or link.error:
            raise AssertionError((session.events[-5:], link.error))
    if not session.replay_finished:
        session.command('SESSION_STOP','STOPPED')
    wait(session, lambda: session.pending is None and session.state == 'STOPPED')
    elapsed = time.perf_counter()-start
    session.log.flush()
    rows = [json.loads(line) for line in session.log_path.read_text(encoding='utf-8').splitlines()]
    results = [r['message'] for r in rows if r['direction']=='RX' and r['message']['type']=='RESULT']
    events = list(link.trace.events)
    rx = [e for e in events if e['stage']=='receive' and e['type']=='RESULT' and e['session']==session.envelope.session]
    tx = [e for e in events if e['stage']=='write']
    diags = [r['diag'] for r in results if 'diag' in r]
    gaps = [(b['pc_ns']-a['pc_ns'])/1e6 for a,b in zip(rx,rx[1:])]
    seq_gaps = sum(max(0,b['seq']-a['seq']-1) for a,b in zip(rx,rx[1:]))
    acks = [r['message']['command'] for r in rows if r['direction']=='RX' and r['message']['type']=='ACK']
    errors = [r['message'] for r in rows if r['direction']=='RX' and r['message']['type'] in ('ERROR','TRANSPORT_NOTICE')]
    live = [r for r in results if all(r['sensors'][s]['valid'] for s in ('IMU','LEFT','RIGHT'))
            and r['left']['reason_code'] not in ('NO_SESSION','PAUSED')]
    summary = dict(name=name, scenario=scenario, source='synthetic CSV pair; NOT proven synchronized capture' if csv_pair else 'synthetic dynamics',
                   sim_seconds=session.world.t, wall_seconds=elapsed, baud=BAUD, speed=1, rules=session.rules.copy(),
                   radar=vars(session.radar.config), imu_period_ms=session.imu_period_ms, sources=sources,
                   result_count=len(rx), result_gap_ms=percentiles(gaps), sequence_gaps=seq_gaps,
                   tx_count=len(tx), tx_bytes=sum(e['size'] for e in tx),
                   rx_result_bytes=sum(e['size'] for e in rx), queue_peak=max((e.get('depth',0) for e in events if e['stage']=='enqueue'),default=0),
                   trace_dropped=link.trace.dropped, acks=acks, errors=errors,
                   parser_errors=max((d['parser_errors'] for d in diags),default=None),
                   parse_max_us=max((d['parse_max_us'] for d in diags),default=None),
                   rx_slice_max_us=max((d['rx_slice_max_us'] for d in diags),default=None),
                   firmware_emit_gap_ms=percentiles([((b['emit_us']-a['emit_us'])&0xffffffff)/1000 for a,b in zip(diags,diags[1:])]),
                   levels={s:sorted({r[s]['level'] for r in live}) for s in ('left','right')},
                   motions=sorted({r['motion'] for r in live}), log=str(session.log_path))
    Path(session.log_dir, name+'-trace.json').write_text(json.dumps(events),encoding='utf-8')
    Path(session.log_dir, name+'-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    assert 'SESSION_START' in acks and 'SESSION_STOP' in acks, acks
    assert not errors and summary['parser_errors']==0, summary
    assert len(rx)>=elapsed*8 and not seq_gaps and link.trace.dropped==0, summary
    assert gaps and max(gaps)<200, summary  # experimental acceptance bound; not a changed timeout
    assert live, 'No fully valid sensor result'
    if name=='straight_target':
        assert summary['levels']['left']==['MONITOR'], summary
        assert summary['motions']==['STRAIGHT'], summary
    elif name=='left_turn_near':
        assert any(r['left']['level']=='HIGH' and r['motion']=='LEFT' and r.get('heading_deg',0)>0 for r in live), summary
    elif csv_pair:
        assert 'HIGH' in summary['levels']['left'], summary
    print(json.dumps(summary),flush=True)
    return summary


def run_ui(port, out):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from rideguard_sim.ui import Window
    app=QApplication([])
    window=Window(out); session=window.session
    session.link=SerialLink(port,BAUD); link=session.link
    window.show()
    started=False; stopping=False; failures=[]; deadline=time.perf_counter()+25
    def observe():
        nonlocal started,stopping
        try:
            if link.error or time.perf_counter()>deadline or session.state=='ERROR':
                raise AssertionError((link.error,session.events[-5:]))
            if not started and link.ready.is_set():
                session.start(0); started=True
            if started and session.world.t>=8 and not stopping and not session.pending:
                window.grab().save(str(out/'ui.png'))
                session.command('SESSION_STOP','STOPPED'); stopping=True
            if stopping and session.state=='STOPPED' and not session.pending:
                events=list(link.trace.events)
                rx=[e for e in events if e['type']=='RESULT' and e['stage']=='receive' and e['session']==session.envelope.session]
                received={e['seq']:e['pc_ns'] for e in rx}
                paint=[(e['pc_ns']-received[e['seq']])/1e6 for e in events if e['stage']=='paint' and e['seq'] in received]
                report=dict(result_count=len(rx),result_gap_ms=percentiles([(b['pc_ns']-a['pc_ns'])/1e6 for a,b in zip(rx,rx[1:])]),
                            rx_to_paint_ms=percentiles(paint),log=str(session.log_path))
                (out/'ui-summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                (out/'ui-trace.json').write_text(json.dumps(events),encoding='utf-8')
                assert len(rx)>=60 and paint, report
                assert report['result_gap_ms']['max']<200, report
                print(json.dumps(report),flush=True)
                timer.stop(); window.close(); app.quit()
        except Exception as error:
            failures.append(str(error)); timer.stop(); window.close(); app.quit()
    timer=QTimer(); timer.timeout.connect(observe); timer.start(5)
    app.exec()
    if failures:
        raise AssertionError(failures)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port',required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--ui-only',action='store_true')
    args=ap.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    if args.ui_only:
        return run_ui(args.port,args.out)
    session=Session(args.out); session.link=SerialLink(args.port,BAUD)
    reports=[]
    try:
        assert session.wait_link_ready(8) and not session.link.error, session.link.error
        for name,scenario,duration,pair in [('straight_target',2,6,False),('left_turn_near',0,8,False),
                                             ('csv_pair',0,45,True),('two_targets_1',5,6,False),
                                             ('two_targets_2',5,6,False),('two_targets_3',5,6,False)]:
            reports.append(exercise(session,name,scenario,duration,pair))
            (args.out/'summary.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    finally:
        session.close()


if __name__=='__main__':
    main()
