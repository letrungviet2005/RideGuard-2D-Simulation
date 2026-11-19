import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from rideguard_sim.comparison_timeline import ComparisonTimeline
from rideguard_sim.ml.comparison import align,segments,summarize

APP=QApplication.instance() or QApplication([])


def record(t,left='NONE',right='NONE',sid='s',**extra):
    return dict(session_id=sid,t_ms=t,left={'level':left},right={'level':right},**extra)


def test_common_grid_gaps_censored_tail_and_labels_not_agreement():
    rules=[record(1000,'HIGH'),record(1050,'HIGH'),record(1150,'NONE')]
    ai=[record(1000,'HIGH'),record(1050,'MONITOR'),record(1100,'HIGH'),record(1150,'HIGH')]
    labels={(1000,'left'):'NONE',(1050,'left'):'HIGH',(1100,'left'):'HIGH'}
    rows=align(rules,ai,labels,'s',end_ms=1200);summary=summarize(rows)
    s=summary['scoring']
    assert s['common_valid_cells']==2  # 1100 missing ESP32, 1150 censored
    assert s['predictors']['esp32']['high_false_cells']==1
    assert s['predictors']['ai']['high_false_cells']==1  # agreement at 1000 is BOTH wrong
    assert s['predictors']['ai']['high_missed_cells']==1
    assert s['high_uncovered_labelled']['esp32']==1
    assert summary['sources']['esp32']['left']['first_high_ms']==1000
    assert summary['sources']['esp32']['left']['high_ms']==100  # no hold through 1100
    assert summary['sources']['ai']['left']['high_ms']==150
    assert segments(rows)==[{'side':'left','start_ms':1050,'end_ms':1100,'cells':1},
                            {'side':'left','start_ms':1150,'end_ms':1200,'cells':1}]


def test_paused_old_sessions_unknown_and_same_timestamp_heartbeats():
    rules=[record(1000,'HIGH'),record(1000,'UNKNOWN'),record(1050,'HIGH',paused=True),record(1100,'HIGH',sid='old')]
    rows=align(rules,[record(1000,'NONE')],session_id='s',end_ms=1150)
    row=next(r for r in rows if r['timestamp_ms']==1000 and r['side']=='left')
    assert row['esp32']=='HIGH'
    assert all(r['esp32'] is None for r in rows if r['timestamp_ms'] in (1050,1100))
    assert summarize(rows)['sources']['esp32']['left']['high_ms']==50


def test_alignment_half_open_boundary_prevents_counting_one_observation_twice():
    rows=align([record(1025,'HIGH')],[record(1025,'HIGH')],session_id='s',end_ms=1100)
    high=[r for r in rows if r['side']=='left' and r['esp32']=='HIGH']
    assert len(high)==1 and high[0]['timestamp_ms']==1050


def test_timeline_renders_and_only_jumps_when_review_enabled():
    timeline=ComparisonTimeline();timeline.resize(900,240);seen=[];timeline.selected.connect(seen.append)
    timeline.set_rows(align([record(1000,'HIGH')],[record(1000,'NONE')],session_id='s',end_ms=1200))
    timeline.show();APP.processEvents()
    assert not timeline.grab().isNull()
    QTest.mouseClick(timeline,Qt.LeftButton,pos=QPoint(500,40));assert seen==[]
    timeline.enabled_review=True;QTest.mouseClick(timeline,Qt.LeftButton,pos=QPoint(500,40))
    assert len(seen)==1 and seen[0]%50==0
    timeline.close()
