"""No-board integration smoke: actual Window/Session and trusted AI subprocess.

Drives deterministic scheduler steps, not a GUI FPS/load benchmark. Never opens COM.
"""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
import rideguard_sim.ui as ui


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    def forbidden(*args,**kwargs):raise AssertionError('PC smoke must not access Serial/COM inventory')
    ui.SerialLink=forbidden;ui.ports=forbidden
    app=QApplication.instance() or QApplication([])
    if os.name=='nt':QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts/segoeui.ttf'))
    app.setFont(QFont('Segoe UI',9))
    w=ui.Window(out);w.timer.stop();w.panel_timer.stop()
    try:
        w.scenario.setCurrentIndex(2);w.synthetic();w.compare.setChecked(True)
        w.yellow_mode.setCurrentIndex(1);w.yellow.setValue(15);w.near.setValue(10)
        w.start();assert w.session.state=='RUNNING' and w.session.link is None and not w.session.pending
        limit=time.monotonic()+20
        while not w.ai.ready and time.monotonic()<limit:
            w.ai.poll(w.session.record);time.sleep(.02)
        assert w.ai.ready and not w.ai.error
        target=w.session.world.targets[0];target.x=-8;target.y=3.5;target.speed=10;target.settle()
        w.imu_mode.setCurrentIndex(1);w.gyro_value.setValue(8)
        for i in range(300):
            w.session.step(.01);w.ai.poll(w.session.record)
            if w.ai.error:raise AssertionError(w.ai.error)
            time.sleep(.006)
        expected=w.common_hash.hexdigest();limit=time.monotonic()+10
        while time.monotonic()<limit:
            w.ai.poll(w.session.record)
            if w.ai.checkpoint and w.ai.checkpoint['sensor_count']==w.common_count:break
            time.sleep(.01)
        assert w.session.pc_rules.stream_hash.hexdigest()==expected==w.ai.checkpoint['stream_sha256']
        assert w.session.pc_rules.sensor_count==w.common_count==w.ai.checkpoint['sensor_count']
        assert any(r['left']['level']=='HIGH' for r in w.rule_history)
        assert any(r['left']['level']!='UNKNOWN' for r in w.ai.history)
        w.refresh_panels();w.tabs.setCurrentIndex(2);w.resize(1366,768);w.show();app.processEvents()
        w.control_stack.currentWidget().ensureWidgetVisible(w.gyro_value);app.processEvents()
        w.grab().save(str(out/'pc-ai-manual.png'))
        assert (w.width(),w.height())==(1366,768)
        w.tabs.setCurrentIndex(0);app.processEvents();w.grab().save(str(out/'pc-rule-source.png'))
        w.tabs.setCurrentIndex(3);w.control_stack.currentWidget().ensureWidgetVisible(w.yellow)
        app.processEvents();w.grab().save(str(out/'pc-applied-thresholds.png'))
        before=len(w.session.live_imu_rows);w.pause();w.gyro_value.setValue(-8);w.session.step(.01)
        assert len(w.session.live_imu_rows)==before and w.session.pending is None
        w.pause();w.session.step(.01);w.ai.poll(w.session.record)
        limit=time.monotonic()+10
        while time.monotonic()<limit:
            w.ai.poll(w.session.record)
            if w.ai.checkpoint and w.ai.checkpoint['sensor_count']==w.common_count:break
            time.sleep(.01)
        expected=w.common_hash.hexdigest()
        assert expected==w.session.pc_rules.stream_hash.hexdigest()==w.ai.checkpoint['stream_sha256']
        assert w.common_count==w.ai.checkpoint['sensor_count']==w.session.pc_rules.sensor_count
        w.session.command('SESSION_STOP','STOPPED');w.refresh_panels()
        w.session.log.flush();log=w.session.log_path
        from rideguard_sim.ml.comparison import read_log,align,export_comparison
        rules,ai,sid=read_log(log);assert rules and ai and all(r['type']=='RULE_PC' for r in rules)
        export_comparison(align(rules,ai,session_id=sid,end_ms=3000),out/'comparison')
        summary=dict(status='PASS',rule_source='pc',rule_version='pc-port-2',serial_accesses=0,
            sensor_count=w.common_count,pc_ai_common_sha256=expected,rule_results=len(rules),ai_results=len(ai),
            applied_rules=w.session.applied_rules,model_sha256=hashlib.sha256((w.repo/w.ml_config['artifacts']/'model.joblib').read_bytes()).hexdigest(),
            log=str(log.relative_to(out)),hardware_verified=False)
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        # Re-open actual evidence without a model or transport; source stays PC.
        ui.QFileDialog.getOpenFileName=lambda *a:(str(log),'JSONL')
        w.open_comparison_log();assert w.rule_title()=='Bộ luật · Máy tính' and w.session.historical_no_geometry
        print(json.dumps(summary,ensure_ascii=False))
    finally:w.close()


if __name__=='__main__':main()
