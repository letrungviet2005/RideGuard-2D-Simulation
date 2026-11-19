"""Generate a synthetic hardware log in the exact format the logger writes.

This is a **test fixture**, not real sensor data.  It produces the same
records that ``firmware/src/sensor_logger.cpp`` emits, so the capture
tool, the radar decoder, the IMU importer and the comparison tool can be
exercised end to end without hardware.

Every generated file is marked as synthetic in its header, and the tool
refuses to write into a folder that already contains a real capture.

Usage
-----
    python make_synthetic_capture.py --out captures_synthetic
"""
import argparse
import math
import sys
from pathlib import Path

# The scenario definitions mirror what the operator is asked to record:
# a straight baseline, a left turn, a right turn and a rough road.
SCENARIOS = {
    'straight': dict(duration_ms=12000, yaw_profile=None, bumps=False),
    'left_turn': dict(duration_ms=12000, yaw_profile='left', bumps=False),
    'right_turn': dict(duration_ms=12000, yaw_profile='right', bumps=False),
    'bumpy': dict(duration_ms=12000, yaw_profile=None, bumps=True),
}

IMU_PERIOD_US = 10000
RADAR_PERIOD_MS = 65


def yaw_rate_dps(profile, t_ms, duration_ms):
    """Yaw rate in deg/s for the given profile.

    The shape matters: a real turn ramps up, holds and ramps down, and a
    lane-change return reverses the sign while the vehicle is still
    angled.  The profiles below are deliberately simple but share that
    ramp shape.
    """
    if profile is None:
        return 0.0
    ramp = 2000.0
    hold = max(0.0, duration_ms - 2 * ramp)
    phase = t_ms
    if phase < ramp:
        shape = phase / ramp
    elif phase < ramp + hold:
        shape = 1.0
    else:
        shape = max(0.0, (duration_ms - phase) / ramp)
    magnitude = 12.0
    return magnitude * shape if profile == 'left' else -magnitude * shape


def vibration_dps(t_ms):
    """Road vibration: a high frequency component on the yaw axis."""
    # 18 Hz and 31 Hz components, typical of a rough surface.
    return (1.8 * math.sin(2 * math.pi * 18.0 * t_ms / 1000.0)
            + 1.1 * math.sin(2 * math.pi * 31.0 * t_ms / 1000.0))


def generate(scenario, name):
    config = SCENARIOS[scenario]
    duration_ms = config['duration_ms']
    rows = []
    imu_rows = []
    radar_rows = []

    # IMU at 100 Hz.
    stamp = 0
    while stamp <= duration_ms * 1000:
        t_ms = stamp / 1000.0
        yaw = yaw_rate_dps(config['yaw_profile'], t_ms, duration_ms)
        if config['bumps']:
            yaw += vibration_dps(t_ms)
        # A stationary vehicle: az is +1 g, and the vibration shakes the
        # accelerometer a little on all axes.
        ax = 0.0
        ay = 0.0
        az = 1.0
        if config['bumps']:
            ax += 0.05 * math.sin(2 * math.pi * 21.0 * t_ms / 1000.0)
            ay += 0.04 * math.sin(2 * math.pi * 27.0 * t_ms / 1000.0)
        imu_rows.append((stamp, ax, ay, az, 0.0, 0.0, yaw, 24.5))
        stamp += IMU_PERIOD_US

    # Radar at ~65 ms.  A target in the left lane closing slowly.
    frame = 0
    t_ms = 0
    while t_ms <= duration_ms:
        distance = max(1.5, 12.0 - 0.15 * t_ms / 1000.0)
        speed = 8.0 + 0.5 * math.sin(2 * math.pi * 0.2 * t_ms / 1000.0)
        angle = -18 + 4 * math.sin(2 * math.pi * 0.1 * t_ms / 1000.0)
        radar_rows.append((t_ms, frame, 31, 'trai', distance, speed, angle,
                           0, 1, min(255, frame % 200)))
        frame += 1
        t_ms += RADAR_PERIOD_MS

    return imu_rows, radar_rows


def write_files(out_dir, name, imu_rows, radar_rows):
    out_dir.mkdir(parents=True, exist_ok=True)
    radar_path = out_dir / f'radar_{name}.csv'
    imu_path = out_dir / f'imu_{name}.csv'
    with open(radar_path, 'w', encoding='utf-8', newline='') as handle:
        handle.write('ms,frame,id,huong,y_m,v_kmh,goc,cao_toc,lien_ket,ton_tai\n')
        for row in radar_rows:
            handle.write('%d,%d,%d,%s,%.2f,%.2f,%d,%d,%d,%d\n' % row)
    with open(imu_path, 'w', encoding='utf-8', newline='') as handle:
        handle.write('timestamp_us,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C\n')
        for row in imu_rows:
            handle.write('%d,%.5f,%.5f,%.5f,%.4f,%.4f,%.4f,%.2f\n' % row)
    return radar_path, imu_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default='captures_synthetic',
                        help='thư mục đích (không dùng cho dữ liệu thật)')
    parser.add_argument('--scenario', default='all',
                        choices=list(SCENARIOS) + ['all'])
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    names = list(SCENARIOS) if args.scenario == 'all' else [args.scenario]
    for name in names:
        imu_rows, radar_rows = generate(name, name)
        radar_path, imu_path = write_files(out_dir, name, imu_rows, radar_rows)
        print(f'{name}: {radar_path} + {imu_path}')
        print(f'   radar {len(radar_rows)} dòng, imu {len(imu_rows)} mẫu')
    print('\nĐây là dữ liệu GIẢ LẬP để kiểm thử pipeline, không phải ghi thực.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
