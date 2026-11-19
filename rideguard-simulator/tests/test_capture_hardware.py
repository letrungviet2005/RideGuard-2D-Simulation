"""Tests for the hardware capture parser.

The logger stream is synthesised here from the exact record formats written
by ``firmware/src/sensor_logger.cpp``.  The tests pin down that the parser
keeps the radar and IMU timestamps on their original common origin, because
that is what makes the two files replayable together.
"""
import csv

from capture_hardware import (
    IMU_HEADER,
    RADAR_HEADER,
    Capture,
    write_scenario_files,
)
from capture_hardware import main as capture_hardware_main


def make_stream():
    return [
        '# RideGuard hardware logger v1',
        '# BMI160 ready',
        'RADAR,65,0,31,trai,5.61,10.18,-26,0,1,11',
        'RADAR,130,1,31,trai,5.69,10.29,-25,0,1,12',
        'IMU,10,0.001,0.002,1.000,0.100,0.200,0.300,24.50',
        'IMU,20100,0.001,0.002,1.000,0.100,0.200,0.300,24.51',
        'SCENARIO,5000,start',
        'RADAR,200,2,31,trai,5.80,10.40,-24,0,1,13',
        'IMU,100200,0.010,0.020,0.990,1.500,0.100,-0.300,24.52',
        'SCENARIO,300000,end',
    ]


def test_parses_radar_and_imu_rows():
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    assert len(capture.radar) == 3
    assert len(capture.imu) == 3
    assert capture.unknown == []


def test_radar_row_fields():
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    row = capture.radar[0]
    assert row[RADAR_HEADER.index('ms')] == 65
    assert row[RADAR_HEADER.index('frame')] == 0
    assert row[RADAR_HEADER.index('id')] == 31
    assert row[RADAR_HEADER.index('huong')] == 'trai'
    assert row[RADAR_HEADER.index('y_m')] == '5.61'
    assert row[RADAR_HEADER.index('v_kmh')] == '10.18'
    assert row[RADAR_HEADER.index('goc')] == '-26'
    assert row[RADAR_HEADER.index('cao_toc')] == 0
    assert row[RADAR_HEADER.index('lien_ket')] == 1
    assert row[RADAR_HEADER.index('ton_tai')] == 11


def test_imu_row_fields():
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    row = capture.imu[0]
    assert row[IMU_HEADER.index('timestamp_us')] == 10
    assert row[IMU_HEADER.index('az_g')] == '1.000'
    assert row[IMU_HEADER.index('gz_dps')] == '0.300'
    assert row[IMU_HEADER.index('temperature_C')] == '24.50'


def test_scenario_markers_are_paired():
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    assert len(capture.scenarios) == 1
    start_us, end_us, name = capture.scenarios[0]
    assert start_us == 5000
    assert end_us == 300000
    assert name == 'scenario_00'


def test_unpaired_start_is_not_reported_as_scenario():
    capture = Capture()
    capture.feed_line('SCENARIO,5000,start')
    assert capture.scenarios == []


def test_stats_report_spans():
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    stats = capture.stats()
    assert stats['radar_rows'] == 3
    assert stats['imu_rows'] == 3
    assert stats['radar_span_ms'] == 135
    assert stats['imu_span_ms'] == 100
    assert stats['scenarios'] == 1
    assert stats['unknown'] == 0


def test_malformed_rows_go_to_unknown_not_silently_dropped():
    capture = Capture()
    capture.feed_line('RADAR,65,0')
    capture.feed_line('IMU,10,0.1')
    capture.feed_line('GARBAGE,1,2')
    assert capture.radar == []
    assert capture.imu == []
    assert len(capture.unknown) == 3


def test_comments_are_kept_separately():
    capture = Capture()
    capture.feed_line('# hello')
    assert capture.comments == ['# hello']
    assert capture.unknown == []


def test_validate_accepts_a_clean_capture():
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    assert capture.validate() == []


def test_validate_detects_duplicate_ids_in_one_frame():
    capture = Capture()
    capture.feed_line('RADAR,65,0,31,trai,5.61,10.18,-26,0,1,11')
    capture.feed_line('RADAR,65,0,31,phai,5.61,10.18,26,0,1,11')
    problems = capture.validate()
    assert any('id trùng' in problem for problem in problems)


