import csv
import json
import math
from pathlib import Path
import pytest
from rideguard_sim import imu_csv as c,imu_import
from rideguard_sim.session import Session

REFERENCE=Path(__file__).parents[2]/'samples/reference/user-provided-14rows.csv'


@pytest.mark.parametrize('column,value,expected',[
    ('gx_dps','not-a-number','cột gx_dps'),
    ('ax_g','99','IMU_RAW_UNIT'),
])
def test_import_diagnostic_uses_original_line_after_comments(tmp_path,column,value,expected):
    lines=REFERENCE.read_text().splitlines()
    row=lines[1].split(',');row[c.HEADER.index(column)]=value
    p=tmp_path/'bad-value.csv'
    p.write_text('# capture notes\n\n'+lines[0]+'\n'+','.join(row)+'\n')
    with pytest.raises(ValueError) as error:
        imu_import.load(p,profile=c.PROFILE)
    assert expected in str(error.value)
    assert '4' in str(error.value) and column in str(error.value)


def test_user_reference_exact_times_units_and_no_labels():
    r=imu_import.load(REFERENCE,profile=c.PROFILE)
    assert len(r.samples)==14
    assert r.span_us==64985
    assert r.rate_hz==pytest.approx(200.0461645)
    assert r.metadata['source']=='USER_PROVIDED' and r.metadata['labels'] is None
    assert r.samples[0].source_timestamp_us==663646
    assert r.samples[-1].source_timestamp_us==728631
    assert r.samples[0].ax_mps2==pytest.approx(.50654*9.80665,abs=1e-5)
    assert r.samples[0].gz_dps==-2.6075
    assert r.samples[0].az_mps2<0  # orientation is unknown; negative az is valid
    assert '64.985 ms' in imu_import.describe(r)


@pytest.mark.parametrize('scenario',c.SCENARIOS)
def test_45s_200hz_profiles_quantization_and_physics(tmp_path,scenario):
    p=tmp_path/(scenario+'.csv');c.generate(p,scenario=scenario)
    r=imu_import.load(p,profile=c.PROFILE)
    assert len(r.samples)==9000 and r.span_us==44995000
    assert all(b.t_us-a.t_us==5000 for a,b in zip(r.samples,r.samples[1:]))
    rows=list(csv.DictReader(p.open()))
    assert list(rows[0])==c.HEADER
    for row in rows:
        for raw,physical,scale in zip(c.HEADER[1:7],c.HEADER[7:13],[.000122]*3+[.0175]*3):
            assert -32768<=int(row[raw])<=32767
            assert float(row[physical])==pytest.approx(int(row[raw])*scale,abs=1e-9)
        assert float(row['temperature_C'])==29.75
    gyro=[s.gz_dps for s in r.samples]
    if scenario in ('lane_left','lane_right'):
        assert min(gyro)<-2 and max(gyro)>2
        assert abs(sum(gyro)*.005)<.02
        assert gyro[0]==gyro[-1]==0
    if scenario in ('stationary','straight'):
        assert max(abs(v) for v in gyro)==0
        assert r.samples[0].az_mps2==pytest.approx(c.G,abs=.001)
    if scenario=='steady_left':assert min(gyro)>7.9
    if scenario=='bumpy':
        assert max(gyro)==0
        assert max(s.az_mps2 for s in r.samples)-min(s.az_mps2 for s in r.samples)>1
    if scenario=='tilted':assert abs(r.samples[0].ay_mps2)>1


def test_seed_mount_and_no_overwrite(tmp_path):
    a=tmp_path/'a.csv';b=tmp_path/'b.csv';d=tmp_path/'d.csv'
    for p in (a,b):c.generate(p,duration=1,seed=42,gyro_noise_dps=.1,mount_deg=(20,-10,5))
    c.generate(d,duration=1,seed=43,gyro_noise_dps=.1,mount_deg=(20,-10,5))
    assert a.read_bytes()==b.read_bytes()!=d.read_bytes()
    with pytest.raises(ValueError,match='exists'):c.generate(a)
    with pytest.raises(ValueError,match='saturation'):c.quantize([4.1,0,0],[0,0,0])
    with pytest.raises(ValueError,match='saturation'):c.quantize([0,0,1],[0,0,600])
    for v in (float('nan'),float('inf')):
        with pytest.raises(ValueError):c.generate(tmp_path/'bad.csv',rate=v)


