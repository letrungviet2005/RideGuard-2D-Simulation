"""Live EGO steering: real Qt key/button/slider events -> Session -> physics -> sensors.

Qt offscreen only; no ESP32 and no serial port.
"""
import math
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from rideguard_sim.ui import Window
from rideguard_sim.radar import Radar

APP=QApplication.instance() or QApplication([])
SAMPLE=Path(__file__).resolve().parents[2]/'samples/radar_recorded_excerpt.csv'
DT=.01


def make_window(tmp_path,monkeypatch,scenario=0):
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:pytest.fail(box.text()))
    w=Window(tmp_path);w.timer.stop();w.panel_timer.stop();w.compare.setChecked(False)
    w.scenario.setCurrentIndex(scenario);w.synthetic();w.start();w.refresh_panels()
    w.tabs.setCurrentIndex(2);w.show();APP.processEvents()
    return w


@pytest.fixture
def window(tmp_path,monkeypatch):
    w=make_window(tmp_path,monkeypatch)
    assert w.session.state=='RUNNING'
    yield w
    w.close()


def advance(w,steps):
    for _ in range(steps):w.session.step(DT)


def ego(w):return w.session.world.ego
def target(w):return w.session.world.targets[0]


def baseline_target_path(tmp_path,monkeypatch,steps,scenario=0):
    """ID31 pose when the user never touches EGO (same scenario, same seed)."""
    w=make_window(tmp_path/'baseline',monkeypatch,scenario)
    try:
        path=[]
        for _ in range(steps):
            w.session.step(DT);t=w.session.world.targets[0];path.append((t.x,t.y,t.speed,t.heading))
        return path
    finally:w.close()


def test_right_steer_held_through_scheduled_left_lane_change(window,tmp_path,monkeypatch):
    w=window;base=baseline_target_path(tmp_path,monkeypatch,500)
    advance(w,100)                                    # t=1 s, before the 2 s scheduled left lane change
    assert ego(w).control=='scenario'
    QTest.keyPress(w.road,Qt.Key_Right)               # real key event on the road view
    assert ego(w).control=='manual' and ego(w).steer<0 and 'Tay lái trực tiếp' in w.control_status.text()
    headings=[];gz=[];ys=[];path=[]
    for _ in range(400):                              # hold across t=2 s and on to t=5 s
        w.session.step(DT);headings.append(ego(w).heading);ys.append(ego(w).y)
        gz.append(w.session.last_sent['IMU']['gz_dps']);t=target(w);path.append((t.x,t.y,t.speed,t.heading))
    assert ego(w).lane is None                        # scheduled lane change never re-armed
    assert headings[-1]<math.radians(-30) and all(b<=a+1e-12 for a,b in zip(headings,headings[1:]))
    assert max(ys)<=1e-9 and ys[-1]<-3                # goes to the RIGHT (Y is left-positive), never left
    assert gz[-1]==pytest.approx(-15,abs=.01) and max(gz)<=0   # IMU shows the real right yaw
    assert path[:500-100]==base[100:500]              # ID31 untouched by the takeover
    # Radar was recomputed for the rotated EGO: it equals a fresh geometric sample of this very world.
    cfg=w.session.radar.config;fresh=Radar(cfg).sample(w.session.world,'LEFT')
    assert fresh and w.session.last_sent['RADAR_FRAMELEFT']['targets'][0]['track_id']==31
    QTest.keyRelease(w.road,Qt.Key_Right)


