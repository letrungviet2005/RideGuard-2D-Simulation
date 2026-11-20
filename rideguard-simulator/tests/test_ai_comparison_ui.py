"""Qt offscreen layout and shared-session guard tests, not physical ESP32."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication
from rideguard_sim.ui import Window

APP=QApplication.instance() or QApplication([])
ROOT=Path(__file__).resolve().parents[3]


def test_comparison_keeps_one_session_geometry_and_no_fake_esp32(tmp_path):
    window=Window(tmp_path)
    try:
        window.timer.stop();window.panel_timer.stop()
        window.show();APP.processEvents()
        # Two frames stay mounted even before inference is enabled (stable UI).
        assert not window.ai_road.isHidden()
        geometry=window.road.geometry(),window.ai_road.geometry(),window.timeline_area.geometry()
        window.compare.setChecked(True);APP.processEvents()
        assert not window.ai_road.isHidden()
        assert geometry==(window.road.geometry(),window.ai_road.geometry(),window.timeline_area.geometry())
        assert window.road.session is window.ai_road.session is window.session
        assert window.road.caption=='Bộ luật · Máy tính' and window.ai_road.caption=='AI · Máy tính'
        assert window.session.visible_result() is None and window.ai_road.result_provider() is None
        window.refresh_panels()
        assert 'Chưa có kết quả · Bộ luật · Máy tính' in window.reason.text()
        assert 'Chưa đủ kết quả' in window.comparison_label.text()
        assert window.road.scale==window.ai_road.scale
        assert not window.grab().isNull()
    finally:window.close()


@pytest.mark.skipif(not (ROOT/'data/RideGuard_Synthetic_v1').exists(),reason='Ignored local dataset required')
def test_dataset_selection_keeps_common_clock_and_blocks_edits(tmp_path):
    window=Window(tmp_path)
    try:
        window.timer.stop();window.panel_timer.stop()
        window.dataset_session.setCurrentIndex(0);window.load_dataset()
        assert window.session.dataset is not None
        assert window.speed.currentIndex()==0 and window.synced_logs.isChecked()
        assert window.session.replay is None and window.session.imu_recording is None
        ego=window.session.world.ego;before=(ego.x,ego.y,ego.heading,ego.lane)
        window.session.state='PAUSED'
        window.edit_vehicle();window.lane(3.5);window.straight()
        assert (ego.x,ego.y,ego.heading,ego.lane)==before
        with pytest.raises(ValueError,match='dataset đồng bộ'):window.load_imu_path('dummy.csv')
        window.session.state='STOPPED';window.synthetic()
        assert window.session.dataset is None
    finally:window.close()


def test_review_displays_recorded_pair_without_sending_or_reprocessing(tmp_path):
    import copy
    from types import SimpleNamespace
    window=Window(tmp_path)
    try:
        window.timer.stop();window.panel_timer.stop()
        sid=window.session.envelope.session
        rule=dict(type='RESULT',seq=1,session_id=sid,t_ms=1000,
                  left=dict(level='HIGH',reason_code='fixture'),right=dict(level='NONE',reason_code='fixture'))
        ai=dict(type='AI',session_id=sid,t_ms=1000,
                left=dict(level='MONITOR',reason_code='READY',scores={'MONITOR':.8}),right=dict(level='NONE',reason_code='READY',scores={'NONE':.9}))
        class FakeAI:
            history=[ai];latest_wall=0;error=None
            def close(self):pass
        window.ai=FakeAI();window.rule_history=[rule]
        world=copy.deepcopy(window.session.world);world.t=1.
        window.visual_history=[(1000,world,{})];window.review_time.setValue(1000)
        window.session.send=lambda *args,**kwargs:pytest.fail('Review must never send Serial')
        window.review_at()
        assert window.visible_rule() is rule and window.visible_ai() is ai
        assert window.session.world.t==1.
        assert 'XEM LẠI' in window.reason.text() and 'XEM LẠI' in window.ai_reason.text()
        assert 'false' in window.comparison_label.text()  # disagreement at same timestamp
    finally:window.close()


def test_disagreement_navigation_is_stop_only_and_grades_reference(tmp_path,monkeypatch):
    import copy
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:pytest.fail(box.text()))
    window=Window(tmp_path)
    try:
        window.timer.stop();window.panel_timer.stop();window.compare.setChecked(True)
        sid=window.session.envelope.session
        def message(t,level,kind):return dict(type=kind,session_id=sid,seq=t,t_ms=t,
            left=dict(level=level,reason_code='fixture',scores={}),right=dict(level='NONE',reason_code='fixture',scores={}))
        rule=message(1000,'HIGH','RESULT');ai=message(1000,'MONITOR','AI')
        class Recorded:
            history=[ai];error=None;latest_wall=0
            def visible(self,*args):return None
            def close(self):pass
        window.ai=Recorded();window.rule_history=[rule]
        window.visual_history=[(1000,copy.deepcopy(window.session.world),{}),(1100,copy.deepcopy(window.session.world),{})]
        window.reference_labels={(1000,'left'):'HIGH'}
        window.session.state='RUNNING';window.refresh_timeline()
        assert not window.difference_jump.isEnabled()
        assert window.difference_select.count()==1
        assert 'bỏ sót HIGH 1 mốc' in window.timeline_summary.toPlainText()
        window.session.state='STOPPED';window.refresh_timeline();window.difference_jump.click()
        assert window.review_time.value()==1000 and window.review_pair==(rule,ai)
        assert window.timeline.cursor_ms==1000
    finally:window.close()
