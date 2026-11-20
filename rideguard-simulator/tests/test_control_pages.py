"""Real Qt widgets/events, fixture Session transport; never opens COM."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent, QFontDatabase, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSpinBox, QDoubleSpinBox, QComboBox, QScrollArea
from rideguard_sim.ui import Window

APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts/segoeui.ttf'))
APP.setFont(QFont('Segoe UI',9))


@pytest.fixture
def window(tmp_path):
    w=Window(tmp_path);w.timer.stop();w.panel_timer.stop();w.show();APP.processEvents()
    yield w
    w.close()


def wheel(widget, delta=-120):
    event=QWheelEvent(QPointF(widget.rect().center()),QPointF(widget.mapToGlobal(widget.rect().center())),
                      QPoint(),QPoint(0,delta),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    QApplication.sendEvent(widget,event);APP.processEvents()


def test_four_tabs_running_are_presentation_only(window,monkeypatch):
    w=window
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())]==['Kết nối ESP32','Nguồn & điều khiển','Xe & quỹ đạo','Tham số phiên mới']
    assert w.tabs.currentIndex()==1
    w.session.state='RUNNING';w.x.setValue(123.45);w.rule_history.append({'fixture':True})
    sentinel=object();w.session.link=sentinel
    monkeypatch.setattr(w.session,'send',lambda *a,**k:pytest.fail('Tab sent Serial'))
    monkeypatch.setattr(w.session,'disconnect',lambda:pytest.fail('Tab disconnected'))
    before=(w.session.world,w.session.envelope.session,w.session.replay,w.session.dataset)
    geometry=w.road.geometry(),w.ai_road.geometry(),w.timeline_area.geometry()
    for index in (0,2,3,1):
        w.tabs.setCurrentIndex(index);APP.processEvents()
        assert w.control_stack.currentIndex()==index
        assert geometry==(w.road.geometry(),w.ai_road.geometry(),w.timeline_area.geometry())
        assert w.x.value()==123.45 and w.rule_history==[{'fixture':True}]
        assert w.session.state=='RUNNING' and w.session.link is sentinel
        assert before==(w.session.world,w.session.envelope.session,w.session.replay,w.session.dataset)
    w.session.link=None


def test_state_locks_wait_for_ack_and_recorded_sources(window):
    w=window
    for state,pending,edit,lane in [('STOPPED',None,False,False),('RUNNING',None,False,True),
                                  ('PAUSED',None,True,True),('PAUSED',('PAUSE',1,'PAUSED',0),False,False)]:
        w.session.state=state;w.session.pending=pending;w.refresh_controls()
        assert w.apply_vehicle_button.isEnabled()==edit
        assert w.straight_button.isEnabled()==edit
        assert w.lane_left.isEnabled()==lane
        assert w.speed.isEnabled()==(state=='STOPPED' and pending is None)
        assert w.pause_button.isEnabled()==(state in ('RUNNING','PAUSED') and pending is None)
    w.session.pending=None;w.session.state='PAUSED';w.session.replay=object();w.refresh_controls()
    assert not w.apply_vehicle_button.isEnabled() and not w.lane_left.isEnabled()
    car=w.chosen();before=(car.x,car.y,car.lane)
    w.lane(3.5);w.straight()
    assert (car.x,car.y,car.lane)==before
    w.session.replay=None


def test_source_browsing_and_failed_load_keep_committed_data(window,tmp_path,monkeypatch):
    w=window;monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:0)
    # Browsing does not mutate the current Session or reset recorded history.
    w.rule_history=[{'keep':True}];world=w.session.world
    w.source_choice.setCurrentIndex(2);APP.processEvents()
    assert w.source_groups[2].isVisible() and not w.source_groups[0].isVisible()
    assert w.session.world is world and w.rule_history==[{'keep':True}]
    # Use the supplied validated 14-row excerpt, not arbitrary fake physical units.
    sample=Path(__file__).resolve().parents[3]/'tools/samples/reference/user-provided-14rows.csv'
    w.load_imu_path(sample);old=w.session.imu_recording
    bad=tmp_path/'bad.csv';bad.write_text('bad header\n')
    assert not w.safe(lambda:w.load_imu_path(bad),'Nhập CSV IMU')
    assert w.session.imu_recording is old
    w.refresh_controls();assert 'CSV IMU + radar tổng hợp' in w.session_summary.text()
    assert 'IMU_HEADER' in w.logs.toPlainText()
    w.session.state='STOPPED';w.source_choice.setCurrentIndex(0);w.scenario.setCurrentIndex(1)
    assert w.session.imu_recording is old
    w.synthetic_button.click();assert w.session.imu_recording is None and w.loaded_scenario==1


def test_radar_and_dataset_failure_never_replace_ready_source(window,tmp_path,monkeypatch):
    w=window;monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:0)
    sample=Path(__file__).resolve().parents[3]/'tools/samples/radar_recorded_excerpt.csv'
    monkeypatch.setattr('rideguard_sim.ui.QFileDialog.getOpenFileName',lambda *a:(str(sample),'CSV'))
    w.source_choice.setCurrentIndex(2);w.import_recording();old=w.session.replay
    assert old is not None and len(old.frames)>0
    bad=tmp_path/'bad.csv';bad.write_text('broken\n')
    monkeypatch.setattr('rideguard_sim.ui.QFileDialog.getOpenFileName',lambda *a:(str(bad),'CSV'))
    w.import_recording();assert w.session.replay is old
    w.source_choice.setCurrentIndex(1);w.dataset_root=tmp_path/'missing'
    w.dataset_button.click();assert w.session.replay is old and w.session.dataset is None
    assert 'CSV/log radar' in w.session_summary.text()


def test_com_open_and_start_ack_are_distinct_and_help_uses_repo_document(window,monkeypatch):
    from types import SimpleNamespace
    import threading
    from rideguard_sim.control_panel import open_field_guide
    w=window;w.session.rule_source='esp32';ready=threading.Event();ready.set()
    w.session.link=SimpleNamespace(ready=ready)
    try:
        w.refresh_panels();assert 'COM đã mở' in w.connection_label.text()
        assert 'chưa xác nhận SESSION_START' in w.connection_label.text()
        w.session.state='STOPPED';w.session.pending=('SESSION_START',1,'RUNNING',0)
        w.refresh_panels();assert 'chưa xác nhận' in w.connection_label.text() and 'chờ ACK' in w.status.text()
        w.session.pending=None;w.session.state='RUNNING';w.refresh_panels()
        assert 'SESSION_START đã ACK' in w.connection_label.text()
    finally:w.session.link=None;w.session.pending=None
    seen=[];monkeypatch.setattr('rideguard_sim.control_panel.QDesktopServices.openUrl',lambda url:seen.append(url.toLocalFile()) or True)
    open_field_guide(w)
    assert Path(seen[0])==w.repo/'docs/simulator/GIAI-THICH-TUNG-TRUONG.md'


def test_wheel_never_changes_editors_even_with_focus_and_scrolls_parent(window):
    w=window;w.tabs.setCurrentIndex(3);APP.processEvents()
    scroll=w.control_stack.currentWidget();assert isinstance(scroll,QScrollArea)
    bar=scroll.verticalScrollBar();bar.setValue(0)
    before=w.range.value();w.range.setFocus();wheel(w.range)
    assert w.range.value()==before and bar.value()>0
    # Every editor gets the guard, including review integer spin and all combos.
    for cls in (QSpinBox,QDoubleSpinBox,QComboBox):
        for editor in w.findChildren(cls):
            before=editor.currentIndex() if cls is QComboBox else editor.value()
            editor.setFocus();wheel(editor)
            assert (editor.currentIndex() if cls is QComboBox else editor.value())==before
    w.range.setFocus();w.range.setValue(40);QTest.keyClick(w.range,Qt.Key_Up)
    assert w.range.value()==41
    w.tabs.setCurrentIndex(1);w.source_choice.showPopup();APP.processEvents()
    assert w.source_choice.view().isVisible()
    event=QWheelEvent(QPointF(4,4),QPointF(4,4),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    assert not w.wheel_guard.eventFilter(w.source_choice,event)
    w.source_choice.hidePopup()
    # A closed, focused combo also scrolls its page without changing the choice.
    scroll=w.control_stack.currentWidget();bar=scroll.verticalScrollBar();bar.setValue(0)
    w.source_choice.setFocus();before=w.source_choice.currentIndex();wheel(w.source_choice)
    assert w.source_choice.currentIndex()==before and bar.value()>0
    w.tabs.setCurrentIndex(3);APP.processEvents();scroll=w.control_stack.currentWidget()
    bar=scroll.verticalScrollBar();bar.setValue(0)
    pixel=QWheelEvent(QPointF(w.range.rect().center()),QPointF(w.range.mapToGlobal(w.range.rect().center())),
        QPoint(0,-40),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
    before=w.range.value();QApplication.sendEvent(w.range,pixel);APP.processEvents()
    assert w.range.value()==before and bar.value()>0



def test_1366_768_controls_visible_and_shortcut_has_no_serial_effect(window):
    w=window;w.resize(1366,768);APP.processEvents()
    assert (w.width(),w.height())==(1366,768)
    for index in range(4):
        assert w.tabs.tabRect(index).width()>200
        w.tabs.setCurrentIndex(index);APP.processEvents()
        for button in (w.start_button,w.pause_button,w.stop_button):
            assert button.isVisible() and w.rect().contains(button.mapTo(w,button.rect().bottomRight()))
    w.connection_shortcut.click();assert w.tabs.currentIndex()==0 and w.session.link is None


def test_pc_default_manual_gyro_and_applied_config_are_independent_of_tabs(tmp_path,monkeypatch):
    monkeypatch.setattr('rideguard_sim.ui.ports',lambda:pytest.fail('PC must not enumerate COM'))
    monkeypatch.setattr('rideguard_sim.ui.SerialLink',lambda *a:pytest.fail('PC must not open COM'))
    w=Window(tmp_path);w.timer.stop();w.panel_timer.stop()
    try:
        assert w.session.rule_source=='pc' and w.rule_source.currentIndex()==0
        w.compare.setChecked(False);w.yellow_mode.setCurrentIndex(1);w.yellow.setValue(15);w.near.setValue(10)
        w.start();assert w.session.state=='RUNNING' and not w.session.pending
        w.imu_mode.setCurrentIndex(1);w.gyro_slider.setValue(85)
        assert w.gyro_value.value()==8.5 and w.session.manual_gz==8.5
        count=len(w.session.live_imu_rows);w.gyro_slider.setValue(90)
        assert len(w.session.live_imu_rows)==count
        w.session.step(.01);assert w.session.last_sent['IMU']['gz_dps']==9
        w.near.setValue(5);w.refresh_panels()
        assert w.session.applied_rules['near_m']==10 and 'đỏ 10.0' in w.session_summary.text()
        w.rule_source.setCurrentIndex(1);assert w.session.rule_source=='pc' and w.rule_source.currentIndex()==0
        w.pause();w.gyro_value.setValue(-4);before=len(w.session.live_imu_rows);w.session.step(.01)
        assert len(w.session.live_imu_rows)==before
        w.session.command('SESSION_STOP','STOPPED');w.start()
        assert w.session.manual_gz==0 and w.gyro_value.value()==0 and w.session.applied_rules['near_m']==5
    finally:w.close()
