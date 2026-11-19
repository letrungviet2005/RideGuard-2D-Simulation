"""Immutable sensor-only events. No label/oracle/metadata dependency."""
from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import json
from ..replay import target


@dataclass(frozen=True)
class Event:
    session_id: str
    kind: str
    sequence: int
    timestamp_us: int
    payload: str  # canonical JSON: callers cannot mutate a shared dictionary

    @classmethod
    def from_message(cls, message):
        kind = message['type']
        if kind == 'IMU':
            fields = {k: message[k] for k in ('ax_mps2','ay_mps2','az_mps2','gx_dps','gy_dps','gz_dps')}
        elif kind == 'RADAR_FRAME':
            fields = {k: message[k] for k in ('radar_id','targets')}
        else:
            raise ValueError('Not a sensor event')
        return cls(message['session_id'],kind,message['seq'],message['t_ms']*1000,
                   json.dumps(fields,sort_keys=True,separators=(',',':'),allow_nan=False))

    def wire_fields(self):
        fields=json.loads(self.payload)
        if self.kind=='IMU':fields['timestamp_ms']=self.timestamp_us//1000
        return fields

    def canonical(self):
        # Session UUID/sequence are identity evidence, not predictive features.
        return json.dumps([self.kind,self.sequence,self.timestamp_us,self.payload],separators=(',',':')).encode()+b'\n'


def read_rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as handle:
        return list(csv.DictReader(handle))


def sensor_events(folder):
    """Only reads the three sensor CSVs; keeps empty frames, omits DROPPED."""
    folder = Path(folder)
    sid = folder.name
    events = []
    for seq,row in enumerate(read_rows(folder/'imu.csv'),1):
        stamp = int(row['timestamp_us'])
        if stamp % 1000:
            raise ValueError('Dataset adapter requires exact ms protocol timestamps; no silent rounding')
        fields = {axis+'_mps2':float(row[axis+'_g'])*9.80665 for axis in ('ax','ay','az')}
        fields.update({axis+'_dps':float(row[axis+'_dps']) for axis in ('gx','gy','gz')})
        events.append(Event.from_message(dict(type='IMU',session_id=sid,seq=seq,t_ms=stamp//1000,**fields)))
    tracks = {}
    for row in read_rows(folder/'radar_targets.csv'):
        key = (int(row['ms']),int(row['frame']),row['radar_id'])
        tracks.setdefault(key,[]).append(target(row))
    seqs = {'LEFT':0,'RIGHT':0}
    for row in read_rows(folder/'radar_frames.csv'):
        key = (int(row['ms']),int(row['frame']),row['radar_id'])
        rows = tracks.pop(key,[])
        if row['status']=='DROPPED':
            if rows or row['target_count']!='':
                raise ValueError('DROPPED frame has targets/count')
            continue
        if row['status']!='OK' or len(rows)!=int(row['target_count']):
            raise ValueError('Radar frame/target mismatch')
        seqs[row['radar_id']]+=1
        events.append(Event.from_message(dict(type='RADAR_FRAME',session_id=sid,seq=seqs[row['radar_id']],
                      t_ms=key[0],radar_id=row['radar_id'],targets=rows)))
    if tracks:
        raise ValueError('Orphan radar targets')
    return tuple(sorted(events,key=lambda e:(e.timestamp_us,e.kind,e.payload)))


def stream_digest(events):
    digest = hashlib.sha256()
    for event in events:
        digest.update(event.canonical())
    return digest.hexdigest()


def check_split(manifest):
    seen = set(); groups = {}; counts = {}
    for row in manifest:
        sid = row['session_id']; split = row['split']; group = row['parameter_group']
        if sid in seen or split not in ('train','validation','test'):
            raise ValueError('Invalid/duplicate session split')
        if group in groups and groups[group]!=split:
            raise ValueError('Parameter group crosses splits')
        seen.add(sid);groups[group]=split;counts[split]=counts.get(split,0)+1
    if counts!={'train':80,'validation':20,'test':20}:
        raise ValueError(f'Unexpected split counts: {counts}')
    return counts
