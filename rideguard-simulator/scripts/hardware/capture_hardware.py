"""Capture a synchronised LD2417 + BMI160 log and split it for the simulator.

The ``sensor_logger`` firmware writes one text stream where every record is a
single line::

    RADAR,<ms>,<frame>,<id>,<huong>,<y_m>,<v_kmh>,<goc>,<cao_toc>,<lien_ket>,<ton_tai>
    IMU,<timestamp_us>,<ax_g>,<ay_g>,<az_g>,<gx_dps>,<gy_dps>,<gz_dps>,<temperature_C>
    SCENARIO,<timestamp_us>,<start|end>
    # ... free-form comments ...

Both streams are stamped by the **same** ESP32 ``micros()`` counter, so the
two files this tool writes are already aligned: the radar ``ms`` column and
the IMU ``timestamp_us`` column share one origin.  The desktop simulator can
therefore replay them together with an IMU offset of 0.

Outputs
-------
``<out>/radar_<scenario>.csv``
    Hi-Link logger format, one row per target::

        ms,frame,id,huong,y_m,v_kmh,goc,cao_toc,lien_ket,ton_tai

``<out>/imu_<scenario>.csv``
    BMI160 importer schema, one row per sample::

        timestamp_us,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C

Usage
-----
    python capture_hardware.py --port COM5 --out captures --duration 60

The tool records until ``--duration`` seconds have elapsed or the operator
presses Enter.  Press ``s``/``e`` in the terminal while recording to mark a
scenario start/end; each marked interval becomes its own pair of CSV files.
"""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import csv
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError:  # pragma: no cover - dependency is declared in requirements
    print('Thiếu pyserial: pip install -r requirements.txt', file=sys.stderr)
    raise

RADAR_HEADER = ['ms', 'frame', 'id', 'huong', 'y_m', 'v_kmh', 'goc',
                 'cao_toc', 'lien_ket', 'ton_tai']
IMU_HEADER = ['timestamp_us', 'ax_g', 'ay_g', 'az_g', 'gx_dps', 'gy_dps',
              'gz_dps', 'temperature_C']

# The logger writes these in this order; the desktop parser expects the
# integer fields to be integers and the rest to be plain decimals.
RADAR_INT_FIELDS = {'ms', 'frame', 'id', 'cao_toc', 'lien_ket', 'ton_tai'}
IMU_INT_FIELDS = {'timestamp_us'}


