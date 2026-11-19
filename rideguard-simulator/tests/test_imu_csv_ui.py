"""Qt widget checks; no board, Serial or physical-display claim."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from rideguard_sim.ui import Window
from rideguard_sim.imu_csv import PROFILE
from rideguard_sim.imu_import import load
from test_imu_csv14 import REFERENCE

_app=QApplication.instance() or QApplication([])


def test_gui_import_summary_plot_offset_and_unknown_result(tmp_path):
    window=Window(tmp_path)
    try:
        window.imu_offset.setValue(.125)
        window.load_imu_path(str(REFERENCE))
        assert '14 mẫu' in window.imu_label.text()
        assert '64.985 ms' in window.imu_label.text()
        assert 'USER_PROVIDED' in window.imu_label.text()
        assert len(window.imu_plot.points)==14
        assert window.session.imu_offset_ms==.125
        assert 'CHƯA ĐỒNG BỘ' in window.provenance.text()
        assert window.session.result is None
        assert 'Chưa có phản hồi' in window.reason.text()
        assert 'm/s²' in window.imu_values.text() and '°/s' in window.imu_values.text()
        window.show();_app.processEvents()
        assert not window.imu_plot.grab().isNull()
    finally:window.close()


def test_gui_export_uses_live_samples_and_json(tmp_path,monkeypatch):
    window=Window(tmp_path/'logs')
    output=tmp_path/'live.csv'
    try:
        window.session.start(0)
        for _ in range(100):window.session.step()
        monkeypatch.setattr('rideguard_sim.ui.QFileDialog.getSaveFileName',lambda *a,**kw:(str(output),'CSV'))
        window.export_imu()
        rec=load(output,profile=PROFILE)
        assert rec.metadata['source']=='SYNTHETIC_LIVE'
        assert rec.metadata['clock_id']==window.session.envelope.session
        assert len(rec.samples)==100
    finally:window.close()


def test_import_error_has_file_context_and_persistent_log_before_start(tmp_path,monkeypatch):
    import json
    window=Window(tmp_path/'logs');seen=[]
    def capture(box):
        seen.append((box.text(),box.informativeText(),box.detailedText()))
        return 0
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',capture)
    bad=tmp_path/'broken.csv'
    bad.write_text('timestamp_us;ax_raw\n10;2\n')
    try:
        assert window.safe(lambda:window.load_imu_path(bad),operation='Nhập CSV IMU') is False
        assert window.session.log is None  # failure saved even before SESSION_START
        record=json.loads(window.error_log_path.read_text(encoding='utf-8'))
        assert record['operation']=='Nhập CSV IMU'
        assert record['file']==str(bad.resolve())
        assert record['profile']=='user_table_v1'
        assert 'IMU_HEADER' in record['message']
        assert record['traceback'] and 'ValueError' in record['traceback']
        assert seen and 'Log lỗi:' in seen[0][1]
        assert 'IMU_HEADER' in seen[0][0] and 'IMU_HEADER' in window.logs.toPlainText()
        assert window.session.imu_recording is None
    finally:window.close()


def test_start_error_explains_ms_collision_with_sample_values(tmp_path,monkeypatch):
    import json
    window=Window(tmp_path/'logs')
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:0)
    # Valid µs ordering but unsupported by the firmware's millisecond clock.
    lines=REFERENCE.read_text().splitlines()
    first=lines[1].split(',');second=lines[2].split(',')
    first[0]='100000';second[0]='100500'
    p=tmp_path/'collision.csv';p.write_text('\n'.join([lines[0],','.join(first),','.join(second)])+'\n')
    try:
        window.load_imu_path(p)
        window.start()
        record=json.loads(window.error_log_path.read_text(encoding='utf-8'))
        assert record['operation']=='Bắt đầu phiên'
        assert 'IMU_TIMESTAMP_MS_COLLISION' in record['message']
        assert '500 µs' in record['message'] and '0 ms' in record['message']
        assert 'Giảm tốc độ phát không sửa' in record['message']
        assert window.session.state=='STOPPED' and window.session.log is None
    finally:window.close()


def test_error_dialog_retains_details_when_log_cannot_be_written(tmp_path,monkeypatch):
    window=Window(tmp_path/'logs');seen=[]
    monkeypatch.setattr('rideguard_sim.ui.QMessageBox.exec',lambda box:seen.append((box.informativeText(),box.detailedText())))
    window.error_log_path=tmp_path  # a directory cannot be opened as a log file
    try:
        assert window.safe(lambda:(_ for _ in ()).throw(ValueError('diagnostic retained'))) is False
        assert 'Không ghi được file log' in seen[0][0]
        assert 'diagnostic retained' in seen[0][1]
        assert 'diagnostic retained' in window.logs.toPlainText()
    finally:window.close()
