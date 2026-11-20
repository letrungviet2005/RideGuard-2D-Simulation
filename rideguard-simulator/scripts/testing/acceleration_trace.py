"""Actual Qt setup -> scheduler -> shared PC-rule/AI stream, no COM or flash."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import argparse
import csv
import json
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont,QFontDatabase
from rideguard_sim.ui import Window

def run(out,config):
    out.mkdir(parents=True,exist_ok=False);app=QApplication.instance() or QApplication([])
    if os.name=='nt':QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts/segoeui.ttf'))
    app.setFont(QFont('Segoe UI',9))
    w=Window(out);w.timer.stop();w.panel_timer.stop()
    try:
        w.compare.setChecked(True);w.ai_variant.setCurrentIndex(w.ai_variant.findData(config))
        assert w.ai_variant.currentData()==config
        p=w.setup_panel;p.cards[0].speed.setValue(36);c=p.cards[31];c.speed.setValue(43.2);c.mode.setCurrentIndex(1)
        for row in [(0,2,0),(2,5,1),(5,8,-1)]:c.add_row(row)
        p.save();assert 'Đã lưu' in p.summary.text()
        w.steer_sensitivity.setValue(.5);w.start();assert w.session.state=='RUNNING' and w.session.world.t==0
        limit=time.monotonic()+20
        while not w.ai.ready and time.monotonic()<limit:w.ai.poll(w.session.record);time.sleep(.01)
        assert w.ai.ready and not w.ai.error
        rows=[]
        for i in range(800):
            if i==100:w.set_steer(-1)
            if i==300:w.set_steer(0)
            w.session.step(.01);w.ai.poll(w.session.record)
            if i%5==4:
                e=w.session.world.ego;t=w.session.world.targets[0]
                rows.append(dict(t_s=w.session.world.t,ego_x=e.x,ego_y=e.y,ego_kmh=e.speed*3.6,ego_accel=e.commanded_acceleration,
                    id31_x=t.x,id31_y=t.y,id31_kmh=t.speed*3.6,id31_accel=t.commanded_acceleration,
                    radar=json.dumps(w.session.last_sent.get('RADAR_FRAMELEFT',{})),imu=json.dumps(w.session.last_sent.get('IMU',{})),
                    rule=json.dumps(w.session.result),ai_observed=json.dumps(w.ai.history[-1] if w.ai.history else None)))
            time.sleep(.002)
        limit=time.monotonic()+20
        while time.monotonic()<limit:
            w.ai.poll(w.session.record)
            if w.ai.error:raise AssertionError(w.ai.error)
            if w.ai.checkpoint and w.ai.checkpoint['sensor_count']==w.common_count:break
            time.sleep(.01)
        expected=w.common_hash.hexdigest()
        assert w.ai.checkpoint['stream_sha256']==expected==w.session.pc_rules.stream_hash.hexdigest()
        # AI in online trace is asynchronous: align by its own t_ms, never pretend
        # the last received observation belongs to the row's simulation timestamp.
        ai_by_time={r['t_ms']:r for r in w.ai.history}
        for row in rows:row['ai_aligned']=json.dumps(ai_by_time.get(round(row['t_s']*1000)))
        with (out/'trace.csv').open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
        w.session.command('SESSION_STOP','STOPPED');w.refresh_panels();w.tabs.setCurrentIndex(2);w.resize(1366,768);w.show();app.processEvents()
        w.control_stack.currentWidget().ensureWidgetVisible(w.steer_sensitivity);app.processEvents();w.grab().save(str(out/'steering-sensitivity.png'))
        w.control_stack.currentWidget().ensureWidgetVisible(w.setup_panel.cards[31]);app.processEvents();w.grab().save(str(out/'initial-setup.png'))
        summary=dict(status='PASS',source='Qt offscreen + PC rules + actual AI worker',config=config,sensor_count=w.common_count,
            common_sha256=expected,sim_duration_s=w.session.world.t,rule_results=len(w.rule_history),ai_results=len(w.ai.history),serial_accesses=0,
            id31_final_kmh=w.session.world.targets[0].speed*3.6,ego_final_y=w.session.world.ego.y,labels='Chưa có nhãn cho quỹ đạo đã chỉnh')
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))
    finally:w.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--config',default='ai/configs/sensor-lab-v2-filtered.json');a=p.parse_args();run(a.out.resolve(),a.config)