class Capture:
    """Parses the logger stream into radar and IMU records."""

    def __init__(self):
        self.radar = []       # list of lists, RADAR_HEADER order
        self.imu = []         # list of lists, IMU_HEADER order
        self.scenarios = []   # list of (start_us, end_us, name)
        self._pending = None  # (start_us, name)
        self._counter = 0
        self.comments = []
        self.unknown = []

    # -- parsing ---------------------------------------------------------
    def feed_line(self, line):
        line = line.strip()
        if not line:
            return
        if line.startswith('#'):
            self.comments.append(line)
            return
        parts = line.split(',')
        kind = parts[0]
        if kind == 'RADAR':
            self._add_radar(parts[1:])
        elif kind == 'IMU':
            self._add_imu(parts[1:])
        elif kind == 'SCENARIO':
            self._add_scenario(parts[1:])
        else:
            self.unknown.append(line)

    def _add_radar(self, fields):
        if len(fields) != len(RADAR_HEADER):
            self.unknown.append('RADAR,' + ','.join(fields))
            return
        row = []
        for name, value in zip(RADAR_HEADER, fields):
            row.append(int(value) if name in RADAR_INT_FIELDS else value)
        self.radar.append(row)

    def _add_imu(self, fields):
        if len(fields) != len(IMU_HEADER):
            self.unknown.append('IMU,' + ','.join(fields))
            return
        row = []
        for name, value in zip(IMU_HEADER, fields):
            row.append(int(value) if name in IMU_INT_FIELDS else value)
        self.imu.append(row)

    def _add_scenario(self, fields):
        if len(fields) != 2:
            self.unknown.append('SCENARIO,' + ','.join(fields))
            return
        stamp = int(fields[0])
        action = fields[1].strip().lower()
        if action == 'start':
            self._pending = (stamp, f'scenario_{self._counter:02d}')
            self._counter += 1
        elif action == 'end' and self._pending is not None:
            start, name = self._pending
            self.scenarios.append((start, stamp, name))
            self._pending = None

    # -- output ----------------------------------------------------------
    def validate(self):
        """Check the capture against the rules the desktop parser enforces.

        The desktop parser refuses a recording with duplicate target ids
        inside one frame, with more than four targets, or with a
        non-increasing timestamp.  Finding that on the PC right after the
        capture is much cheaper than finding it when the replay silently
        refuses to start.
        """
        problems = []
        by_frame = {}
        for row in self.radar:
            key = (row[RADAR_HEADER.index('ms')], row[RADAR_HEADER.index('frame')])
            by_frame.setdefault(key, []).append(row)
        for key, rows in by_frame.items():
            ids = [row[RADAR_HEADER.index('id')] for row in rows]
            if len(rows) > 4:
                problems.append(f'khung {key} có {len(rows)} target (>4)')
            if len(set(ids)) != len(ids):
                problems.append(f'khung {key} có id trùng: {sorted(ids)}')
        stamps = [row[RADAR_HEADER.index('ms')] for row in self.radar]
        for previous, current in zip(stamps, stamps[1:]):
            if current < previous:
                problems.append(f'timestamp radar giảm: {previous} -> {current}')
                break
        imu_stamps = [row[IMU_HEADER.index('timestamp_us')] for row in self.imu]
        for previous, current in zip(imu_stamps, imu_stamps[1:]):
            if current < previous:
                problems.append(f'timestamp IMU giảm: {previous} -> {current}')
                break
        return problems

    def stats(self):
        radar_ms = [row[0] for row in self.radar]
        imu_us = [row[0] for row in self.imu]
        return {
            'radar_rows': len(self.radar),
            'imu_rows': len(self.imu),
            'radar_span_ms': (max(radar_ms) - min(radar_ms)) if radar_ms else 0,
            'imu_span_ms': ((max(imu_us) - min(imu_us)) // 1000) if imu_us else 0,
            'scenarios': len(self.scenarios),
            'unknown': len(self.unknown),
        }


def write_csv(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_scenario_files(out_dir, name, radar_rows, imu_rows):
    """Write one scenario as a radar CSV plus an IMU CSV.

    Both files keep the timestamps recorded by the firmware, so the radar
    ``ms`` column and the IMU ``timestamp_us`` column share one origin.  The
    desktop app loads them with ``rebase=False`` and aligns the two streams
    with the IMU offset; if this tool rebased each file independently, the
    two streams would be shifted apart by the distance between their first
    rows.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    radar_path = out_dir / f'radar_{name}.csv'
    imu_path = out_dir / f'imu_{name}.csv'
    write_csv(radar_path, RADAR_HEADER, radar_rows)
    write_csv(imu_path, IMU_HEADER, imu_rows)
    return radar_path, imu_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', default='COM5', help='serial port of the ESP32')
    parser.add_argument('--baud', type=int, default=921600, help='logger baud rate')
    parser.add_argument('--out', default='captures', help='output directory')
    parser.add_argument('--duration', type=float, default=60.0,
                        help='maximum recording time in seconds (0 = until Ctrl+C)')
    parser.add_argument('--scenario', default='session',
                        help='name used when no SCENARIO markers were sent')
    parser.add_argument('--from-file', default='',
                        help='đọc log đã ghi từ file thay vì từ serial port')
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f'capture_{args.scenario}.log'

    capture = Capture()
    if args.from_file:
        return _process_file(Path(args.from_file), capture, out_dir, args)

    try:
        link = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as error:
        print(f'Không mở được {args.port}: {error}', file=sys.stderr)
        return 2

    print(f'Đang ghi {args.port} @ {args.baud} -> {raw_path}')
    print('Nhấn Enter để dừng. Gõ "s" + Enter để bắt đầu kịch bản, '
          '"e" + Enter để kết thúc.')
    deadline = None if args.duration <= 0 else time.monotonic() + args.duration
    started = time.monotonic()
    stopped = False

    try:
        with open(raw_path, 'w', encoding='utf-8') as raw:
            while not stopped:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                waiting = link.in_waiting
                if waiting:
                    chunk = link.read(waiting)
                    text = chunk.decode('utf-8', errors='replace')
                    raw.write(text)
                    raw.flush()
                    for line in text.splitlines():
                        capture.feed_line(line)
                else:
                    time.sleep(0.01)
                # A single keypress from the terminal marks a scenario.
                if sys.stdin.isatty() and select_stdin_ready():
                    key = sys.stdin.readline().strip().lower()
                    if key == 's':
                        link.write(b's')
                        link.flush()
                    elif key == 'e':
                        link.write(b'e')
                        link.flush()
                    elif key == 'q':
                        break
    except KeyboardInterrupt:
        print('\nĐã dừng theo yêu cầu.')
    finally:
        link.close()

    elapsed = time.monotonic() - started
    stats = capture.stats()
    print(f'\nGhi {elapsed:.1f} s')
    print(f'  RADAR rows : {stats["radar_rows"]} (span {stats["radar_span_ms"]} ms)')
    print(f'  IMU rows   : {stats["imu_rows"]} (span {stats["imu_span_ms"]} ms)')
    print(f'  scenarios  : {stats["scenarios"]}')
    if stats['unknown']:
        print(f'  UNKNOWN    : {stats["unknown"]} dòng (xem {raw_path})')

    if capture.comments:
        with open(out_dir / f'capture_{args.scenario}.comments.txt', 'w',
                  encoding='utf-8') as handle:
            handle.write('\n'.join(capture.comments) + '\n')

    return _write_outputs(capture, out_dir, args.scenario)


def _process_file(source, capture, out_dir, args):
    """Replay a log that was already written to disk.

    Useful when a capture has to be re-split after the scenario markers were
    added, and as a way to exercise the whole tool without hardware.
    """
    text = Path(source).read_text(encoding='utf-8', errors='replace')
    for line in text.splitlines():
        capture.feed_line(line)
    raw_path = out_dir / f'capture_{args.scenario}.log'
    raw_path.write_text(text, encoding='utf-8')
    print(f'Đọc {source} ({len(text.splitlines())} dòng)')
    return _write_outputs(capture, out_dir, args.scenario)


def _write_outputs(capture, out_dir, scenario_name):
    written = []
    if capture.scenarios:
        for start_us, end_us, name in capture.scenarios:
            radar_rows = [row for row in capture.radar
                          if start_us <= row[0] * 1000 <= end_us]
            imu_rows = [row for row in capture.imu
                        if start_us <= row[0] <= end_us]
            written.append(write_scenario_files(out_dir, name, radar_rows, imu_rows))
    else:
        written.append(write_scenario_files(out_dir, scenario_name,
                                            capture.radar, capture.imu))

    for radar_path, imu_path in written:
        print(f'  ghi {radar_path.name} + {imu_path.name}')

    problems = capture.validate()
    if problems:
        print(f'\nCảnh báo: bản ghi có {len(problems)} vấn đề mà parser của app '
              'sẽ từ chối:')
        for problem in problems[:10]:
            print(f'  - {problem}')
        if len(problems) > 10:
            print(f'  ... và {len(problems) - 10} vấn đề nữa')
        print('Sửa hoặc cắt bỏ đoạn dữ liệu lỗi trước khi phát lại.')

    if not capture.radar:
        print('Cảnh báo: không có khung RADAR nào. Kiểm tra nối LD2417 vào UART2 '
              '(GPIO17 = RX2) và nguồn cho module.', file=sys.stderr)
    if not capture.imu:
        print('Cảnh báo: không có mẫu IMU nào. Kiểm tra địa chỉ I2C 0x68 và '
              'dây SDA/SCL.', file=sys.stderr)
    return 0


def select_stdin_ready():
    """True when a keypress is waiting on stdin (POSIX only)."""
    try:
        import select
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except Exception:
        return False


if __name__ == '__main__':
    raise SystemExit(main())
