"""Causal rg-causal-v1; shared batch/online, no sklearn/numpy/label imports."""
from collections import deque
import math

CHANNELS = ('ax_mps2','ay_mps2','az_mps2','gx_dps','gy_dps','gz_dps')
FEATURES = ('target_count','nearest_y_m','angle_deg','lateral_left_m','closing_mps',
            'track_samples','track_span_s','associated','radar_age_ms','frame_loss_ratio',
            'imu_age_ms','imu_samples','radar_missing','imu_missing') + tuple(
            f'{channel}_{stat}' for channel in CHANNELS for stat in ('last','mean','std'))


class Features:
    def __init__(self,config):
        self.config=config;self.session=None;self.imu=deque();self.frames={'LEFT':deque(),'RIGHT':deque()}
        self.first_us=None

    def ingest(self,event):
        if self.session is None:self.session=event.session_id
        if event.session_id!=self.session:raise ValueError('Feature session mismatch: reset required')
        self.first_us=event.timestamp_us if self.first_us is None else min(self.first_us,event.timestamp_us)
        q=self.imu if event.kind=='IMU' else self.frames[event.wire_fields()['radar_id']]
        if q and event.timestamp_us<q[-1][0]:raise ValueError('Non-monotonic sensor stream')
        q.append((event.timestamp_us,event.wire_fields()))
        cutoff=event.timestamp_us-self.config['window_us']
        while q and q[0][0]<cutoff:q.popleft()

    def at(self,t_us,side):
        # Never use future events, even if a caller has already queued them.
        lo=t_us-self.config['window_us']
        imu=[(t,p) for t,p in self.imu if lo<=t<=t_us]
        frames={r:[(t,p) for t,p in q if lo<=t<=t_us] for r,q in self.frames.items()}
        radar_age=max([t_us-q[-1][0] if q else self.config['window_us']+1 for q in frames.values()])
        imu_age=t_us-imu[-1][0] if imu else self.config['window_us']+1
        warm=self.first_us is None or t_us-self.first_us<self.config['window_us']
        reason='WARM_UP' if warm else 'IMU_STALE' if imu_age>self.config['imu_stale_us'] else 'RADAR_STALE' if radar_age>self.config['stale_us'] else 'READY'
        candidates=[]
        for radar,q in frames.items():
            if not q or t_us-q[-1][0]>self.config['stale_us']:continue
            for target in q[-1][1]['targets']:
                y=target['y_m'];a=target['angle_deg']
                lateral=(self.config['mount_offset_m'] if radar=='LEFT' else -self.config['mount_offset_m'])-y*math.tan(math.radians(a))
                if ('LEFT' if lateral>=0 else 'RIGHT')!=side:continue
                if not (-40<=-y<=2 and abs(lateral)<=7):continue
                history=[]
                for stamp,frame in q:
                    # Radar/session are already scoped: equal IDs in two radars never merge.
                    match=next((v for v in frame['targets'] if v['track_id']==target['track_id']),None)
                    if match:history.append((stamp,match['y_m']))
                    else:history=[]  # no bridging disappearance/reappearance
                span=(history[-1][0]-history[0][0])/1e6 if len(history)>1 else 0
                closing=(history[0][1]-history[-1][1])/span if span else 0
                candidates.append((y,a,lateral,closing,len(history),span,target['associated']))
        chosen=min(candidates,key=lambda v:v[0]) if candidates else (0,0,0,0,0,0,0)
        expected=max(1,2*(min(self.config['window_us'],t_us-(self.first_us or 0))//65000+1))
        loss=max(0,1-sum(len(q) for q in frames.values())/expected)
        x=[len(candidates),*chosen,radar_age/1000,loss,imu_age/1000,len(imu),int(radar_age>self.config['stale_us']),int(imu_age>self.config['imu_stale_us'])]
        for channel in CHANNELS:
            values=[p[channel] for _,p in imu]
            mean=sum(values)/len(values) if values else 0
            variance=sum((v-mean)**2 for v in values)/len(values) if values else 0
            x.extend((values[-1] if values else 0,mean,math.sqrt(variance)))
        assert len(x)==len(FEATURES)
        return x,reason


def snapshots(events,timestamps,config):
    features=Features(config);index=0
    for stamp in timestamps:
        while index<len(events) and events[index].timestamp_us<=stamp:
            features.ingest(events[index]);index+=1
        yield stamp,{side:features.at(stamp,side) for side in ('LEFT','RIGHT')}
