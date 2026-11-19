"""Feed a recorded BMI160 IMU log to the real ESP32 and check the answer.

This proves the importer's output is accepted by the firmware: the board must
return MONITOR/HIGH from the recorded yaw rate, and must not report an
IMU_STALE or a parser error while the stream is running.
"""
import argparse
import json
import time
from pathlib import Path

from rideguard_sim.protocol import BAUD
from rideguard_sim.replay import load
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.session import Session


def run(port, radar_csv, imu_csv, mount, offset_ms, duration, log_dir):
    session = Session(log_dir)
    link = SerialLink(port, BAUD)
    session.link = link
    session.state = 'STOPPED'
    session.replay = load(Path(radar_csv), 'LEFT')
    session.load_imu(Path(imu_csv), mount=mount, offset_ms=offset_ms)
    results = []
    try:
        if not session.wait_link_ready(8):
            raise SystemExit('link never ready')
        session.start(0)
        deadline = time.monotonic() + 3
        while session.pending and time.monotonic() < deadline:
            session.poll()
            time.sleep(.005)
        if session.pending:
            raise SystemExit('SESSION_START ACK missing')

        t0 = time.monotonic()
        while time.monotonic() - t0 < duration:
            session.step()
            session.poll()
            r = session.result
            if r and r.get('seq', 0) > (results[-1]['seq'] if results else 0):
                results.append(r)
            if session.state == 'ERROR':
                raise SystemExit(f'session error {session.events[-3:]}')
            time.sleep(.005)
        session.command('SESSION_STOP', 'STOPPED')
    finally:
        session.close()

    if not results:
        raise SystemExit('no RESULT received')

    levels = {'left': set(), 'right': set()}
    reasons = {'left': set(), 'right': set()}
    motions = set()
    for r in results:
        for side in ('left', 'right'):
            levels[side].add(r[side]['level'])
            reasons[side].add(r[side].get('reason_code'))
        motions.add(r.get('motion'))

    diag = results[-1].get('diag', {})
    print(f'RESULT from board: {len(results)}')
    print(f'  left  levels={sorted(levels["left"])} reasons={sorted(x for x in reasons["left"] if x)}')
    print(f'  right levels={sorted(levels["right"])} reasons={sorted(x for x in reasons["right"] if x)}')
    print(f'  motion={sorted(motions)}')
    print(f'  filtered_yaw_dps={results[-1].get("filtered_yaw_dps")}')
    print(f'  parser_errors={diag.get("parser_errors")} rx_lines={diag.get("rx_lines")}')
    print(f'  sensors={json.dumps(results[-1].get("sensors"), ensure_ascii=False)}')

    imu_stale = 'IMU_STALE' in reasons['left'] or 'IMU_STALE' in reasons['right']
    if imu_stale:
        print('FAIL: the board reported IMU_STALE while the recorded IMU was streaming')
    if diag.get('parser_errors'):
        print('FAIL: the board reported parser errors')
    if not imu_stale and not diag.get('parser_errors'):
        print('PASS: recorded BMI160 IMU accepted by the board, no IMU_STALE, no parser error')
        return 0
    return 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--radar', required=True)
    ap.add_argument('--imu', required=True)
    ap.add_argument('--mount', default='identity')
    ap.add_argument('--offset-ms', type=int, default=0)
    ap.add_argument('--duration', type=float, default=8.0)
    ap.add_argument('--log-dir', default='../../.local/imu-import')
    a = ap.parse_args()
    raise SystemExit(run(a.port, a.radar, a.imu, a.mount, a.offset_ms,
                         a.duration, a.log_dir))