def test_takeover_mid_lane_change_then_center_never_rejoins_sample(window):
    w=window;advance(w,250)                           # t=2.5 s: scheduled lane change is active
    assert ego(w).lane is not None
    pose=(ego(w).x,ego(w).y,ego(w).heading,ego(w).speed);v0=(ego(w).vx,ego(w).vy)
    QTest.keyPress(w.road,Qt.Key_Right)
    previous=(ego(w).x,ego(w).y,ego(w).heading);worst_step=0;worst_acc=0
    for _ in range(100):
        w.session.step(DT)
        x,y,h=ego(w).x,ego(w).y,ego(w).heading
        worst_step=max(worst_step,math.hypot(x-previous[0],y-previous[1]));previous=(x,y,h)
        imu=w.session.last_sent['IMU'];worst_acc=max(worst_acc,abs(imu['ax_mps2']),abs(imu['ay_mps2']))
    assert ego(w).lane is None and ego(w).control=='manual'
    assert worst_step<=math.hypot(*v0)*DT*1.0001+1e-9         # no teleport: never faster than the vehicle
    assert worst_acc<3                                # no IMU impulse at takeover
    assert ego(w).speed==pytest.approx(math.hypot(*v0),rel=1e-9)
    QTest.keyRelease(w.road,Qt.Key_Right)             # steering back to centre = stop turning
    advance(w,40)                                     # yaw rate slews down to 0
    held=(ego(w).heading,ego(w).y);y_before=ego(w).y;x_before=ego(w).x
    assert ego(w).yaw_rate==pytest.approx(0,abs=1e-9)
    advance(w,300)
    assert ego(w).heading==pytest.approx(held[0],abs=1e-12)        # keeps heading, no return to straight
    assert ego(w).lane is None and ego(w).control=='manual'
    assert ego(w).y-y_before==pytest.approx(math.sin(held[0])*ego(w).speed*3,abs=1e-6)
    assert ego(w).x-x_before==pytest.approx(math.cos(held[0])*ego(w).speed*3,abs=1e-6)
    assert w.session.last_sent['IMU']['gz_dps']==0
    # Reset restores the original scenario exactly.
    w.session.command('SESSION_STOP','STOPPED');w.start()
    e=ego(w)
    assert (e.x,e.y,e.heading,e.control,e.steer)==(0,0,0,'scenario',0) and not e.manual_fields
    advance(w,210)
    assert ego(w).lane is not None and ego(w).control=='scenario'


def test_hold_buttons_and_slider_use_the_same_command_path(window):
    w=window;advance(w,10)
    QTest.mousePress(w.steer_left_button,Qt.LeftButton);APP.processEvents()
    assert ego(w).steer==pytest.approx(w.STEER_BUTTON_LEVEL) and ego(w).control=='manual'
    advance(w,50);assert ego(w).heading>0 and w.session.last_sent['IMU']['gz_dps']>0
    QTest.mouseRelease(w.steer_left_button,Qt.LeftButton);APP.processEvents()
    assert ego(w).steer==0
    w.steer_slider.setValue(-50)                       # slider emits valueChanged like a user drag
    assert ego(w).steer==pytest.approx(-.5)
    advance(w,100);assert w.session.last_sent['IMU']['gz_dps']==pytest.approx(-7.5,abs=.01)
    QTest.mouseClick(w.steer_center_button,Qt.LeftButton);APP.processEvents()
    assert ego(w).steer==0 and w.steer_slider.value()==0
    advance(w,20)                                      # yaw rate slews to 0 within ~0.17 s (90 deg/s^2)
    heading=ego(w).heading;advance(w,100);assert ego(w).heading==pytest.approx(heading,abs=1e-9)


def test_steering_allowed_when_running_but_coordinate_edit_still_needs_pause(window):
    w=window;advance(w,5)
    assert w.session.steer_reason()=='' and w.steer_slider.isEnabled() and w.steer_left_button.isEnabled()
    assert 'Tạm dừng' in w.session.vehicle_edit_reason() or 'PAUSED' in w.session.vehicle_edit_reason()
    assert not w.apply_vehicle_button.isEnabled() and not w.x.isEnabled()
    w.pause();w.refresh_panels()
    assert w.session.state=='PAUSED' and w.apply_vehicle_button.isEnabled() and w.steer_slider.isEnabled()


def test_release_to_scenario_only_on_explicit_request(window):
    w=window;advance(w,50)
    QTest.keyPress(w.road,Qt.Key_Left);advance(w,50);QTest.keyRelease(w.road,Qt.Key_Left);advance(w,20)
    assert ego(w).control=='manual' and w.steer_release_button.isEnabled()
    pose=(ego(w).x,ego(w).y,ego(w).heading)
    advance(w,200)                                    # past 2 s event: still the user's car
    assert ego(w).lane is None and ego(w).control=='manual'
    pose2=(ego(w).x,ego(w).y,ego(w).heading)
    assert pose2[2]==pytest.approx(pose[2],abs=1e-12)
    QTest.mouseClick(w.steer_release_button,Qt.LeftButton);APP.processEvents()
    assert ego(w).control=='scenario' and (ego(w).x,ego(w).y,ego(w).heading)==pose2   # no teleport
    assert 'Theo kịch bản' in w.control_status.text()


