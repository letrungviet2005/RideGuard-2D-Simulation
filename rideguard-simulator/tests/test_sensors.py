import math
import pytest
from rideguard_sim.model import World, Vehicle, body_position
from rideguard_sim.imu import sample
from rideguard_sim.radar import Radar, RadarConfig, radar_to_body
from rideguard_sim.protocol import Envelope, encode


def test_rotation_and_mount_roundtrip():
    ego=Vehicle(0,10,20,heading=math.pi/2)
    assert body_position(ego,10,25)==pytest.approx((5,0))
    assert radar_to_body('LEFT',10,-3)==pytest.approx((-9.35,3.4))


def test_both_radars_can_see_same_left_vehicle():
    world=World()
    radar=Radar()
    left,right=radar.sample(world,'LEFT'),radar.sample(world,'RIGHT')
    assert left[0]['track_id']==right[0]['track_id']==31
    assert left[0]['y_m']==pytest.approx(24.65)
    assert left[0]['v_kmh']==pytest.approx(7.2)
    assert left[0]['angle_deg']<0


@pytest.mark.parametrize('x,y',[ (5,0),(-10,40),(-100,0)])
def test_outside_fov_or_range_is_valid_empty(x,y):
    world=World(targets=[Vehicle(31,x,y)])
    assert Radar().sample(world,'LEFT')==[]


def test_dropout_is_not_empty_and_unassociated_is_retained():
    assert Radar(RadarConfig(dropout=1)).sample(World(),'LEFT') is None
    radar=Radar(RadarConfig(unassociated=1))
    first=radar.sample(World(),'LEFT')[0]
    second=radar.sample(World(),'LEFT')[0]
    assert first['associated']==0 and second['track_id']==31
    assert second['duration_frames']==2


def test_stationary_specific_force():
    car=Vehicle(0,speed=0,vx=0)
    car.step(.01)
    assert sample(car,10)==dict(timestamp_ms=10,ax_mps2=0,ay_mps2=0,az_mps2=9.80665,gx_dps=0,gy_dps=0,gz_dps=0)


def test_lane_change_is_smooth_and_yaw_reverses():
    car=Vehicle(0)
    car.change_lane(3.5)
    yaws=[];positions=[]
    for _ in range(410):
        car.step(.01);yaws.append(sample(car,0)['gz_dps']);positions.append(car.y)
    assert max(yaws)>2 and min(yaws)<-2
    assert max(abs(b-a) for a,b in zip(positions,positions[1:]))<.02
    assert car.y==pytest.approx(3.5) and car.heading==pytest.approx(0)
    assert yaws[-1]==0


def test_protocol_rejects_nonfinite_and_oversized():
    envelope=Envelope()
    with pytest.raises(ValueError):
        encode(envelope.make('IMU',gz_dps=float('nan')))
    with pytest.raises(ValueError):
        encode(envelope.make('IMU',data='x'*5000))
