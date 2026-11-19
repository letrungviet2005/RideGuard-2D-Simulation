"""IMU CSV importer: preserve source microseconds and physical units.

user_table_v1 validates the ordered 14-column schema and RAW coefficients
provided by the user (not a verified chip configuration). physical_only is
explicit compatibility for old physical-unit captures without known RAW scale.
No gravity normalization, whole-session gyro bias subtraction, inferred turn
label, or timestamp repair. Mounting is declared by the caller.
"""
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

GRAVITY = 9.80665

# The protocol accepts these ranges; anything else is rejected by the board.
ACCEL_LIMIT = 200.0
GYRO_LIMIT = 2000.0


@dataclass
class ImuSample:
    t_us: int
    ax_mps2: float
    ay_mps2: float
    az_mps2: float
    gx_dps: float
    gy_dps: float
    gz_dps: float
    temperature_C: float = None
    source_timestamp_us: int | None = None

    @property
    def t_ms(self):
        """Legacy read-only projection; scheduling always uses t_us."""
        return self.t_us//1000


@dataclass
class ImuRecording:
    source: str
    raw: str
    samples: list = field(default_factory=list)
    mount: str = 'identity'
    quantised_samples: int = 0
    notes: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    profile: str = 'physical_only'

    @property
    def span_us(self):
        return sample_us(self.samples[-1])-sample_us(self.samples[0])

    @property
    def rate_hz(self):
        return (len(self.samples)-1)*1e6/self.span_us if self.span_us else 0


def sample_us(sample):
    return sample.t_us


def _axis_map(mount):
    """Return (permutation, signs) mapping sensor axes to vehicle axes.

    ``mount`` is ``'identity'`` or ``'xyz<-abc'`` with optional ``,-z`` style
    sign suffixes.
    """
    if mount == 'identity':
        return 'xyz', {}
    if '<-' not in mount:
        raise ValueError(f'Mount phải là "identity" hoặc "xyz<-abc": {mount!r}')
    left, right = mount.split('<-', 1)
    left = left.strip().lower()
    right = right.strip().lower()
    if sorted(left) != ['x', 'y', 'z']:
        raise ValueError(f'Cặp trục phải là x,y,z: {left!r}')
    perm = ''
    signs = {}
    # The right-hand side is a comma-separated list of *sensor* axes, one per
    # vehicle axis: "xyz<-z,y,x" means vehicle X comes from sensor Z,
    # vehicle Y from sensor Y and vehicle Z from sensor X.
    tokens = [token.strip().lower() for token in right.split(',')]
    for token in tokens:
        if not token:
            continue
        sign = 1
        if token.startswith('-'):
            sign, token = -1, token[1:]
        elif token.startswith('+'):
            token = token[1:]
        if token not in 'xyz' or len(token) != 1:
            raise ValueError(f'Tộc không hợp lệ trong mount: {token!r}. '
                             f'Mỗi trục phải là đúng một ký tự x/y/z.')
        if token in perm:
            raise ValueError(f'Tộc {token!r} lặp trong mount')
        perm += token
        signs[token] = sign
    if sorted(perm) != ['x', 'y', 'z']:
        raise ValueError(f'Mount phải gán đủ 3 trục, thiếu hoặc lặp: {right!r}')
    return perm, signs


def _parse_mount(mount):
    """Split a mount string into (axis mapping, yaw sign)."""
    yaw_sign = 1
    text = mount.strip()
    if 'yaw=' in text:
        text, _, yaw_part = text.partition('yaw=')
        text = text.strip().rstrip(',').strip()
        try:
            yaw_sign = int(yaw_part.strip())
        except ValueError:
            raise ValueError(f'yaw= phải là ±1: {yaw_part!r}')
        if yaw_sign not in (1, -1):
            raise ValueError(f'yaw= phải là ±1: {yaw_part!r}')
    perm, signs = _axis_map(text)
    return perm, signs, yaw_sign


