"""Offline check that generated recordings are loadable and internally consistent.

Verifies the CSV against every rule enforced by ``replay.load`` and against the
numeric bounds the firmware enforces on RADAR_FRAME targets.
"""
import math
from pathlib import Path

from rideguard_sim.replay import load, coordinates


def check(path, radar_id='LEFT'):
    rec = load(path, radar_id)
    rows = Path(path).read_text(encoding='utf-8-sig').splitlines()
    header = rows[0].split(',')
    data = [r for r in rows[1:] if r.strip()]

    problems = []
    # Every data row must be a complete frame in the log format.
    frames = rec.frames
    if len(data) != sum(len(f.targets) for f in frames):
        problems.append('row count does not match parsed target count')

    # Per-frame checks that mirror the firmware's target validation.
    for frame in frames:
        if len(frame.targets) > 4:
            problems.append(f'frame {frame.frame}: {len(frame.targets)} targets > 4')
        ids = [t['track_id'] for t in frame.targets]
        if len(set(ids)) != len(ids):
            problems.append(f'frame {frame.frame}: duplicate track_id')
        for t in frame.targets:
            if not (0 <= t['track_id'] <= 255):
                problems.append(f'track_id out of range: {t["track_id"]}')
            if not (0.001 <= t['y_m'] <= 150):
                problems.append(f'y_m out of range: {t["y_m"]}')
            if not (0 <= t['v_kmh'] <= 655.35):
                problems.append(f'v_kmh out of range: {t["v_kmh"]}')
            if not (-80 <= t['angle_deg'] <= 80):
                problems.append(f'angle_deg out of range: {t["angle_deg"]}')
            if t['high_speed'] not in (0, 1):
                problems.append(f'high_speed invalid: {t["high_speed"]}')
            if t['associated'] not in (0, 1):
                problems.append(f'associated invalid: {t["associated"]}')
            if not (0 <= t['duration_frames'] <= 255):
                problems.append(f'duration_frames out of range: {t["duration_frames"]}')
            if not all(math.isfinite(v) for v in t.values()):
                problems.append('non-finite value')

    # Timestamps must strictly increase *per radar*, which is what the parser
    # enforces.  The two radars share a timestamp in a dual file, so a global
    # comparison would report a false failure.
    previous = {}
    for frame in frames:
        last = previous.get(frame.radar_id)
        if last is not None and frame.t_ms <= last:
            problems.append(f'radar {frame.radar_id}: t_ms not strictly increasing')
        previous[frame.radar_id] = frame.t_ms

    # Provenance columns must agree with the documented relation.  The optional
    # ``radar_id`` column shifts the logger columns by one.
    offset = 1 if 'radar_id' in header else 0
    for line, frame in zip(data, [f for f in frames for _ in f.targets]):
        cols = line.split(',')
        if len(cols) != len(header):
            problems.append(f'column count mismatch: {line}')
            continue
        y_m = float(cols[5 + offset])
        angle = float(cols[7 + offset])
        x_m = float(cols[11 + offset])
        fwd_m = float(cols[12 + offset])
        expect_x = y_m * math.sin(math.radians(angle))
        expect_f = y_m * math.cos(math.radians(angle))
        if abs(x_m - expect_x) > 0.02:
            problems.append(f'x_m={x_m} != y*sin(angle)={expect_x:.2f}')
        if abs(fwd_m - expect_f) > 0.02:
            problems.append(f'fwd_m={fwd_m} != y*cos(angle)={expect_f:.2f}')

    # The parser's own coordinate interpretation.
    for frame in frames:
        for t in frame.targets:
            forward, lateral = coordinates(t)
            if abs(forward - t['y_m']) > 1e-9:
                problems.append('coordinates() forward mismatch')
            expected = t['y_m'] * math.tan(math.radians(t['angle_deg']))
            if abs(lateral - expected) > 1e-9:
                problems.append('coordinates() lateral mismatch')

    ids = sorted({t['track_id'] for f in frames for t in f.targets})
    max_per = max(len(f.targets) for f in frames)
    return {
        'frames': len(frames),
        'span_ms': max((f.t_ms for f in frames), default=0),
        'ids': ids,
        'max_targets_per_frame': max_per,
        'problems': problems,
    }


if __name__ == '__main__':
    import sys
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else Path(__file__).resolve().parents[2] / 'samples' / 'generated')
    failed = False
    for path in sorted(root.glob('*.csv')):
        info = check(path)
        status = 'OK  ' if not info['problems'] else 'FAIL'
        print(f"{status} {path.name}")
        print(f"     frames={info['frames']} span={info['span_ms']} ms "
              f"ids={info['ids']} max_targets/frame={info['max_targets_per_frame']}")
        for problem in info['problems'][:10]:
            print(f'     ! {problem}')
            failed = True
    raise SystemExit(1 if failed else 0)
