"""Short steering trace: real Qt key events -> Session -> PC rules and AI.

Writes ``trace.csv`` / ``trace.json`` (never into the repo tree unless you ask).
Qt offscreen only, never opens a serial port.  With ``--legacy-scheduler`` the
"before" behaviour is reproduced: steering nudges EGO's heading but does not
take control of EGO, so the scheduled lane change at 2 s reclaims it.
"""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import csv
import json
import math
import os
from pathlib import Path
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import rideguard_sim.ui as ui
from rideguard_sim import model


def level(result,side):
    return result[side]['level'] if result else 'NA'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--scenario',type=int,default=0)
    parser.add_argument('--seconds',type=float,default=5.)
    parser.add_argument('--steer-from',type=float,default=1.)
    parser.add_argument('--steer-to',type=float,default=4.)
    parser.add_argument('--no-ai',action='store_true')
    parser.add_argument('--legacy-scheduler',action='store_true')
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    def forbidden(*a,**k):raise AssertionError('trace must not access Serial/COM')
    ui.SerialLink=forbidden;ui.ports=forbidden
    app=QApplication.instance() or QApplication([])
    w=ui.Window(out);w.timer.stop();w.panel_timer.stop();w.compare.setChecked(not args.no_ai)
    rows=[]
    try:
        w.scenario.setCurrentIndex(args.scenario);w.synthetic();w.start()
        if w.ai:
            limit=time.monotonic()+20
            while not w.ai.ready and time.monotonic()<limit:
                w.ai.poll(w.session.record);time.sleep(.02)
            assert w.ai.ready and not w.ai.error
        ego=w.session.world.ego;car=w.session.world.targets[0]
        dt=.01;pressed=False;steps=round(args.seconds/dt)
        for i in range(steps):
            t=w.session.world.t
            if not args.legacy_scheduler:
                if not pressed and t>=args.steer_from and t<args.steer_to:
                    QTest.keyPress(w.road,Qt.Key_Right);pressed=True
                if pressed and t>=args.steer_to:
                    QTest.keyRelease(w.road,Qt.Key_Right);pressed=False
                command=ego.steer
            else:
                # Naive steering that does NOT take control of EGO.
                command=-w.STEER_BUTTON_LEVEL if args.steer_from<=t<args.steer_to else 0.
                if command:ego.next_heading=ego.heading+command*model.MAX_STEER_YAW_RATE*dt
            w.session.step(dt)
            if w.ai:
                w.ai.poll(w.session.record);time.sleep(.004)   # give the worker time; not a load test
            if i%10==9:
                imu=w.session.last_sent.get('IMU',{})
                rule=w.session.result
                ai=w.ai.latest if w.ai else None
                rows.append(dict(t_s=round(w.session.world.t,3),steer_command=round(command,3),
                    ego_x=round(ego.x,3),ego_y=round(ego.y,3),ego_heading_deg=round(math.degrees(ego.heading),3),
                    ego_control=ego.control,ego_lane_active=int(ego.lane is not None),
                    gz_sim_dps=imu.get('gz_dps'),ay_sim_mps2=imu.get('ay_mps2'),
                    id31_x=round(car.x,3),id31_y=round(car.y,3),
                    rule_left=level(rule,'left'),rule_right=level(rule,'right'),
                    ai_left=level(ai,'left'),ai_right=level(ai,'right')))
        # Let the worker deliver the remaining AI results so AI columns are not just lagging.
        if w.ai:
            limit=time.monotonic()+5
            while time.monotonic()<limit:
                w.ai.poll(w.session.record);time.sleep(.02)
        summary=dict(mode='legacy-scheduler (before)' if args.legacy_scheduler else 'live-steering (after)',
            scenario=args.scenario,final_heading_deg=rows[-1]['ego_heading_deg'],final_y=rows[-1]['ego_y'],
            lane_active_at_end=bool(rows[-1]['ego_lane_active']),ai_enabled=bool(w.ai),
            note='PC rules + AI on synthetic data; no ESP32, not a road-safety measurement')
        with (out/'trace.csv').open('w',newline='',encoding='utf-8') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        (out/'trace.json').write_text(json.dumps(dict(summary=summary,rows=rows),ensure_ascii=False,indent=1),encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False))
    finally:
        w.close()


if __name__=='__main__':main()