def test_validate_detects_more_than_four_targets():
    capture = Capture()
    for index in range(5):
        capture.feed_line(f'RADAR,65,0,{index},trai,5.61,10.18,-26,0,1,11')
    problems = capture.validate()
    assert any('>4' in problem for problem in problems)


def test_validate_detects_a_backwards_timestamp():
    capture = Capture()
    capture.feed_line('RADAR,130,1,31,trai,5.69,10.29,-25,0,1,12')
    capture.feed_line('RADAR,65,2,31,trai,5.61,10.18,-26,0,1,11')
    problems = capture.validate()
    assert any('giảm' in problem for problem in problems)


def test_scenario_files_slice_on_shared_origin(tmp_path):
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    start_us, end_us, name = capture.scenarios[0]
    radar_rows = [row for row in capture.radar if start_us <= row[0] * 1000 <= end_us]
    imu_rows = [row for row in capture.imu if start_us <= row[0] <= end_us]

    radar_path, imu_path = write_scenario_files(tmp_path, name, radar_rows, imu_rows)
    assert radar_path.exists() and imu_path.exists()

    with open(radar_path, encoding='utf-8') as handle:
        radar = list(csv.reader(handle))
    with open(imu_path, encoding='utf-8') as handle:
        imu = list(csv.reader(handle))

    assert radar[0] == RADAR_HEADER
    assert imu[0] == IMU_HEADER
    # All three radar rows (65/130/200 ms) fall inside the 5 ms .. 300 ms
    # window; the two IMU rows at 100.2 ms and 20.1 ms do too, while the
    # 10 us sample is before the window and is excluded.
    assert len(radar) == 4  # header + 3 radar rows
    assert len(imu) == 3    # header + 2 IMU rows

    assert radar[1][0] == '65'
    assert radar[2][0] == '130'
    assert radar[3][0] == '200'


def test_end_to_end_from_a_firmware_style_log(tmp_path):
    """The whole chain: firmware log -> capture tool -> app importers.

    The generated log follows the exact record format written by
    ``firmware/src/sensor_logger.cpp``.  The two resulting CSV files must
    load in the desktop app and must share one origin, so the radar and the
    IMU describe the same physical timeline.
    """
    from rideguard_sim import imu_import, replay

    log_lines = ['# RideGuard hardware logger v1', '# READY']
    for frame, t in enumerate(range(0, 3000, 65)):
        log_lines.append(f'RADAR,{t},{frame},31,trai,{12.0 - t * 0.001:.2f},8.00,-18,0,1,0')
    for index in range(300):
        log_lines.append(f'IMU,{index * 10000},0.00100,0.00200,1.00000,'
                         f'0.0000,0.0000,{index * 0.001:.4f},24.50')
    log_lines.insert(2, 'SCENARIO,0,start')
    log_lines.append('SCENARIO,3000000,end')

    log_path = tmp_path / 'fw.log'
    log_path.write_text('\n'.join(log_lines), encoding='utf-8')

    out_dir = tmp_path / 'out'
    code = capture_hardware_main(['--from-file', str(log_path),
                                  '--out', str(out_dir)])
    assert code == 0

    radar_path = out_dir / 'radar_scenario_00.csv'
    imu_path = out_dir / 'imu_scenario_00.csv'
    assert radar_path.exists() and imu_path.exists()

    recording = replay.load(str(radar_path), 'LEFT', rebase=False)
    imu = imu_import.load(str(imu_path), mount='identity', rebase=False)

    assert len(recording.frames) == 47
    assert len(imu.samples) == 300
    # One shared origin: both streams start at 0 and end at the same time.
    assert recording.frames[0].t_ms == 0
    assert imu.samples[0].t_ms == 0
    assert recording.frames[-1].t_ms == imu.samples[-1].t_ms
    assert recording.frames[0].targets[0]['track_id'] == 31
    assert recording.frames[0].targets[0]['y_m'] == 12.0
    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    start_us, end_us, name = capture.scenarios[0]
    radar_rows = [row for row in capture.radar if start_us <= row[0] * 1000 <= end_us]
    imu_rows = [row for row in capture.imu if start_us <= row[0] <= end_us]

    radar_path, imu_path = write_scenario_files(tmp_path, name, radar_rows, imu_rows)
    assert radar_path.exists() and imu_path.exists()

    with open(radar_path, encoding='utf-8') as handle:
        radar = list(csv.reader(handle))
    with open(imu_path, encoding='utf-8') as handle:
        imu = list(csv.reader(handle))

    assert radar[0] == RADAR_HEADER
    assert imu[0] == IMU_HEADER
    # All three radar rows (65/130/200 ms) fall inside the 5 ms .. 300 ms
    # window; the two IMU rows at 100.2 ms and 20.1 ms do too, while the
    # 10 us sample is before the window and is excluded.
    assert len(radar) == 4  # header + 3 radar rows
    assert len(imu) == 3    # header + 2 IMU rows

    # The radar row before the window (t=65 ms) is still inside it, because
    # the window starts at 5 ms.
    assert radar[1][0] == '65'
    assert radar[2][0] == '130'
    assert radar[3][0] == '200'


