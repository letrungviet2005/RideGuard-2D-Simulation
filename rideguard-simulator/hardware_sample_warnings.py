"""Audit existing sample files against a real ESP32; no fabricated RESULT.

Radar CSV is replayed unchanged. IMU is explicitly synthetic and not a
measurement synchronized with that recording. Steer only after a straight
baseline, so receiving JSON alone cannot pass the warning assertion.
"""
import argparse
import json
from pathlib import Path
import time

from hardware_acceptance import wait
from rideguard_sim.protocol import BAUD
from rideguard_sim.replay import load
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.session import Session


def exercise(session, path):
    recording = load(path, 'LEFT')
    session.replay = recording
    session.replay_imu = True
    session.start()
    wait(session, lambda: session.pending is None and session.state == 'RUNNING')
    session.record('META', {'type': 'AUDIT_PLAN', 'source': 'existing synthetic radar CSV + synthetic IMU',
                            'steer_left_at_ms': 14000, 'lane_delta_m': 3.5, 'lane_duration_s': 4})
    start = time.monotonic()
    deadline = start + recording.frames[-1].t_ms / 1000 + 8
    results = []
    last_seq = 0
    steered = False
    while not session.replay_finished:
        if time.monotonic() > deadline:
            raise TimeoutError('Replay did not finish')
        if session.world.t >= 14 and not steered:
            session.world.ego.change_lane(3.5, 4)
            steered = True
        next_tick = start + session.world.t + .01
        session.step()
        while time.monotonic() < next_tick:
            session.poll()
            time.sleep(.001)
        session.poll()
        if session.state == 'ERROR' or not session.link or session.link.error:
            raise AssertionError(session.events[-5:])
        result = session.visible_result()
        if result and result['seq'] > last_seq:
            results.append(result)
            last_seq = result['seq']
    wait(session, lambda: session.pending is None and session.state == 'STOPPED')
    baseline = [r for r in results if 1000 <= r['t_ms'] < 14000]
    turn = [r for r in results if 14000 <= r['t_ms'] < 18000]
    assert baseline and turn, 'Missing baseline/turn observations'
    assert all(r['left']['level'] == 'MONITOR' for r in baseline), 'Straight baseline must monitor left targets'
    assert all(r['right']['level'] != 'HIGH' for r in baseline), 'No turn in baseline'
    high = [r for r in turn if r['left']['level'] == 'HIGH']
    assert high, 'No real left HIGH when near target + synthetic left turn'
    assert all(r['left']['reason_code'] == 'TURN_TOWARD_NEAR_TARGET' for r in high)
    # Do not drop UNKNOWN when all sensor valid flags become false mid-run.
    active = [r for r in results if 1000 <= r['t_ms'] < recording.frames[-1].t_ms]
    assert all(r[side]['level'] != 'UNKNOWN' for r in active for side in ('left','right')), 'Unexpected stale/error mid-run'
    return dict(file=path.name, input_frames=len(recording.frames), duration_ms=recording.frames[-1].t_ms,
                unique_result_frames=len(results), baseline_result_frames=len(baseline),
                left_high_result_frames=len(high), first_high=high[0], log=str(session.log_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--samples', type=Path, default=Path(__file__).parent.parent/'samples/generated')
    parser.add_argument('--log-dir', type=Path, default=Path(__file__).resolve().parents[2]/'.local/simulator-sample-audit')
    args = parser.parse_args()
    files = sorted(args.samples.glob('*.csv'))
    if not files:
        raise ValueError('No sample CSV files found')
    session = Session(args.log_dir)
    session.link = SerialLink(args.port, BAUD)
    report = []
    try:
        if not session.wait_link_ready(8) or session.link.error:
            raise RuntimeError(session.link.error or 'Serial not ready')
        for path in files:
            summary = exercise(session, path)
            report.append(summary)
            (args.log_dir/'sample-warning-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"PASS {path.name}: {summary['unique_result_frames']} unique RESULT; "
                  f"left HIGH={summary['left_high_result_frames']}; first HIGH t={summary['first_high']['t_ms']} ms", flush=True)
        print(f'PASS {len(report)}/{len(files)} sample warning checks', flush=True)
    finally:
        session.close()


if __name__ == '__main__':
    main()
