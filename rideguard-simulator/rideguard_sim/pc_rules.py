"""Explicit PC execution of the existing firmware model. Never a Serial RESULT."""
import copy
import time
from .firmware_model import FirmwareModel

VERSION='pc-port-2'
FIRMWARE_VERSION='sensor-rules-2'
YELLOW_UNSUPPORTED=('Firmware này chưa hỗ trợ ngưỡng vàng riêng. Chọn Bộ luật · Máy tính '
                    'để thử ngay, hoặc dùng chế độ vàng cũ trên bo.')


def validate_rules(rules):
    import math
    ranges={'yaw_threshold_dps':(.2,30),'near_m':(1,100),'confirm_ms':(50,2000),
            'hold_ms':(0,2000),'stale_ms':(100,2000)}
    for key,(lo,hi) in ranges.items():
        value=rules[key]
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError(f'RULES: {key} ngoài [{lo},{hi}]')
        if key.endswith('_ms') and int(value)!=value:raise ValueError(f'RULES: {key} phải là số nguyên')
    if rules.get('yellow_mode','detected') not in ('detected','distance'):raise ValueError('RULES: yellow_mode')
    if rules.get('yellow_mode')=='distance':
        yellow=rules.get('yellow_m')
        if not isinstance(yellow,(int,float)) or not math.isfinite(yellow) or not 0<rules['near_m']<=yellow<=150:
            raise ValueError('Cần 0 < Vùng gần báo đỏ <= Khoảng cách báo vàng <= 150 m')


def confirms_rules(applied,expected):
    import math
    if not isinstance(applied,dict) or applied.keys()!=expected.keys():return False
    return all(math.isclose(applied[k],v,rel_tol=1e-6,abs_tol=1e-5)
               if isinstance(v,(int,float)) and isinstance(applied[k],(int,float))
               else applied[k]==v for k,v in expected.items())


class PcRules:
    def __init__(self,rules,session_id,clock=time.monotonic):
        validate_rules(rules)
        self.rules=copy.deepcopy(rules);self.session_id=session_id;self.clock=clock
        self.model=FirmwareModel(dict(yaw=rules['yaw_threshold_dps'],near=rules['near_m'],
            confirm=rules['confirm_ms'],hold=rules['hold_ms'],stale=rules['stale_ms'],
            yellow_mode=rules.get('yellow_mode','detected'),yellow_m=rules.get('yellow_m',15)))
        self.walls={};self.sequences={};self.active=True;self.paused=False
        self.last_wall=clock();self.sequence=0
        import hashlib
        self.stream_hash=hashlib.sha256();self.sensor_count=0

    def ingest(self,event):
        if event.session_id!=self.session_id:raise ValueError('PC session mismatch')
        self.stream_hash.update(event.canonical());self.sensor_count+=1
        p=event.wire_fields();t=event.timestamp_us//1000
        key='IMU' if event.kind=='IMU' else p['radar_id']
        if event.kind=='IMU':self.model.feed_imu(t,p['gz_dps'])
        else:self.model.feed_radar(key,t,p['targets'])
        self.walls[key]=self.last_wall=self.clock();self.sequences[key]=event.sequence

    def control(self,kind):
        self.last_wall=self.clock()
        if kind=='SESSION_STOP':self.active=False
        elif kind=='PAUSE':self.paused=True
        elif kind=='RESUME':
            self.paused=False;self.walls={};self.model.imu=None;self.model.radar={}
            self.model.motion=0;self.model.filtered_yaw=0

    def result(self):
        wall=self.clock();m=self.model;out,motion,yaw=m.result()
        def fresh(key,entry):return m._fresh(entry) and key in self.walls and wall-self.walls[key]<1
        unknown=('NO_SESSION' if not self.active else 'LINK_STALE' if wall-self.last_wall>=1 else
                 'IMU_STALE' if not fresh('IMU',m.imu) else
                 'RADAR_STALE' if any(not fresh(r,m.radar.get(r)) for r in ('LEFT','RIGHT')) else None)
        if self.paused:unknown='PAUSED'
        if unknown:
            motion='UNKNOWN';m.high_until={'left':0,'right':0}
            for side in out:out[side].update(level='UNKNOWN',reason_code=unknown)
        sensors={}
        for key in ('IMU','LEFT','RIGHT'):
            entry=m.imu if key=='IMU' else m.radar.get(key)
            sensors[key]=dict(valid=m._fresh(entry) and key in self.walls and wall-self.walls[key]<1,
                seq=self.sequences.get(key,0),t_ms=entry[0] if entry else None,
                sim_age_ms=m.now-entry[0] if entry else None,
                wall_age_ms=round((wall-self.walls[key])*1000) if key in self.walls else None)
        self.sequence+=1
        return dict(type='RULE_PC',session_id=self.session_id,seq=self.sequence,t_ms=m.now,
            rule_source='pc',rule_version=VERSION,applied_rules=self.rules,paused=self.paused,
            pc_sensor_count=self.sensor_count,pc_stream_sha256=self.stream_hash.hexdigest(),
            motion=motion,filtered_yaw_dps=yaw,heading_deg=m.heading_deg,sensors=sensors,**out)
