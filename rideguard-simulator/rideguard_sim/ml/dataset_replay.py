"""Replay adapter; oracle is isolated in an explicitly visual-only function."""
from bisect import bisect_right
from pathlib import Path
import json
from .stream import sensor_events,read_rows


class DatasetReplay:
    def __init__(self,root,session_id):
        # Allow only a manifest entry, never a path supplied in a session identifier.
        manifest=json.loads((Path(root)/'sessions.json').read_text())
        self.meta=next((r for r in manifest if r['session_id']==session_id),None)
        if self.meta is None:raise ValueError('Unknown dataset session')
        self.folder=Path(root)/'sessions'/session_id
        self.events=sensor_events(self.folder);self.index=0
        self.imu_points=[]
        for event in self.events:
            if event.kind=='IMU':
                p=event.wire_fields()
                self.imu_points.append((event.timestamp_us/1e6,*(p[k] for k in ('ax_mps2','ay_mps2','az_mps2','gx_dps','gy_dps','gz_dps'))))
        self.imu_times=[p[0] for p in self.imu_points]
        self.raw_frames=read_rows(self.folder/'radar_frames.csv')
        self.dropped=sum(r['status']=='DROPPED' for r in self.raw_frames)
        self.empty=sum(r['status']=='OK' and r['target_count']=='0' for r in self.raw_frames)
        self.visual_frames=None

    def reset(self):self.index=0

    def due(self,t_us):
        start=self.index
        while self.index<len(self.events) and self.events[self.index].timestamp_us<=t_us:self.index+=1
        return self.events[start:self.index]

    def load_visual_oracle(self):
        # Sole oracle reader in the app. Never passed into event/feature/Serial path.
        frames={}
        for row in read_rows(self.folder/'oracle_vehicles_DO_NOT_TRAIN.csv'):
            frames.setdefault(int(row['timestamp_ms']),[]).append(row)
        self.visual_frames=frames;self.visual_times=sorted(frames)

    def update_visual_only(self,world):
        if self.visual_frames is None:return
        from ..model import Vehicle
        index=bisect_right(self.visual_times,round(world.t*1000))-1
        if index<0:return
        rows=self.visual_frames[self.visual_times[index]];world.targets=[]
        for row in rows:
            vx=float(row['vx_mps']);vy=float(row['vy_mps'])
            car=Vehicle(0 if row['vehicle_id']=='EGO' else int(row['vehicle_id']),
                x=float(row['world_x_m']),y=float(row['world_y_m']),speed=(vx*vx+vy*vy)**.5,
                heading=float(row['yaw_rad']),vx=vx,vy=vy)
            if row['vehicle_id']=='EGO':world.ego=car
            else:world.targets.append(car)