def _apply_axes(perm, signs, ax, ay, az, gx, gy, gz):
    """Map sensor-frame values into the vehicle frame."""
    sensor = {'x': (ax, gx), 'y': (ay, gy), 'z': (az, gz)}
    acc = {}
    gyr = {}
    for vehicle_axis, sensor_axis in zip('xyz', perm):
        sign = signs.get(sensor_axis, 1)
        acc[vehicle_axis], gyr[vehicle_axis] = (sensor[sensor_axis][0] * sign,
                                                sensor[sensor_axis][1] * sign)
    return acc['x'], acc['y'], acc['z'], gyr['x'], gyr['y'], gyr['z']


def _number(row, name):
    value = row.get(name)
    if value is None or value.strip() == '':
        return None
    try:
        result = float(value)
    except ValueError:
        raise ValueError(f'{name} không phải số: {value!r}')
    if not math.isfinite(result):
        raise ValueError(f'{name} không hữu hạn: {value!r}')
    return result


def load(path, mount='identity', max_gap_ms=None, rebase=True, profile='physical_only', rotation_deg=(0,0,0)):
    """Load IMU CSV using a strict profile or physical_only compatibility.

    Parameters
    ----------
    path         CSV file; user_table_v1 requires the ordered 14-column schema.
    mount        explicit mounting declaration (see the module docstring).
    max_gap_ms   optional guard: reject a log with a gap larger than this,
                 which usually means the recording was interrupted.
    rebase       when True (the default) the timestamps are made relative to
                 the first sample, so the log starts at 0 ms.  Set it to
                 False to keep the absolute ``timestamp_us`` values (still
                 preserved in microseconds).  A synchronised hardware capture
                 needs ``rebase=False`` for the IMU file, because the radar
                 file and the IMU file then share one origin and the desktop
                 app aligns them with the IMU offset.

    Raises
    ------
    ValueError
        For a malformed log, a missing required column, a non-monotonic
        timestamp, or out-of-range values.  The importer never repairs data
        silently.
    """
    perm, signs, yaw_sign = _parse_mount(mount)
    from .imu_csv import HEADER,PROFILE,ACCEL_G_PER_LSB,GYRO_DPS_PER_LSB,rotation,transform
    if profile not in ('physical_only',PROFILE):
        raise ValueError('Unknown IMU profile')
    if len(rotation_deg)!=3 or not all(math.isfinite(v) for v in rotation_deg):
        raise ValueError('Invalid sensor-to-body mounting angles')
    matrix=rotation(*(math.radians(v) for v in rotation_deg))
    raw = Path(path).read_text(encoding='utf-8-sig')
    indexed_lines=[(i,line) for i,line in enumerate(raw.splitlines(),start=1) if line.strip() and not line.startswith('#')]
    lines=[line for _,line in indexed_lines]
    if not lines:
        raise ValueError('File IMU rỗng')
    reader = csv.DictReader(lines)
    fieldnames = [str(name).strip() for name in (reader.fieldnames or [])]
    if profile==PROFILE and reader.fieldnames!=HEADER:
        raise ValueError(f'IMU_HEADER: dòng {indexed_lines[0][0]}, profile user_table_v1 cần đúng 14 cột theo thứ tự. '
                         f'Đang đọc {len(reader.fieldnames or [])} cột: {reader.fieldnames}. '
                         f'Header cần có: {",".join(HEADER)}. Dùng dấu phẩy phân cột và dấu chấm thập phân, không dùng dấu chấm phẩy.')
    required = ('timestamp_us', 'ax_g', 'ay_g', 'az_g', 'gx_dps', 'gy_dps', 'gz_dps')
    missing = [name for name in required if name not in fieldnames]
    if missing:
        raise ValueError(f'Thiếu cột bắt buộc: {", ".join(missing)}. '
                         f'Cột có: {", ".join(fieldnames)}')

    samples = []
    notes = []
    quantised = 0
    base_us = None
    previous_us = None

    for row in reader:
        number=indexed_lines[reader.line_num-1][0]
        def numeric(name):
            try:
                return _number(row,name)
            except ValueError as error:
                raise ValueError(f'Dòng {number}, cột {name}: {error}') from error
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f'Dòng {number}: sai số cột')
        row = {str(k).strip(): v for k, v in row.items()}
        timestamp_us = numeric('timestamp_us')
        if timestamp_us is None or timestamp_us<0 or timestamp_us!=int(timestamp_us) or not row['timestamp_us'].strip().isdigit():
            raise ValueError(f'Dòng {number}: timestamp_us phải là số nguyên không âm')
        timestamp_us=int(row['timestamp_us'])
        if previous_us is not None and timestamp_us <= previous_us:
            raise ValueError(f'IMU_TIMESTAMP_ORDER: dòng {number}, cột timestamp_us={timestamp_us}; '
                             f'mẫu trước={previous_us}. Timestamp µs phải tăng nghiêm ngặt, không được trùng hoặc giảm. '
                             'Kiểm tra thứ tự ghi/xuất CSV; app không tự sắp xếp hay sửa thời gian.')
        previous_us = timestamp_us

        # Preserve source microseconds; no forward quantization or sorting.
        if base_us is None:
            base_us = timestamp_us
        t_us=timestamp_us-base_us if rebase else timestamp_us

        if max_gap_ms is not None and samples:
            gap = (t_us-sample_us(samples[-1]))/1000
            if gap > max_gap_ms:
                raise ValueError(f'Dòng {number}: lỗ hổng {gap} ms > {max_gap_ms} ms')

        ax_g = numeric('ax_g')
        ay_g = numeric('ay_g')
        az_g = numeric('az_g')
        gx = numeric('gx_dps')
        gy = numeric('gy_dps')
        gz = numeric('gz_dps')
        if None in (ax_g, ay_g, az_g, gx, gy, gz):
            raise ValueError(f'Dòng {number}: thiếu giá trị gia tốc/gyro')

        for i,(raw_name,physical_name) in enumerate(zip(HEADER[1:7],HEADER[7:13])):
            if raw_name not in row:
                continue  # explicit physical_only compatibility for hardware capture CSV
            raw_value=numeric(raw_name)
            if raw_value is None or not -32768<=raw_value<=32767 or raw_value!=int(raw_value) or not row[raw_name].strip().lstrip('+-').isdigit():
                raise ValueError(f'IMU_RAW_INT16: dòng {number}, cột {raw_name}={row[raw_name]!r}; cần số nguyên trong [-32768,32767]. Không tự tràn/cắt giá trị.')
            if profile==PROFILE:
                scale=ACCEL_G_PER_LSB if i<3 else GYRO_DPS_PER_LSB
                # User excerpt physical columns are rounded to 5 decimal places.
                actual=numeric(physical_name);expected=raw_value*scale
                if abs(actual-expected)>5.1e-6:
                    raise ValueError(f'IMU_RAW_UNIT: dòng {number}, cột {physical_name}={actual}; '
                                     f'{raw_name}={int(raw_value)} × hệ số {scale} = {expected:.9f}; '
                                     f'sai lệch {abs(actual-expected):.9f} > dung sai 0.0000051. '
                                     'Kiểm tra profile/cấu hình xuất dữ liệu. Chỉ chọn physical_only nếu chủ động dùng cột vật lý và không kiểm hệ số RAW; không sửa hệ số để che lỗi.')

        # g -> m/s^2.  Gravity stays in the reading, per the protocol.
        ax, ay, az = ax_g * GRAVITY, ay_g * GRAVITY, az_g * GRAVITY
        # The gyro columns are already degrees/s; pass them through unchanged.
        ax, ay, az, gx, gy, gz = _apply_axes(perm, signs, ax, ay, az, gx, gy, gz)
        gz *= yaw_sign
        ax,ay,az=transform(matrix,(ax,ay,az))
        gx,gy,gz=transform(matrix,(gx,gy,gz))

        for name, value in (('ax_mps2', ax), ('ay_mps2', ay), ('az_mps2', az)):
            if abs(value) > ACCEL_LIMIT:
                raise ValueError(f'Dòng {number}: {name}={value} ngoài ±{ACCEL_LIMIT}')
        for name, value in (('gx_dps', gx), ('gy_dps', gy), ('gz_dps', gz)):
            if abs(value) > GYRO_LIMIT:
                raise ValueError(f'Dòng {number}: {name}={value} ngoài ±{GYRO_LIMIT}')

        temperature = numeric('temperature_C')
        if profile==PROFILE and temperature is None:
            raise ValueError(f'Dòng {number}: missing temperature_C')
        samples.append(ImuSample(t_us, round(ax, 5), round(ay, 5), round(az, 5),
                                  round(gx, 5), round(gy, 5), round(gz, 5),
                                  temperature,timestamp_us))

    if not samples:
        raise ValueError('File IMU không có mẫu nào')

    if profile=='physical_only' and ('ax_raw' in fieldnames or 'gx_raw' in fieldnames):
        notes.append('Cột *_raw là LSB, không dùng để quy đổi: thiếu full-scale '
                     'và sensitivity thực tế của thiết bị. Chỉ cột g và dps '
                     'được dùng.')
    if quantised:
        notes.append(f'{quantised} mẫu trùng mili-giây đã được lượng tử '
                     f'tiếp để giữ timestamp tăng nghiêm ngặt.')
    if mount != 'identity':
        notes.append(f'Áp dụng mount {mount!r} (yaw sign {yaw_sign:+d}).')

    import json,hashlib
    metadata={'source':'UNKNOWN','sensor_model':'UNKNOWN'}
    sidecar=Path(str(path)+'.json')
    if sidecar.exists():
        loaded=json.loads(sidecar.read_text(encoding='utf-8'))
        if loaded.get('sha256')!=hashlib.sha256(Path(path).read_bytes()).hexdigest():
            raise ValueError('IMU metadata SHA-256 does not match CSV')
        metadata=loaded
    if profile==PROFILE:
        notes.append('RAW/đơn vị đã kiểm theo user_table_v1; cấu hình phần cứng chưa xác minh')
    metadata=dict(metadata,applied_rotation_deg=list(rotation_deg),import_rebase=rebase)
    return ImuRecording(str(Path(path).resolve()), raw, samples, mount,
                        quantised, notes,metadata,profile)


def to_protocol(sample, offset_ms=0):
    """Convert one imported sample into the IMU protocol fields.

    ``offset_ms`` shifts the timestamp onto another clock, for example when
    the IMU log and the radar log were recorded with separate clocks.
    """
    return dict(timestamp_ms=(sample_us(sample)+round(offset_ms*1000))//1000,
                ax_mps2=sample.ax_mps2, ay_mps2=sample.ay_mps2, az_mps2=sample.az_mps2,
                gx_dps=sample.gx_dps, gy_dps=sample.gy_dps, gz_dps=sample.gz_dps)


def describe(recording):
    """Human-readable provenance for the UI."""
    if not recording.samples:
        return 'IMU: không có mẫu'
    parts = [f'IMU: {len(recording.samples)} mẫu, '
             f'{recording.span_us/1000:.3f} ms, ~{recording.rate_hz:.3f} Hz, mount {recording.mount}',
             f'nguồn {recording.metadata.get("source","UNKNOWN")}; profile {recording.profile}; g → m/s²; gyro °/s',
             f'mount Euler sensor→body {recording.metadata.get("applied_rotation_deg",[0,0,0])}°']
    parts.extend(recording.notes)
    return ' | '.join(parts)
