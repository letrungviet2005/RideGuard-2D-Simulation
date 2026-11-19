"""Generate a synthetic BMI160-format IMU log for testing the importer.

The file is *simulated* and is not measured data.  It exists so the importer
can be exercised without a physical BMI160; it must not be presented as a
recording from the vehicle.
"""
import argparse
import csv
import math
from pathlib import Path

HEADER = ['timestamp_us', 'ax_raw', 'ay_raw', 'az_raw',
          'gx_raw', 'gy_raw', 'gz_raw',
          'ax_g', 'ay_g', 'az_g', 'gx_dps', 'gy_dps', 'gz_dps',
          'temperature_C']


def generate(duration_s=10.0, rate_hz=100.0, yaw_dps=8.0, start_us=123456789):
    """Generate a log that turns gently to the left.

    The accelerometer shows the gravity component in the vehicle frame plus a
    small centripetal term, so it is physically plausible rather than random.
    """
    step_us = int(1_000_000 / rate_hz)
    total = int(duration_s * rate_hz)
    rows = []
    yaw_rate = math.radians(yaw_dps)
    for index in range(total + 1):
        t = index / rate_hz
        timestamp_us = start_us + int(index * step_us)
        heading = yaw_rate * t
        # Gravity measured in the body frame: Z up, so az = +g while level.
        az_g = 1.0
        ax_g = 0.0
        ay_g = 0.0
        # A small centripetal acceleration while turning.
        ay_g += 0.05 * math.sin(heading)
        gz_dps = yaw_dps
        # RAW columns are LSB counts with an assumed, *documented* scale so
        # the sample is self-consistent.  The importer must not rely on them.
        ax_raw = int(round(ax_g * 16384))
        ay_raw = int(round(ay_g * 16384))
        az_raw = int(round(az_g * 16384))
        gx_raw = 0
        gy_raw = 0
        gz_raw = int(round(gz_dps * 16.4))
        rows.append([timestamp_us, ax_raw, ay_raw, az_raw,
                     gx_raw, gy_raw, gz_raw,
                     f'{ax_g:.6f}', f'{ay_g:.6f}', f'{az_g:.6f}',
                     '0.000000', '0.000000', f'{gz_dps:.6f}',
                     '31.5'])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='samples/generated/imu_bmi160_synthetic.csv')
    parser.add_argument('--duration', type=float, default=10.0)
    parser.add_argument('--rate', type=float, default=100.0)
    parser.add_argument('--yaw-dps', type=float, default=8.0)
    args = parser.parse_args()

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(generate(args.duration, args.rate, args.yaw_dps))
    print(f'{path}  ({int(args.duration * args.rate) + 1} samples)')


if __name__ == '__main__':
    main()
