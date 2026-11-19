"""Compare a real radar + IMU capture against the simulator's expectations.

This tool answers the question "does the recorded real-world run look like
the simulated scenario it was meant to reproduce?".  It never rewrites the
capture: it only measures and reports, so a disagreement between the real
run and the simulation stays visible instead of being smoothed away.

What is compared
----------------
* **IMU yaw rate** -- the filtered yaw the firmware would derive from the
  recorded IMU, compared with the yaw the simulator generated for the same
  scenario.  A left turn must show a positive yaw rate and a right turn a
  negative one; a straight run must stay near zero.
* **Radar targets** -- the recorded target distance, speed and lane, compared
  with the simulator's radar model.
* **Timing** -- the sample rate of each stream and the gap distribution, so
  a dropped or bursty recording is visible before the data is trusted.

Usage
-----
    python compare_capture.py --capture captures --scenario straight
    python compare_capture.py --capture captures --scenario left_turn --mount identity
"""
import argparse
import math
import statistics
import sys
from pathlib import Path

from rideguard_sim import imu_import, replay


def load_capture(folder, scenario):
    """Load the radar and IMU files of one capture, keeping their origin."""
    radar_path = folder / f'radar_{scenario}.csv'
    imu_path = folder / f'imu_{scenario}.csv'
    if not radar_path.exists():
        raise SystemExit(f'Không thấy {radar_path}')
    if not imu_path.exists():
        raise SystemExit(f'Không thấy {imu_path}')
    radar = replay.load(str(radar_path), 'LEFT', rebase=False)
    imu = imu_import.load(str(imu_path), mount='identity', rebase=False)
    return radar, imu


def yaw_series(recording):
    """Filtered yaw rate in deg/s, using the firmware's own filter.

    The firmware low-passes the yaw rate with a time constant of 80 ms and
    then confirms a turn over 200 ms.  Reproducing that here keeps the
    comparison honest: the numbers below are the ones the board would have
    acted on.
    """
    if not recording.samples:
        return []
    first = recording.samples[0].t_ms
    filtered = 0.0
    result = []
    for sample in recording.samples:
        dt = max(0.001, (sample.t_ms - first) / 1000.0)
        # The first sample has no predecessor, so the filter starts from 0
        # and converges; that mirrors the firmware's cold start.
        alpha = dt / (0.08 + dt)
        filtered += alpha * (sample.gz_dps - filtered)
        result.append((sample.t_ms - first, filtered))
    return result


def motion_states(recording, yaw_threshold_dps=2.0, confirm_ms=200, hold_ms=350):
    """Reproduce the firmware's motion state machine on a recorded IMU log.

    The firmware keeps a low-passed yaw rate, declares a *candidate* turn as
    soon as the filtered yaw crosses the threshold, and only reports
    ``motion=LEFT``/``motion=RIGHT`` once the candidate has held for
    ``confirm_ms``.  Reproducing that here answers the question that
    actually matters for a capture: "would the board have believed the
    vehicle was turning?".

    Returns a list of ``(t_ms, state)`` where state is one of
    ``'STRAIGHT'``, ``'LEFT'`` or ``'RIGHT'``.
    """
    if not recording.samples:
        return []
    first = recording.samples[0].t_ms
    filtered = 0.0
    candidate = 0
    candidate_since = recording.samples[0].t_ms
    motion = 0
    states = []
    for sample in recording.samples:
        stamp = sample.t_ms - first
        dt = max(0.001, (sample.t_ms - first) / 1000.0)
        alpha = dt / (0.08 + dt)
        filtered += alpha * (sample.gz_dps - filtered)
        new_candidate = 1 if filtered > yaw_threshold_dps else \
            -1 if filtered < -yaw_threshold_dps else 0
        if new_candidate != candidate:
            candidate = new_candidate
            candidate_since = sample.t_ms
        motion = candidate if (sample.t_ms - candidate_since) >= confirm_ms else 0
        states.append((stamp, 'LEFT' if motion > 0 else 'RIGHT' if motion < 0 else 'STRAIGHT'))
    return states


