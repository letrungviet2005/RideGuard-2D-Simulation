"""Trace the yaw rate, the filtered motion and the warning level through a
lane change, on the real ESP32.

The question under test: while the ego changes lane and then straightens
out, does the board raise a warning merely because the gyro changed sign?

The board is the only source of the warning level; this script never
predicts a level.  It records what the board actually reported.
"""
import argparse
import json
import time
from pathlib import Path

from rideguard_sim.protocol import BAUD
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.session import Session


def run(port, scenario, duration, log_dir):
    session = Session(log_dir)
    link = SerialLink(port, BAUD)
    session.link = link
    session.state = 'STOPPED'
    rows = []
    try:
        if not session.wait_link_ready(8):
            raise SystemExit('link never ready')
        session.start(scenario)
        deadline = time.monotonic() + 3
        while session.pending and time.monotonic() < deadline:
            session.poll()
            time.sleep(.005)
        if session.pending:
            raise SystemExit('SESSION_START ACK missing')

        t0 = time.monotonic()
        last_seq = 0
        while time.monotonic() - t0 < duration:
            session.step()
            session.poll()
            r = session.result
            if r and r.get('seq', 0) > last_seq:
                last_seq = r['seq']
                sent = session.last_sent.get('IMU', {})
                rows.append(dict(
                    t_ms=r['t_ms'], seq=r['seq'], motion=r.get('motion'),
                    yaw=round(r.get('filtered_yaw_dps', 0.0), 3),
                    heading=round(r.get('heading_deg', 0.0), 3),
                    left=r['left']['level'], left_reason=r['left']['reason_code'],
                    right=r['right']['level'], right_reason=r['right']['reason_code'],
                    imu_gz=sent.get('gz_dps'),
                    near=[(t['radar_id'], t['track_id'], round(t['y_m'], 2), t['side'])
                          for t in r.get('targets', []) if t['y_m'] <= 18.0],
                ))
            if session.state == 'ERROR':
                raise SystemExit(f'session error {session.events[-3:]}')
            time.sleep(.005)
        session.command('SESSION_STOP', 'STOPPED')
    finally:
        session.close()

    if not rows:
        raise SystemExit('no RESULT received')

    print(f'=== scenario {scenario}: motion / yaw / heading / warning ===')
    print(f'{"t_ms":>6} {"motion":>9} {"yaw_dps":>8} {"head_deg":>9} {"gz":>7} '
          f'{"left":>8} {"right":>8}  near targets')
    for row in rows:
        gz = row['imu_gz']
        gz_text = f'{gz:7.3f}' if isinstance(gz, (int, float)) else '      -'
        near = ','.join(f'{radar}:{tid}@{y}m/{side}' for radar, tid, y, side in row['near'])
        print(f'{row["t_ms"]:>6} {row["motion"]:>9} {row["yaw"]:>8.3f} '
              f'{row["heading"]:>9.3f} {gz_text} '
              f'{row["left"]:>8} {row["right"]:>8}  {near}')

    # --- the question: did a warning start only because the gyro flipped? --
    print()
    for side in ('left', 'right'):
        rises = []
        for previous, current in zip(rows, rows[1:]):
            if previous[side] != 'HIGH' and current[side] == 'HIGH':
                rises.append(current)
        print(f'{side}: {len(rises)} lan chuyen sang HIGH')
        for row in rises:
            index = rows.index(row)
            history = rows[max(0, index - 8):index + 1]
            signs = [h['yaw'] for h in history]
            flipped = any(a * b < 0 for a, b in zip(signs, signs[1:]))
            print(f'   t={row["t_ms"]} motion={row["motion"]} yaw={row["yaw"]} '
                  f'heading={row["heading"]} '
                  f'gyro doi dau trong 800 ms truoc: '
                  f'{"CO" if flipped else "KHONG"} | near: {row["near"]}')

    # --- the rule that matters: a warning needs the vehicle to be angled
    #     towards the warned side, not merely a gyro of the matching sign.
    print()
    print('kiem tra quy tac: HIGH phai co heading cung dau voi phia canh bao')
    violations = []
    for row in rows:
        for side in ('left', 'right'):
            if row[side] != 'HIGH':
                continue
            heading = row['heading']
            if side == 'left' and heading <= 0:
                violations.append((row['t_ms'], side, heading))
            if side == 'right' and heading >= 0:
                violations.append((row['t_ms'], side, heading))
    if violations:
        print(f'   VI PHAM: {len(violations)} frame HIGH ma heading khong cung dau')
        for t_ms, side, heading in violations[:6]:
            print(f'      t={t_ms} {side} heading={heading}')
    else:
        print('   OK: khong co frame HIGH nao ma heading khong cung dau phia')

    out = dict(scenario=scenario, rows=rows)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    Path(log_dir, f'lanechange_s{scenario}.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--scenario', type=int, default=0)
    ap.add_argument('--duration', type=float, default=8.0)
    ap.add_argument('--log-dir', default='../../.local/lanechange')
    a = ap.parse_args()
    run(a.port, a.scenario, a.duration, a.log_dir)
