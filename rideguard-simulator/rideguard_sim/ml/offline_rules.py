"""Bản luật offline: port for comparison, never labelled hardware evidence."""
import math


class OfflineRules:
    def __init__(self):
        self.imu=None;self.radars={};self.filtered=0.;self.heading=0.;self.candidate=0
        self.since=0;self.motion=0;self.until={'LEFT':0,'RIGHT':0}

    def ingest(self,event):
        t=event.timestamp_us//1000;p=event.wire_fields()
        if event.kind=='IMU':
            dt=(t-self.imu[0])/1000 if self.imu else .01
            if dt>.2:self.filtered=0;self.candidate=0;self.motion=0;self.heading=0
            self.filtered+=dt/(.08+dt)*(p['gz_dps']-self.filtered)
            self.heading+=self.filtered*dt
            candidate=1 if self.filtered>2 else -1 if self.filtered<-2 else 0
            if candidate!=self.candidate:self.candidate=candidate;self.since=t
            self.motion=candidate if t-self.since>=200 else 0
            self.imu=(t,p)
        else:self.radars[p['radar_id']]=(t,p)

    def at(self,t_us):
        t=t_us//1000
        if self.imu is None or t-self.imu[0]>260 or any(r not in self.radars or t-self.radars[r][0]>260 for r in ('LEFT','RIGHT')):
            self.until={'LEFT':0,'RIGHT':0};return dict(LEFT='UNKNOWN',RIGHT='UNKNOWN')
        seen=set();high=set()
        for radar,(_,p) in self.radars.items():
            for target in p['targets']:
                lateral=(.4 if radar=='LEFT' else -.4)-target['y_m']*math.tan(math.radians(target['angle_deg']))
                side='LEFT' if lateral>=0 else 'RIGHT';seen.add(side)
                if target['y_m']<=18 and self.motion==(1 if side=='LEFT' else -1) and (self.heading>0 if side=='LEFT' else self.heading<0):high.add(side)
        for side in high:self.until[side]=t+350
        return {side:'HIGH' if side in seen and t<self.until[side] else 'MONITOR' if side in seen else 'NONE' for side in ('LEFT','RIGHT')}
