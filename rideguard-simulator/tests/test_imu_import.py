from pathlib import Path
import pytest

from rideguard_sim.imu_import import (GRAVITY, load, to_protocol, describe,
                                     _parse_mount)

SAMPLE = Path(__file__).parents[2] / 'samples' / 'generated' / 'imu_bmi160_synthetic.csv'

HEADER = ('timestamp_us,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,'
          'ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C')


def write(tmp_path, rows, header=HEADER, name='imu.csv'):
    path = tmp_path / name
    path.write_text(header + '\n' + '\n'.join(rows) + '\n', encoding='utf-8')
    return path


def test_sample_loads_and_converts_units():
    rec = load(SAMPLE, mount='identity')
    assert len(rec.samples) == 1001
    first = rec.samples[0]
    # timestamp_us becomes relative milliseconds.
    assert first.t_ms == 0
    assert rec.samples[-1].t_ms == 10000
    # g -> m/s^2, gravity kept: standing level means az = +9.80665.
    assert first.az_mps2 == pytest.approx(GRAVITY, abs=1e-4)
    assert first.ax_mps2 == pytest.approx(0.0, abs=1e-4)
    # Gyro columns are already dps and must not be converted again.
    assert first.gz_dps == pytest.approx(8.0, abs=1e-4)
    assert first.gx_dps == 0.0
    # temperature is carried as metadata only.
    assert first.temperature_C == pytest.approx(31.5)


def test_raw_columns_are_never_converted():
    """RAW is LSB; without full-scale/sensitivity it must stay unused."""
    rec = load(SAMPLE, mount='identity')
    # The generated RAW accel column is scaled by 16384 LSB/g, which would
    # give a wildly different number if it had been used.
    assert rec.samples[0].az_mps2 == pytest.approx(GRAVITY, abs=1e-4)
    assert any('raw' in note.lower() for note in rec.notes)


def test_protocol_fields_match_firmware_limits():
    rec = load(SAMPLE, mount='identity')
    for sample in rec.samples:
        fields = to_protocol(sample)
        assert set(fields) == {'timestamp_ms', 'ax_mps2', 'ay_mps2', 'az_mps2',
                               'gx_dps', 'gy_dps', 'gz_dps'}
        assert abs(fields['ax_mps2']) <= 200
        assert abs(fields['ay_mps2']) <= 200
        assert abs(fields['az_mps2']) <= 200
        assert abs(fields['gx_dps']) <= 2000
        assert abs(fields['gy_dps']) <= 2000
        assert abs(fields['gz_dps']) <= 2000


def test_timestamps_strictly_increase():
    rec = load(SAMPLE, mount='identity')
    times = [s.t_ms for s in rec.samples]
    assert all(b > a for a, b in zip(times, times[1:]))


def test_same_millisecond_keeps_us_and_is_refused_at_protocol_boundary(tmp_path):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,5,30',
        '100500,0,0,16384,0,0,0,0,0,1,0,0,5,30',
        '100900,0,0,16384,0,0,0,0,0,1,0,0,5,30',
    ])
    rec = load(path, mount='identity')
    assert [s.t_us for s in rec.samples]==[0,500,900]
    assert [s.source_timestamp_us for s in rec.samples]==[100000,100500,100900]
    assert rec.quantised_samples==0
    from rideguard_sim.session import Session
    session=Session(tmp_path/'logs')
    session.imu_recording=rec
    with pytest.raises(ValueError,match='share protocol ms'):
        session.start()


@pytest.mark.parametrize('mount,expect_axis', [
    # Mountings that keep the sensor Z axis as vehicle Z (yaw) unchanged.
    ('identity', 'gz'),
    ('xyz<-x,y,z', 'gz'),
    # This mounting sends the sensor Z axis to vehicle Y, so the yaw rate
    # lands on gy instead of gz.
    ('xyz<-x,z,y', 'gy'),
])
def test_mount_declarations(tmp_path, mount, expect_axis):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,5,30',
    ])
    rec = load(path, mount=mount)
    sample = rec.samples[0]
    if expect_axis == 'gz':
        assert sample.gz_dps == pytest.approx(5.0)
        assert sample.gy_dps == 0.0
    else:
        assert sample.gy_dps == pytest.approx(5.0)
        assert sample.gz_dps == 0.0


