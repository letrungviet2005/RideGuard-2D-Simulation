"""Import recorded tracks without inventing empty frames or ego motion."""
import csv
from dataclasses import dataclass
import math
from pathlib import Path


@dataclass
class Frame:
    t_ms: int
    frame: int
    radar_id: str
    targets: list


@dataclass
class Recording:
    source: str
    raw: str
    frames: list[Frame]


def target(row):
    direction = {'trai':1, 'phai':2, '1':1, '2':2}
    result = dict(track_id=int(row['id']), dir=direction[row['huong'].strip().lower()],
                  y_m=float(row['y_m']), v_kmh=float(row['v_kmh']), angle_deg=float(row['goc']),
                  high_speed=int(row['cao_toc']), associated=int(row['lien_ket']),
                  duration_frames=int(row['ton_tai']))
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError('Non-finite radar value')
    if not (0<=result['track_id']<=255 and .001<=result['y_m']<=150 and
            0<=result['v_kmh']<=655.35 and abs(result['angle_deg'])<=80 and
            result['high_speed'] in (0,1) and result['associated'] in (0,1) and
            0<=result['duration_frames']<=255):
        raise ValueError('Radar value outside protocol bounds')
    return result


def load(path, radar_id, rebase=True):
    """Load a radar recording.

    ``rebase`` (the default) shifts every frame so the first one starts at
    0 ms.  Set it to False to keep the recorded ``ms`` values, which is what
    a synchronised hardware capture needs: the radar file and the IMU file
    then keep the same origin and the desktop app aligns them with the IMU
    offset instead of each importer rebasing on its own first row.
    """
    if radar_id not in ('LEFT','RIGHT'):
        raise ValueError('Chọn radar gắn LEFT/RIGHT; huong không phải radar_id')
    raw = Path(path).read_text(encoding='utf-8-sig')
    lines = [line for line in raw.splitlines() if line.strip() and not line.startswith('#')]
    frames = []
    if not lines:
        raise ValueError('Empty recording')
    if lines[0].startswith(('D,','E,')):
        pending, key = [], None
        columns = 'ms,frame,id,huong,y_m,v_kmh,goc,cao_toc,lien_ket,ton_tai'.split(',')
        for line in lines:
            values = next(csv.reader([line]))
            if values[0]=='D' and len(values)==11:
                row = dict(zip(columns,values[1:]))
                current = int(row['ms']),int(row['frame'])
                if key is not None and current!=key:
                    raise ValueError('Missing E marker before next frame')
                key=current
                pending.append(target(row))
            elif values[0]=='E' and len(values)==4:
                current=int(values[1]),int(values[2])
                if key is not None and current!=key or int(values[3])!=len(pending):
                    raise ValueError('E marker mismatch')
                frames.append(Frame(*current,radar_id,pending))
                pending,key=[],None
            else:
                raise ValueError('Invalid D/E line')
        if pending:
            raise ValueError('Unterminated D frame')
    else:
        key=None
        for row in csv.DictReader(lines):
            sensor=row.get('radar_id',radar_id)
            if sensor not in ('LEFT','RIGHT'):
                raise ValueError('Invalid radar_id')
            current=int(row['ms']),int(row['frame']),sensor
            if current!=key:
                frames.append(Frame(*current,[]))
                key=current
            frames[-1].targets.append(target(row))
    if not frames:
        raise ValueError('No complete frames')
    previous={}
    previous_global=-1
    base=frames[0].t_ms
    for frame in frames:
        if frame.t_ms<previous_global or frame.t_ms<=previous.get(frame.radar_id,-1):
            raise ValueError('ms must increase per radar; no sorting or invented timestamps')
        previous[frame.radar_id]=frame.t_ms
        previous_global=frame.t_ms
        if len(frame.targets)>4 or len({t['track_id'] for t in frame.targets})!=len(frame.targets):
            raise ValueError('Too many / duplicate targets')
        if rebase:
            frame.t_ms-=base
    return Recording(str(Path(path).resolve()),raw,frames)


def coordinates(target):
    # Explicit Y-axis interpretation; raw x_m/fwd_m remain in Recording.raw.
    return target['y_m'], target['y_m']*math.tan(math.radians(target['angle_deg']))
