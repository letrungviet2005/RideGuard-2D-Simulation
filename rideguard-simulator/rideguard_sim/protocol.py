import json
import uuid

VERSION = 1
MAX_LINE = 4096
BAUD = 921600
CONTROL = {'SESSION_START', 'PAUSE', 'RESUME', 'SESSION_STOP'}
DEFAULT_RULES = dict(yaw_threshold_dps=2.0, confirm_ms=200, hold_ms=350,
                     near_m=18., stale_ms=260)


class Envelope:
    def __init__(self):
        self.session = uuid.uuid4().hex
        self.sequences = {}

    def make(self, kind, t_ms=0, **fields):
        stream = kind + fields.get('radar_id', '')
        seq = self.sequences.get(stream, 0)+1
        self.sequences[stream] = seq
        return dict(protocol_version=VERSION, session_id=self.session, type=kind,
                    seq=seq, t_ms=int(t_ms), **fields)


def encode(message):
    data = json.dumps(message, separators=(',', ':'), allow_nan=False).encode('utf-8')+b'\n'
    if len(data)>MAX_LINE:
        raise ValueError('Serial frame exceeds 4096 bytes')
    return data


def budget_bytes_per_second(period_ms, max_targets, speed=1, imu_period_ms=10):
    # Conservative bound for normalized numeric values and 32-char session.
    return speed*(1000/imu_period_ms*330 + 2*1000/period_ms*(210+max_targets*170))
