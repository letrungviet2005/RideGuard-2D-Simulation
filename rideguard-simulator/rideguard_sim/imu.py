"""Flat rigid-body IMU: R^T(a_world - gravity). No roll/pitch model."""
import math


def sample(vehicle, timestamp_ms):
    c, s = math.cos(vehicle.heading), math.sin(vehicle.heading)
    return dict(timestamp_ms=timestamp_ms,
                ax_mps2=round(c*vehicle.ax+s*vehicle.ay, 5),
                ay_mps2=round(-s*vehicle.ax+c*vehicle.ay, 5),
                az_mps2=9.80665, gx_dps=0., gy_dps=0.,
                gz_dps=round(math.degrees(vehicle.yaw_rate), 5))
