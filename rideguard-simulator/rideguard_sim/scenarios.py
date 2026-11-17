"""Expanded after COM5 end-to-end acceptance, 2026-11-17 15:55–15:59.

Scenario labels/expected outcomes never enter the firmware protocol.
"""
import math
from .model import World, Vehicle

NAMES = ['Xe trái tiến gần → chuyển làn trái', 'Đi thẳng · đường trống',
         'Xe phía sau trái tiến gần', 'Xe phía sau phải tiến gần',
         'Xe trái · ego chuyển làn phải', 'Hai bên có xe · ego sang trái',
         'Mục tiêu vào / ra FOV', 'Rung hướng ngắn · kiểm tra lọc',
         'Mất radar trái trong cảnh báo', 'Mất IMU trong cảnh báo']


def create(index=0):
    world=World()
    if index==1:world.targets=[]
    if index==3:world.targets[0].y=-3.5
    if index==5:world.targets.append(Vehicle(32,-24,-3.5,12,vx=12))
    if index==6:
        world.targets=[Vehicle(31,-15,30,10,heading=-.8)]
        world.targets[0].settle()
    return world


def update(world, index=0):
    if index in (0,4,5,8,9) and 2 <= world.t < 2.011 and world.ego.lane is None:
        world.ego.change_lane(-3.5 if index==4 else 3.5)
    if index==7:
        # Short physical yaw excursion (not an arbitrary gyro label). The IMU
        # differentiates this heading on the next physics step, then reverses.
        u=(world.t-2)/.12
        world.ego.next_heading=math.radians(.25)*math.sin(2*math.pi*u) if 0<u<1 else 0


def enabled_sensors(world, index=0):
    return not(index==9 and world.t>=3.5),not(index==8 and world.t>=3.5),True