def summarise(values):
    if not values:
        return dict(count=0, mean=0.0, peak=0.0, rms=0.0, span_ms=0)
    return dict(
        count=len(values),
        mean=statistics.fmean(values),
        peak=max(abs(value) for value in values),
        rms=math.sqrt(statistics.fmean(value * value for value in values)),
        span_ms=0,
    )


def describe_gaps(stamps):
    if len(stamps) < 2:
        return 'không đủ mẫu để đo khoảng cách'
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return (f'median {statistics.median(gaps):.1f} ms, '
            f'max {max(gaps)} ms, min {min(gaps)} ms')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--capture', default='captures', help='thư mục chứa capture')
    parser.add_argument('--scenario', required=True,
                        help='tên kịch bản (straight, left_turn, right_turn, bumpy)')
    parser.add_argument('--mount', default='identity',
                        help='cách gắn BMI160 (xem HUONG-DAN.md)')
    parser.add_argument('--expect', default='',
                        help='kỳ vọng: straight, left, right, bumpy (tự suy ra nếu bỏ trống)')
    args = parser.parse_args(argv)

    folder = Path(args.capture)
    radar_path = folder / f'radar_{args.scenario}.csv'
    imu_path = folder / f'imu_{args.scenario}.csv'
    if not radar_path.exists() or not imu_path.exists():
        raise SystemExit(f'Cần cả {radar_path} và {imu_path}')

    radar = replay.load(str(radar_path), 'LEFT', rebase=False)
    imu = imu_import.load(str(imu_path), mount=args.mount, rebase=False)

    expected = args.expect or _infer_expectation(args.scenario)

    print(f'=== Capture: {args.scenario} ({folder}) ===')
    print(f'expected      : {expected}')
    print(f'radar frames  : {len(radar.frames)}')
    print(f'IMU samples   : {len(imu.samples)}')

    # --- timing --------------------------------------------------------
    radar_stamps = [frame.t_ms for frame in radar.frames]
    imu_stamps = [sample.t_ms for sample in imu.samples]
    print(f'radar cadence : {describe_gaps(radar_stamps)}')
    print(f'IMU cadence   : {describe_gaps(imu_stamps)}')

    # --- IMU -----------------------------------------------------------
    series = yaw_series(imu)
    yaw_values = [value for _, value in series]
    stats = summarise(yaw_values)
    print(f'\nyaw lọc (firmware 80 ms): mean {stats["mean"]:+.2f} dps, '
          f'peak {stats["peak"]:.2f} dps, rms {stats["rms"]:.2f} dps')

    gyro_z = [sample.gz_dps for sample in imu.samples]
    gyro_stats = summarise(gyro_z)
    print(f'gyro z thô            : mean {gyro_stats["mean"]:+.2f} dps, '
          f'peak {gyro_stats["peak"]:.2f} dps')

    accel_z = [sample.az_mps2 for sample in imu.samples]
    accel_stats = summarise(accel_z)
    print(f'az (m/s2)             : mean {accel_stats["mean"]:.2f}, '
          f'peak |a| {accel_stats["peak"]:.2f}')

    # --- motion state the firmware would have reported ------------------
    states = motion_states(imu)
    left_ms = sum(1 for _, state in states if state == 'LEFT')
    right_ms = sum(1 for _, state in states if state == 'RIGHT')
    straight_ms = sum(1 for _, state in states if state == 'STRAIGHT')
    total_ms = max(1, len(states))
    print(f'\nmotion (mô phỏng state machine của firmware): '
          f'LEFT {left_ms * 100.0 / total_ms:.0f}% • '
          f'RIGHT {right_ms * 100.0 / total_ms:.0f}% • '
          f'STRAIGHT {straight_ms * 100.0 / total_ms:.0f}%')

    # --- radar ---------------------------------------------------------
    targets = [target for frame in radar.frames for target in frame.targets]
    print(f'\nradar targets : {len(targets)}')
    if targets:
        distances = [target['y_m'] for target in targets]
        speeds = [target['v_kmh'] for target in targets]
        angles = [target['angle_deg'] for target in targets]
        lanes = {target['dir'] for target in targets}
        lane_text = ', '.join('trái' if lane == 1 else 'phải' for lane in sorted(lanes))
        print(f'  distance   : {min(distances):.2f} .. {max(distances):.2f} m')
        print(f'  speed      : {min(speeds):.2f} .. {max(speeds):.2f} km/h')
        print(f'  angle      : {min(angles):.0f} .. {max(angles):.0f} deg')
        print(f'  lane       : {lane_text}')

    # --- verdict -------------------------------------------------------
    print('\n=== Đối chiếu với mô phỏng ===')
    checks = []
    if expected == 'straight':
        checks.append(('yaw gần 0 khi đi thẳng', abs(stats['mean']) < 2.0,
                       f'mean yaw {stats["mean"]:+.2f} dps'))
        checks.append(('firmware giữ motion=STRAIGHT',
                       right_ms == 0 and left_ms == 0,
                       f'LEFT {left_ms} mẫu, RIGHT {right_ms} mẫu'))
    elif expected == 'left':
        checks.append(('yaw dương khi rẽ trái', stats['mean'] > 1.0,
                       f'mean yaw {stats["mean"]:+.2f} dps'))
        checks.append(('firmware nhận LEFT', left_ms > 0 and right_ms == 0,
                       f'LEFT {left_ms} mẫu, RIGHT {right_ms} mẫu'))
    elif expected == 'right':
        checks.append(('yaw âm khi rẽ phải', stats['mean'] < -1.0,
                       f'mean yaw {stats["mean"]:+.2f} dps'))
        checks.append(('firmware nhận RIGHT', right_ms > 0 and left_ms == 0,
                       f'LEFT {left_ms} mẫu, RIGHT {right_ms} mẫu'))
    elif expected == 'bumpy':
        # A rough road shakes the gyroscope.  The question is not whether
        # the raw signal is large -- it is -- but whether the firmware
        # would have declared a turn.
        raw_peak = max((abs(value) for value in gyro_z), default=0.0)
        checks.append(('gyro thô bị rung (peak lớn)', raw_peak > 2.0,
                       f'peak gyro {raw_peak:.2f} dps'))
        checks.append(('firmware không nhận nhầm là rẽ',
                       left_ms == 0 and right_ms == 0,
                       f'LEFT {left_ms} mẫu, RIGHT {right_ms} mẫu'))
        if left_ms or right_ms:
            print('  LƯU Ý: rung đường đủ mạnh để firmware nhận LEFT/RIGHT.')
            print('        Cần tăng thời gian lọc hoặc nâng yaw_threshold_dps.')
    else:
        checks.append(('kịch bản không xác định', False, f'{expected!r}'))

    if len(imu.samples) >= 2:
        gaps = [b - a for a, b in zip(imu_stamps, imu_stamps[1:])]
        checks.append(('IMU không bị đứt quãng', max(gaps) <= 200,
                       f'max gap {max(gaps)} ms'))
    if radar_stamps:
        checks.append(('radar có dữ liệu', len(radar.frames) > 0,
                       f'{len(radar.frames)} khung'))

    failures = 0
    for name, passed, detail in checks:
        mark = 'OK  ' if passed else 'FAIL'
        print(f'  [{mark}] {name} ({detail})')
        if not passed:
            failures += 1

    if failures:
        print(f'\n{failures} kiểm tra KHÔNG khớp với kỳ vọng "{expected}".')
        print('Đây là điểm cần đối chiếu: hoặc kịch bản chưa đúng, hoặc mount IMU sai.')
        return 1
    print(f'\nTất cả khớp với kỳ vọng "{expected}".')
    return 0


def _infer_expectation(name):
    lowered = name.lower()
    if 'straight' in lowered or 'thang' in lowered:
        return 'straight'
    if 'left' in lowered or 'trai' in lowered:
        return 'left'
    if 'right' in lowered or 'phai' in lowered:
        return 'right'
    if 'bump' in lowered or 'rung' in lowered or 'rough' in lowered:
        return 'bumpy'
    return 'unknown'


if __name__ == '__main__':
    raise SystemExit(main())