def test_yaw_sign_flip_inverts_gz(tmp_path):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,5,30',
    ])
    flipped = load(path, mount='identity,yaw=-1').samples[0]
    assert flipped.gz_dps == pytest.approx(-5.0)


def test_axis_sign_flip_inverts_that_axis(tmp_path):
    """A sign flip on the sensor Y axis reverses the yaw rate that lands
    on vehicle Y."""
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,5,30',
    ])
    # Vehicle Y comes from sensor Z here, so flipping sensor Z flips vehicle Y.
    flipped = load(path, mount='xyz<-x,-z,y').samples[0]
    assert flipped.gy_dps == pytest.approx(-5.0)
    assert flipped.gz_dps == 0.0


def test_mount_swaps_accel_axes(tmp_path):
    """A board lying flat turns the sensor's Z-up axis into vehicle Y."""
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,0,30',
    ])
    identity = load(path, mount='identity').samples[0]
    swapped = load(path, mount='xyz<-x,z,y').samples[0]
    assert identity.az_mps2 == pytest.approx(GRAVITY, abs=1e-4)
    assert swapped.ay_mps2 == pytest.approx(GRAVITY, abs=1e-4)
    assert swapped.az_mps2 == pytest.approx(0.0, abs=1e-4)


@pytest.mark.parametrize('mount', [
    'xyz<-x,y',       # missing an axis
    'xyz<-x,y,x',     # repeated axis
    'xyz<-x,y,z,w',   # too many axes
    'abc<-x,y,z',     # bad target frame
    'garbage',        # no mapping
    'xyz<-xy',        # bundled axes are not accepted
])
def test_bad_mount_rejected(mount):
    with pytest.raises(ValueError):
        _parse_mount(mount)


def test_missing_column_rejected(tmp_path):
    path = write(tmp_path, ['100000,0,0,1,0,0,0,0,0,0,30'],
                 header=('timestamp_us,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,'
                         'ax_g,ay_g,az_g,gx_dps,gy_dps,temperature_C'))
    with pytest.raises(ValueError):
        load(path, mount='identity')


def test_non_monotonic_timestamp_rejected(tmp_path):
    path = write(tmp_path, [
        '200000,0,0,16384,0,0,0,0,0,1,0,0,0,30',
        '100000,0,0,16384,0,0,0,0,0,1,0,0,0,30',
    ])
    with pytest.raises(ValueError):
        load(path, mount='identity')


def test_non_finite_value_rejected(tmp_path):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,nan,0,0,0,30',
    ])
    with pytest.raises(ValueError):
        load(path, mount='identity')


def test_out_of_range_accel_rejected(tmp_path):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,100,0,0,0,30',
    ])
    with pytest.raises(ValueError):
        load(path, mount='identity')


def test_out_of_range_gyro_rejected(tmp_path):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,5000,30',
    ])
    with pytest.raises(ValueError):
        load(path, mount='identity')


def test_gap_guard_rejected(tmp_path):
    path = write(tmp_path, [
        '100000,0,0,16384,0,0,0,0,0,1,0,0,0,30',
        '900000,0,0,16384,0,0,0,0,0,1,0,0,0,30',
    ])
    with pytest.raises(ValueError):
        load(path, mount='identity', max_gap_ms=500)


def test_empty_file_rejected(tmp_path):
    path = tmp_path / 'empty.csv'
    path.write_text('', encoding='utf-8')
    with pytest.raises(ValueError):
        load(path, mount='identity')


def test_describe_mentions_mount_and_raw_caveat():
    rec = load(SAMPLE, mount='xyz<-x,z,y')
    text = describe(rec)
    assert 'xyz<-x,z,y' in text
    assert 'raw' in text.lower()
