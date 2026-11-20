"""Paused edits, actual Qt press/move/release, physics and resulting sensors."""
import math
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import Qt,QEvent,QPoint,QPointF
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from rideguard_sim.ui import Window
from rideguard_sim.scene import RoadView
from rideguard_sim.radar import radar_to_body

APP=QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path,monkeypatch):
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:pytest.fail(box.text()))
    w=Window(tmp_path);w.timer.stop();w.panel_timer.stop();w.compare.setChecked(False)
    w.scenario.setCurrentIndex(0);w.synthetic();w.start();w.pause();w.refresh_panels()
    w.tabs.setCurrentIndex(2);w.show();APP.processEvents()
    w.road.scale=w.ai_road.scale=5
    yield w
    w.close()


def select(w,index):
    w.vehicle.setCurrentIndex(index);w.read_vehicle();return w.chosen()


def advance(w,count):
    for _ in range(count):w.session.step(.01)


def drag(w,view,car,dx_pixels,dy_pixels):
    start=view.point(car.x,car.y).toPoint()+QPoint(2,1) # off-center grab must not snap
    end=start+QPoint(dx_pixels,dy_pixels)
    QTest.mousePress(view,Qt.LeftButton,Qt.NoModifier,start)
    for point in (start+(end-start)/2,end,end):
        pos=QPointF(point)
        QApplication.sendEvent(view,QMouseEvent(QEvent.MouseMove,pos,pos,Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
        APP.processEvents()
    QTest.mouseRelease(view,Qt.LeftButton,Qt.NoModifier,end);APP.processEvents()


@pytest.mark.parametrize('scenario',[0,2])
def test_target_speed_three_stages_labels_and_one_second_motion(window,scenario,monkeypatch):
    w=window;w.session.command('SESSION_STOP','STOPPED')
    w.scenario.setCurrentIndex(scenario);w.synthetic();w.start();w.pause();w.refresh_panels()
    car=select(w,1);assert car.speed*3.6==pytest.approx(43.2)
    original=(car.x,car.y,car.heading);before_t=w.session.world.t
    rendered=[];original_draw=RoadView.draw_vehicle
    def capture(view,painter,vehicle,ego):
        rendered.append((view,vehicle.track_id,f'{vehicle.speed*3.6:.1f}'))
        original_draw(view,painter,vehicle,ego)
    monkeypatch.setattr(RoadView,'draw_vehicle',capture)
    w.velocity.lineEdit().selectAll();QTest.keyClicks(w.velocity.lineEdit(),'60')
    QTest.keyClick(w.velocity,Qt.Key_Tab)
    w.control_stack.currentWidget().ensureWidgetVisible(w.apply_vehicle_button);APP.processEvents()
    QTest.mouseClick(w.apply_vehicle_button,Qt.LeftButton);APP.processEvents()
    assert car.speed*3.6==pytest.approx(60)
    assert (car.x,car.y,car.heading)==original and w.session.world.t==before_t
    assert 'Đã áp dụng: ID31 · 60 km/h'==w.vehicle_feedback.text()
    for view in (w.road,w.ai_road):
        view.grab();assert (view,31,'60.0') in rendered and (view,0,'36.0') in rendered
    w.read_vehicle();assert w.velocity.value()==60
    w.pause();advance(w,1)
    assert car.speed*3.6==pytest.approx(60) and car.x==pytest.approx(original[0]+1/6)
    # 60 km/h = 16.6667 m/s; first 10 ms advances 1/6 m.


def test_target_and_ego_straight_displacements_one_second(window):
    w=window;car=select(w,1);w.velocity.setValue(60);w.edit_vehicle()
    x0,e0=car.x,w.session.world.ego.x
    w.pause();advance(w,100)
    assert car.x-x0==pytest.approx(60/3.6)
    assert w.session.world.ego.x-e0==pytest.approx(10)
    assert (e0-x0)-(w.session.world.ego.x-car.x)==pytest.approx(20/3)
    # Separate EGO-only edit, reset of the same selected scenario.
    w.session.command('SESSION_STOP','STOPPED');w.start();w.pause();w.refresh_panels()
    ego=select(w,0);w.velocity.setValue(54);w.edit_vehicle()
    x0,t0=ego.x,w.session.world.targets[0].x
    w.pause();advance(w,100)
    assert ego.x-x0==pytest.approx(15)
    assert w.session.world.targets[0].x-t0==pytest.approx(12)
    assert w.session.world.targets[0].speed*3.6==pytest.approx(43.2)


@pytest.mark.parametrize('view_name',['road','ai_road'])
def test_drag_target_crosses_side_shared_views_and_radar_then_scheduled_event(window,view_name):
    w=window;car=select(w,1);before=(car.x,car.y,car.speed,car.heading);t=w.session.world.t
    drag(w,getattr(w,view_name),car,35,0) # 7 m to vehicle-right at 5 px/m
    assert (car.x,car.y)==pytest.approx((before[0],-3.5))
    assert (car.speed,car.heading)==before[2:]
    assert w.x.value()==pytest.approx(car.x,abs=.005) and w.y.value()==-3.5
    assert w.road.selected==w.ai_road.selected==31
    assert w.road.point(car.x,car.y)==w.ai_road.point(car.x,car.y)
    assert w.session.world.t==t and 'Đã kéo: ID31' in w.vehicle_feedback.text()
    w.pause();advance(w,1)
    assert car.x==pytest.approx(before[0]+.12) and car.y==-3.5
    for radar in ('LEFT','RIGHT'):
        frame=w.session.last_sent['RADAR_FRAME'+radar]
        assert frame['targets']
        for target in frame['targets']:
            assert target['track_id']==31 and abs(target['angle_deg'])<=w.session.radar.config.half_fov_deg
            _,body_y=radar_to_body(radar,target['y_m'],target['y_m']*math.tan(math.radians(target['angle_deg'])))
            assert body_y<0
    advance(w,299)
    assert car.y==-3.5 and car.x==pytest.approx(before[0]+36)
    assert w.session.world.ego.lane is not None # untouched EGO still follows default event at 2 s


@pytest.mark.parametrize('scenario',[0,7])
def test_drag_ego_freezes_camera_suppresses_only_its_scheduled_steering_and_no_imu_impulse(window,scenario):
    w=window;w.session.command('SESSION_STOP','STOPPED');w.scenario.setCurrentIndex(scenario);w.synthetic();w.start();w.pause();w.refresh_panels()
    ego=select(w,0);target=w.session.world.targets[0];target0=(target.x,target.y,target.speed)
    drag(w,w.road,ego,-15,10)
    assert (ego.x,ego.y)==pytest.approx((-2,3)) # repeated end-position events must not accumulate
    assert ego.speed==10 and ego.heading==0 and w.session.world.t==0
    assert w.x.value()==-2 and w.y.value()==3
    assert w.road.camera_origin()==w.ai_road.camera_origin()==(ego.x,ego.y)
    # Camera re-centers, but lane and target screen positions reflect the new EGO world pose.
    assert w.road.world_point(w.road.point(target.x,target.y))==pytest.approx((target.x,target.y))
    w.pause();advance(w,1)
    assert (ego.x,ego.y)==pytest.approx((-1.9,3))
    assert w.session.last_sent['IMU']['gz_dps']==0 and w.session.last_sent['IMU']['ax_mps2']==0
    advance(w,299)
    assert (ego.x,ego.y)==pytest.approx((28,3)) and ego.lane is None
    assert target.x==pytest.approx(target0[0]+target0[2]*3) and target.y==target0[1]
    w.session.command('SESSION_STOP','STOPPED');w.start()
    assert not w.session.world.ego.manual_fields and w.session.world.ego.x==0 and w.session.world.ego.y==0
    advance(w,210)
    if scenario==0:assert w.session.world.ego.lane is not None
    else:assert abs(w.session.world.ego.heading)>0


@pytest.mark.parametrize('vehicle_index',[0,1])
def test_speed_only_preserves_active_lane_and_full_precision_pose(window,vehicle_index):
    w=window
    if vehicle_index==1:select(w,1);w.lane(3.5)
    w.pause();advance(w,250 if vehicle_index==0 else 50);w.pause();w.refresh_panels()
    car=select(w,vehicle_index);lane=car.lane;pose=(car.x,car.y,car.heading)
    assert lane is not None
    w.velocity.setValue(50);w.edit_vehicle()
    assert car.lane==lane and (car.x,car.y,car.heading)==pose
    w.pause();advance(w,1)
    assert car.lane[3]==pytest.approx(lane[3]+.01)
    assert car.x==pytest.approx(pose[0]+50/3.6*.01)


def test_after_edits_ai_submission_and_pc_rules_receive_same_sensor_stream(window):
    import hashlib
    from types import SimpleNamespace
    w=window;received=[]
    w.ai=SimpleNamespace(submit=lambda events,t:received.extend(events),visible=lambda *args:None,
                         close=lambda:None,history=[],error=None)
    w.common_hash=hashlib.sha256();w.common_count=0
    w.session.sensor_observer=w.observe_sensors
    target=select(w,1);w.velocity.setValue(60);w.edit_vehicle();drag(w,w.road,target,35,0)
    ego=select(w,0);w.velocity.setValue(54);w.edit_vehicle();drag(w,w.ai_road,ego,-10,5)
    w.pause();advance(w,100)
    digest=hashlib.sha256()
    for event in received:digest.update(event.canonical())
    assert len(received)==w.common_count==w.session.pc_rules.sensor_count
    assert digest.hexdigest()==w.common_hash.hexdigest()==w.session.pc_rules.stream_hash.hexdigest()
    imu=[e for e in received if e.kind=='IMU']
    assert imu and all(e.wire_fields()['gz_dps']==0 and e.wire_fields()['ax_mps2']==0 for e in imu)
    assert ego.speed==15 and target.speed==pytest.approx(60/3.6)


def test_drag_active_lane_rebases_without_teleport_yaw_or_acceleration(window):
    w=window;w.pause();advance(w,210);w.pause();w.refresh_panels()
    ego=select(w,0);heading=ego.heading;speed=ego.speed
    assert ego.lane is not None
    drag(w,w.ai_road,ego,20,0);pose=(ego.x,ego.y)
    assert ego.lane is None and ego.heading==heading
    w.pause();advance(w,1)
    assert (ego.x,ego.y)==pytest.approx((pose[0]+speed*math.cos(heading)*.01,pose[1]+speed*math.sin(heading)*.01))
    assert ego.yaw_rate==pytest.approx(0) and (ego.ax,ego.ay)==pytest.approx((0,0))


@pytest.mark.parametrize('source',['replay','dataset','imu_recording','reviewing_history','historical_no_geometry'])
def test_recordings_and_history_are_read_only_for_apply_and_drag(window,source):
    w=window;car=select(w,0);before=(car.x,car.y,car.speed)
    setattr(w.session,source,True)
    w.velocity.setValue(60);w.edit_vehicle();drag(w,w.road,car,10,10)
    assert (car.x,car.y,car.speed)==before and 'chỉ đọc' in w.vehicle_feedback.text()
    setattr(w.session,source,None)