def test_mount_roundtrip_preserves_gravity_without_normalization(tmp_path):
    p=tmp_path/'mount.csv';angles=(20,-30,40)
    c.generate(p,scenario='stationary',duration=1,mount_deg=angles)
    r=imu_import.load(p,profile=c.PROFILE,rotation_deg=angles)
    s=r.samples[0]
    assert s.ax_mps2==pytest.approx(0,abs=.002)
    assert s.ay_mps2==pytest.approx(0,abs=.002)
    assert s.az_mps2==pytest.approx(c.G,abs=.002)
    force,_=c.model(.1,'steady_left')
    assert math.sqrt(sum(x*x for x in force))>c.G  # centripetal acceleration retained


@pytest.mark.parametrize('mutation', ['header','raw','nan','time','duplicate','unit','missing','temperature'])
def test_invalid_csv_refused(tmp_path,mutation):
    lines=REFERENCE.read_text().splitlines();rows=[line.split(',') for line in lines]
    if mutation=='header':rows[0][1],rows[0][2]=rows[0][2],rows[0][1]
    if mutation=='raw':rows[1][1]='32768'
    if mutation=='nan':rows[1][7]='nan'
    if mutation=='time':rows[2][0]='1'
    if mutation=='duplicate':rows[2][0]=rows[1][0]
    if mutation=='unit':rows[1][7]='1'
    if mutation=='missing':rows[1].pop()
    if mutation=='temperature':rows[1][-1]=''
    p=tmp_path/'bad.csv';p.write_text('\n'.join(','.join(r) for r in rows))
    with pytest.raises(ValueError):imu_import.load(p,profile=c.PROFILE)


def test_us_scheduler_offset_and_budget_do_not_drop_or_retime(tmp_path):
    s=Session(tmp_path/'logs');s.load_imu(REFERENCE,profile=c.PROFILE,offset_ms=.015)
    try:
        s.start(1)
        s.send_recorded_imu(10)
        assert 'IMU' not in s.last_sent
        s.send_recorded_imu(15)
        assert s.last_sent['IMU']['t_ms']==0
        s.send_recorded_imu(5006)
        assert s.imu_index==1
        s.send_recorded_imu(5007)
        assert s.imu_index==2 and s.last_sent['IMU']['t_ms']==5
        assert REFERENCE.read_text().splitlines()[1].startswith('663646,4152,')
    finally:s.close()
    p=tmp_path/'200.csv';c.generate(p,duration=1)
    s=Session(tmp_path/'budget');s.load_imu(p,profile=c.PROFILE)
    class Link:
        baud=921600
    s.link=Link()
    with pytest.raises(ValueError,match='Unsupported Serial budget'):s.preflight()
    s.playback_speed=.5
    assert s.preflight()<921600/10*.8
    s.imu_offset_ms=-.001
    with pytest.raises(ValueError,match='outside'):s.preflight()


def test_live_export_reflects_same_session_clock(tmp_path):
    s=Session(tmp_path/'live')
    try:
        s.start(0)
        for _ in range(600):s.step()
        p=tmp_path/'live.csv';s.export_imu(p)
        r=imu_import.load(p,profile=c.PROFILE)
        assert len(r.samples)==len(s.live_imu_rows)
        assert r.metadata['clock_id']==s.envelope.session
        assert r.metadata['source']=='SYNTHETIC_LIVE'
        assert min(x.gz_dps for x in r.samples)<0<max(x.gz_dps for x in r.samples)
    finally:s.close()


def test_metadata_hash_and_empty_sample_count_refused(tmp_path):
    p=tmp_path/'hash.csv';c.generate(p,duration=1)
    p.write_text(p.read_text()+'\n')
    with pytest.raises(ValueError,match='SHA-256'):imu_import.load(p,profile=c.PROFILE)
    with pytest.raises(ValueError):c.generate(tmp_path/'empty.csv',duration=1e-10,rate=1)
    s=Session(tmp_path/'speed')
    for speed in (0,-1,float('nan')):
        s.playback_speed=speed
        with pytest.raises(ValueError,match='finite and positive'):s.preflight()
