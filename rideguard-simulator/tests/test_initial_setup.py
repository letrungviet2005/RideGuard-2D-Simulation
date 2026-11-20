"""Qt STOPPED -> save -> START and numerical speed/trajectory acceptance."""
import math
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from rideguard_sim.ui import Window
from rideguard_sim.model import Vehicle,World
from rideguard_sim.vehicle_setup import validate_setup,defaults

APP=QApplication.instance() or QApplication([])

@pytest.fixture
def window(tmp_path,monkeypatch):
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:pytest.fail(box.text()))
    w=Window(tmp_path);w.timer.stop();w.panel_timer.stop();w.compare.setChecked(False);w.show();w.tabs.setCurrentIndex(2)
    yield w
    w.close()

def schedule(card):
    card.mode.setCurrentIndex(1)
    for row in [(0,2,0),(2,5,1),(5,8,-1)]:card.add_row(row)

def save(w):
    w.control_stack.currentWidget().ensureWidgetVisible(w.setup_panel.save_button);APP.processEvents()
    QTest.mouseClick(w.setup_panel.save_button,Qt.LeftButton);APP.processEvents()
    assert 'Đã lưu' in w.setup_panel.summary.text()

@pytest.mark.parametrize('playback',[0,1,2])
def test_qt_initial_speed_profile_and_exact_displacements(window,playback):
    w=window;w.scenario.setCurrentIndex(2);w.synthetic();p=w.setup_panel
    p.cards[0].speed.setValue(36);p.cards[31].speed.setValue(43.2);p.cards[31].x.setValue(-120);schedule(p.cards[31]);save(w)
    w.speed.setCurrentIndex(playback);w.start()
    assert w.session.world.t==0 and w.session.world.ego.speed*3.6==pytest.approx(36)
    target=w.session.world.targets[0];assert target.x==-120 and target.speed*3.6==pytest.approx(43.2)
    x0=target.x
    for i in range(800):
        w.session.step(.01)
        if i==499:assert target.speed*3.6==pytest.approx(54,abs=1e-7)
    assert target.speed*3.6==pytest.approx(43.2,abs=1e-7)
    assert target.x-x0==pytest.approx(105,abs=1e-6)
    assert w.session.world.ego.x==pytest.approx(80,abs=1e-6)
    w.session.command('SESSION_STOP','STOPPED');w.start()
    assert target is not w.session.world.targets[0] and w.session.world.targets[0].x==x0

def test_ego_independent_schedule_and_boundary_stop():
    car=Vehicle(0,speed=10);car.settle();car.acceleration_schedule=[(0,2,1),(2,3,-20)]
    world=World(ego=car,targets=[]);world.step(3.5)
    assert car.x==pytest.approx(22+3.6) and car.speed==0
    assert car.acceleration_at(4)==0

def test_ego_only_acceleration_keeps_target(window):
    w=window;w.scenario.setCurrentIndex(2);w.synthetic();p=w.setup_panel
    p.cards[0].acceleration.setValue(1);save(w);w.start()
    for _ in range(100):w.session.step(.01)
    assert w.session.world.ego.speed==pytest.approx(11)
    assert w.session.world.ego.x==pytest.approx(10.5)
    assert w.session.world.targets[0].speed==12
    assert w.session.last_sent['IMU']['ax_mps2']==pytest.approx(1)

@pytest.mark.parametrize('playback',[0,1,2])
def test_wall_clock_playback_preserves_physics_at_same_simulation_time(window,monkeypatch,playback):
    w=window;w.scenario.setCurrentIndex(2);w.synthetic();schedule(w.setup_panel.cards[31]);save(w)
    w.speed.setCurrentIndex(playback);w.start();captured={};step=w.session.world.step
    def observed(dt):
        step(dt);world=w.session.world
        if round(world.t*100) in (500,800):
            captured[round(world.t)]=(world.ego.x,world.targets[0].x,world.targets[0].speed*3.6)
    w.session.world.step=observed
    clock=[0.];w.last_tick=0.
    monkeypatch.setattr('rideguard_sim.ui.time.monotonic',lambda:clock[0])
    for _ in range(1700):
        clock[0]+=.02;w.tick()
        if 8 in captured:break
    assert captured[5][2]==pytest.approx(54)
    assert captured[8]==pytest.approx((80,-24+105,43.2))

def test_live_steering_sensitivity_and_pause_profile_override(window):
    w=window;schedule(w.setup_panel.cards[31]);save(w);w.start()
    w.steer_sensitivity.setValue(.5);w.set_steer(-1)
    for _ in range(300):w.session.step(.01)
    assert w.session.world.ego.heading<0 and w.session.world.ego.lane is None
    assert math.degrees(w.session.world.ego.yaw_rate)==pytest.approx(-7.5)
    car=w.session.world.targets[0];assert car.speed==pytest.approx(13)
    w.pause();w.vehicle.setCurrentIndex(1);w.read_vehicle();w.velocity.setValue(60);w.edit_vehicle()
    assert car.speed*3.6==pytest.approx(60) and not car.speed_profile_enabled
    assert 'dừng đến hết phiên' in w.profile_feedback.text()
    w.pause()
    for _ in range(500):w.session.step(.01)
    assert car.speed*3.6==pytest.approx(60)
    assert w.session.initial_setups[0][31]['acceleration_schedule']

def test_validation_dirty_and_multiple_target_cards(window):
    w=window;p=w.setup_panel;c=p.cards[31];schedule(c);c.add_row((1,3,1));p.save()
    assert 'ID31 · dòng 4' in p.summary.text() and 'chồng lấn' in p.summary.text()
    assert not w.session.initial_setups
    assert p.dirty()
    w.scenario.setCurrentIndex(5);w.synthetic();assert set(p.cards)=={0,31,32}
    p.cards[0].speed.setValue(50);p.cards[31].speed.setValue(60);p.cards[32].speed.setValue(70);save(w);w.start()
    assert [v.speed*3.6 for v in [w.session.world.ego,*w.session.world.targets]]==pytest.approx([50,60,70])

@pytest.mark.parametrize('segments',[[(0,2,1),(1,3,0)],[(2,1,0)],[(0,1,float('nan'))]])
def test_invalid_schedule_reports_vehicle_and_row(segments):
    values=defaults(Vehicle(31));values['acceleration_schedule']=segments
    with pytest.raises(ValueError,match='ID31 · dòng'):validate_setup(values,'ID31')
