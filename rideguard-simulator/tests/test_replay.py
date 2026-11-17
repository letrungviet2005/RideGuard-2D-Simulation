from pathlib import Path
import pytest
from rideguard_sim.replay import load,coordinates

SAMPLE=Path(__file__).parents[1]/'samples/radar_recorded_excerpt.csv'


def test_recorded_excerpt_and_coordinate_provenance():
    rec=load(SAMPLE,'LEFT')
    assert len(rec.frames)==7
    assert [f.t_ms for f in rec.frames]==[0,58,125,185,251,311,378]
    assert {t['track_id'] for f in rec.frames for t in f.targets}=={31}
    forward,lateral=coordinates(rec.frames[0].targets[0])
    assert forward==5.61 and lateral==pytest.approx(-2.73625,abs=.001)
    assert '-2.46,5.04' in rec.raw


def test_de_grouping_empty_and_unassociated(tmp_path):
    file=tmp_path/'record.txt'
    file.write_text('# stats\nD,100,1,31,trai,5,10,-20,0,0,11\nD,100,1,32,phai,8,10,20,0,1,3\nE,100,1,2\nE,165,2,0\n')
    rec=load(file,'RIGHT')
    assert len(rec.frames[0].targets)==2
    assert rec.frames[0].targets[0]['associated']==0
    assert rec.frames[1].targets==[] and rec.frames[1].radar_id=='RIGHT'


@pytest.mark.parametrize('raw',[
    'D,100,1,31,trai,5,10,-20,0,1,1\n',
    'E,100,1,1\n',
    'E,100,1,0\nE,90,2,0\n',
    'D,100,1,31,trai,nan,10,-20,0,1,1\nE,100,1,1\n'])
def test_reject_bad_recording(tmp_path,raw):
    file=tmp_path/'bad.txt';file.write_text(raw)
    with pytest.raises(ValueError):load(file,'LEFT')
