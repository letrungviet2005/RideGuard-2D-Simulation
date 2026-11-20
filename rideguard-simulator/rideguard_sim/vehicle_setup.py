"""Saved initial conditions and independent longitudinal acceleration profiles."""
from copy import deepcopy
import math


def validate_setup(values, name):
    out=deepcopy(values)
    for key,lo,hi in [('x',-10000,10000),('y',-100,100),('speed',0,200/3.6),('heading',-math.pi,math.pi),('acceleration',-10,10)]:
        value=float(out[key])
        if not math.isfinite(value) or not lo<=value<=hi:raise ValueError(f'{name}: {key} không hợp lệ')
        out[key]=value
    rows=out['acceleration_schedule']
    for index,(start,end,a) in enumerate(rows,1):
        if not all(math.isfinite(v) for v in (start,end,a)) or not 0<=start<end<=3600 or not -10<=a<=10:
            raise ValueError(f'{name} · dòng {index}: cần 0 ≤ Từ < Đến ≤ 3600 s, gia tốc −10…10 m/s²')
        if any(start<old_end and end>old_start for old_start,old_end,_ in rows[:index-1]):
            raise ValueError(f'{name} · dòng {index}: giai đoạn chồng lấn')
    out['acceleration_schedule']=sorted(rows)
    return out


def defaults(car):
    return dict(x=car.x,y=car.y,speed=car.speed,heading=car.heading,acceleration=0.,acceleration_schedule=[])


def apply_setup(world, saved):
    for car in [world.ego,*world.targets]:
        values=saved.get(car.track_id)
        if values is None:continue
        values=validate_setup(values,'EGO' if car.track_id==0 else f'ID{car.track_id}')
        # Initial position is a new trajectory origin, not a paused edit that
        # implicitly cancels a compatible scheduled lane change.
        for key,value in values.items():setattr(car,key,deepcopy(value))
        car.settle()
