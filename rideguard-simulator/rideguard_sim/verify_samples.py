"""End-to-end check: replay a generated CSV through the real ESP32 and report
the levels the board actually returns.

This drives the same ``Session`` replay path as the GUI, so the pacing, the
freshness window and the control handshake are identical to normal use.

Usage:
    python -m rideguard_sim.verify_samples samples/generated/left_approach_45s_dual.csv --port COM5
"""
import argparse
import time
from pathlib import Path

from rideguard_sim.replay import load
from rideguard_sim.session import Session
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.protocol import BAUD


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('csv')
    parser.add_argument('--port', required=True)
    parser.add_argument('--radar-id', default='LEFT')
    parser.add_argument('--log-dir', default='.local/simulator-logs')
    args = parser.parse_args()

    rec = load(Path(args.csv), args.radar_id)
    session = Session(Path(args.log_dir))
    session.replay = rec
    # The firmware needs a live IMU stream: without it every result is
    # UNKNOWN/IMU_STALE.  The synthetic IMU is not synchronised with the
    # recording, which the UI states explicitly.
    session.replay_imu = True
    session.link = SerialLink(args.port, BAUD)
    session.state = 'STOPPED'
    try:
        if not session.wait_link_ready(8):
            raise TimeoutError('link never ready')
        session.start(0)
        # Wait for the SESSION_START ACK.
        deadline = time.monotonic() + 3
        while session.pending and time.monotonic() < deadline:
            session.poll()
            time.sleep(.01)
        if session.pending:
            raise TimeoutError('SESSION_START ACK missing')

        # Drive the replay on the simulation clock, like the GUI tick does.
        seen = []
        duration = rec.frames[-1].t_ms / 1000.0
        end = time.monotonic() + duration + 1.5
        while time.monotonic() < end:
            session.step()
            session.poll()
            if session.state == 'ERROR':
                raise RuntimeError(f'session error: {session.events[-3:]}')
            if session.result:
                seen.append(session.result)
            time.sleep(.01)
    finally:
        session.close()

    if not seen:
        raise SystemExit('FAIL: board returned no RESULT')
    levels, reasons = {}, {}
    for r in seen:
        for side in ('left', 'right'):
            levels.setdefault(side, set()).add(r[side]['level'])
            reasons.setdefault(side, set()).add(r[side].get('reason_code'))
    motions = {r.get('motion') for r in seen}
    print(f'PASS {Path(args.csv).name}: {len(seen)} RESULT from board')
    print(f'  levels  left={sorted(levels["left"])}  right={sorted(levels["right"])}')
    print(f'  reasons left={sorted(x for x in reasons["left"] if x)}')
    print(f'  reasons right={sorted(x for x in reasons["right"] if x)}')
    print(f'  motion  {sorted(motions)}')


if __name__ == '__main__':
    main()