def test_written_csv_can_be_read_back_by_the_app_importers(tmp_path):
    from rideguard_sim import imu_import, replay

    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    radar_path, imu_path = write_scenario_files(tmp_path, 'session',
                                                capture.radar, capture.imu)

    # The radar CSV must load with the desktop replay parser.
    recording = replay.load(str(radar_path), 'LEFT')
    assert len(recording.frames) == 3
    assert recording.frames[0].t_ms == 0
    assert recording.frames[0].targets[0]['track_id'] == 31
    assert recording.frames[0].targets[0]['y_m'] == 5.61
    assert recording.frames[0].targets[0]['angle_deg'] == -26

    # The IMU CSV must load with the BMI160 importer.
    imu = imu_import.load(str(imu_path), mount='identity')
    assert len(imu.samples) == 3
    assert imu.samples[0].t_ms == 0
    assert imu.samples[0].az_mps2 == 9.80665
    assert imu.samples[2].t_ms == 100


def test_rebasing_keeps_radar_and_imu_on_the_same_origin(tmp_path):
    """The two importers each rebase on their own first sample.

    A synchronised capture must therefore be loaded with ``rebase=False`` on
    both sides; only then do the radar file and the IMU file still describe
    the same physical timeline.
    """
    from rideguard_sim import imu_import, replay

    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    start_us, end_us, name = capture.scenarios[0]
    radar_rows = [row for row in capture.radar if start_us <= row[0] * 1000 <= end_us]
    imu_rows = [row for row in capture.imu if start_us <= row[0] <= end_us]

    radar_path, imu_path = write_scenario_files(tmp_path, name, radar_rows, imu_rows)

    recording = replay.load(str(radar_path), 'LEFT', rebase=False)
    imu = imu_import.load(str(imu_path), mount='identity', rebase=False)

    # The original stamps are preserved.
    assert [f.t_ms for f in recording.frames] == [65, 130, 200]
    assert [s.t_ms for s in imu.samples] == [20, 100]

    # The radar frame at 130 ms and the IMU sample at 20 ms are 110 ms apart
    # in the capture, and they must stay 110 ms apart.
    radar_at = next(f.t_ms for f in recording.frames if f.t_ms == 130)
    imu_at = next(s.t_ms for s in imu.samples if s.t_ms == 20)
    assert radar_at - imu_at == 110


def test_independent_rebasing_would_shift_the_streams(tmp_path):
    """Guards the reason the capture tool keeps the original origin.

    Rebasing each file on its own first row moves the radar and the IMU by
    different amounts, so the two streams no longer line up.
    """
    from rideguard_sim import imu_import, replay

    capture = Capture()
    for line in make_stream():
        capture.feed_line(line)
    start_us, end_us, name = capture.scenarios[0]
    radar_rows = [row for row in capture.radar if start_us <= row[0] * 1000 <= end_us]
    imu_rows = [row for row in capture.imu if start_us <= row[0] <= end_us]

    radar_path, imu_path = write_scenario_files(tmp_path, name, radar_rows, imu_rows)

    rebased_radar = replay.load(str(radar_path), 'LEFT', rebase=True)
    rebased_imu = imu_import.load(str(imu_path), mount='identity', rebase=True)

    # After rebasing, the radar frame that was at 130 ms sits at 65 ms and
    # the IMU sample that was at 20 ms sits at 0 ms.  The true separation of
    # 110 ms has become 65 ms, so the streams no longer describe the same
    # physical timeline.
    radar_at = next(f.t_ms for f in rebased_radar.frames if f.t_ms == 65)
    imu_at = next(s.t_ms for s in rebased_imu.samples if s.t_ms == 0)
    assert radar_at - imu_at == 65
    assert radar_at - imu_at != 110