def test_csv_replay_locks_steering_and_keeps_recorded_samples(tmp_path,monkeypatch):
    w=make_window(tmp_path,monkeypatch)
    try:
        w.session.command('SESSION_STOP','STOPPED')
        recording=w.session.load_replay(SAMPLE,'LEFT');w.start()
        e=ego(w);before=(e.x,e.y,e.heading,e.control)
        QTest.keyPress(w.road,Qt.Key_Right);QTest.keyRelease(w.road,Qt.Key_Right)
        w.steer_slider.setValue(80)
        assert (e.x,e.y,e.heading,e.control)==before and e.steer==0
        assert not w.steer_slider.isEnabled() and 'khóa lái' in w.control_status.text()
        assert 'CSV radar' in w.control_status.text() and 'khóa' in w.steer_hint.text()
        for _ in range(300):
            QTest.keyPress(w.road,Qt.Key_Right);w.session.step(DT)
        frame=w.session.last_sent['RADAR_FRAMELEFT']
        assert frame['targets']==recording.frames[w.session.replay_index-1].targets   # exact recorded sample
        assert frame['t_ms']==recording.frames[w.session.replay_index-1].t_ms
        # Back to synthetic: no replay source may keep overriding EGO.
        w.session.command('SESSION_STOP','STOPPED');w.synthetic();w.start()
        assert w.session.replay is None and w.session.dataset is None and w.session.imu_recording is None
        assert w.session.replay_index==0 and not w.session.replay_finished
        advance(w,20);QTest.keyPress(w.road,Qt.Key_Right);advance(w,100)
        assert ego(w).control=='manual' and ego(w).heading<0
        assert 'Nguồn: Tình huống dựng sẵn | EGO: Tay lái trực tiếp' in w.control_status.text()
    finally:w.close()


def test_selecting_dropdown_session_without_loading_does_not_lock_steering(window):
    w=window
    w.source_choice.setCurrentIndex(1);w.source_choice.setCurrentIndex(2)   # choosing is not loading
    assert w.session.dataset is None and w.session.replay is None
    QTest.keyPress(w.road,Qt.Key_Left);assert ego(w).control=='manual'


def test_manual_gyro_test_mode_locks_live_steering_with_reason(window):
    w=window;w.imu_mode.setCurrentIndex(1);w.refresh_panels()
    assert not w.steer_slider.isEnabled() and 'gyro' in w.steer_hint.text().lower()
    QTest.keyPress(w.road,Qt.Key_Right);assert ego(w).control=='scenario'


def test_id31_speed_edit_and_drag_regression_with_live_steering(window):
    w=window;advance(w,20);QTest.keyPress(w.road,Qt.Key_Right);advance(w,20);QTest.keyRelease(w.road,Qt.Key_Right)
    w.pause();w.refresh_panels()
    car=target(w);assert car.speed*3.6==pytest.approx(43.2)
    ego_before=(ego(w).x,ego(w).y,ego(w).heading,ego(w).speed,ego(w).control)
    w.vehicle.setCurrentIndex(1);w.read_vehicle()
    w.velocity.setValue(60);w.edit_vehicle();w.read_vehicle()
    assert car.speed*3.6==pytest.approx(60) and w.velocity.value()==pytest.approx(60)
    assert (ego(w).x,ego(w).y,ego(w).heading,ego(w).speed,ego(w).control)==ego_before   # EGO not changed
    x0=car.x;w.pause();advance(w,100)
    assert car.x-x0==pytest.approx(60/3.6) and car.speed*3.6==pytest.approx(60)
    for message in (w.session.last_sent['RADAR_FRAMELEFT'],w.session.last_sent['RADAR_FRAMERIGHT']):
        assert all(t['track_id']==31 for t in message['targets'])
    # Both views draw the same world and the same new speed.
    from rideguard_sim.scene import RoadView
    seen=[];original=RoadView.draw_vehicle
    RoadView.draw_vehicle=lambda view,p,c,e:(seen.append((view,c.track_id,round(c.speed*3.6,1))),original(view,p,c,e))[1]
    try:
        for view in (w.road,w.ai_road):
            view.grab();assert (view,31,60.0) in seen
    finally:RoadView.draw_vehicle=original
