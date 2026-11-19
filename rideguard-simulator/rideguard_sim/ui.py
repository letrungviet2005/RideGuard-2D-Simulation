import json
import math
from pathlib import Path
import shutil
import time
import traceback
import uuid
from datetime import datetime
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QFormLayout, QGroupBox,
    QPlainTextEdit, QFileDialog, QMessageBox, QCheckBox, QSplitter, QScrollArea)
from .session import Session
from .serial_link import SerialLink, ports
from .protocol import BAUD, budget_bytes_per_second
from .scene import RoadView
from . import scenarios, replay, imu_import
from .imu_plot import ImuPlot
from .comparison_timeline import ComparisonTimeline


class Window(QMainWindow):
    def __init__(self, log_dir):
        super().__init__()
        self.setWindowTitle('RideGuard • Phòng mô phỏng 2D / ESP32')
        self.resize(1460,900)
        self.session=Session(log_dir)
        self.error_log_path=self.session.log_dir/f'app-errors-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.jsonl'
        self.imu_import_attempt=None
        self.repo=Path(__file__).resolve().parents[3]
        self.ml_config=json.loads((self.repo/'ai/configs/sensor-lab.json').read_text())
        self.ai=None;self.rule_history=[];self.compare_seen=set()
        self.review_pair=None;self.visual_history=[];self.visual_tick=-1
        self.reference_labels={};self.timeline_signature=None;self.timeline_rows=[]
        self.last_tick=time.monotonic();self.accumulator=0
        self.setStyleSheet('''QWidget {background:#101c2b;color:#e2eaf2;font-size:13px;}
            QGroupBox {border:1px solid #35475b;border-radius:8px;margin-top:14px;padding:12px;}
            QGroupBox::title {subcontrol-origin:margin;left:10px;color:#74d3cb;}
            QPushButton,QComboBox,QDoubleSpinBox,QSpinBox {background:#20354a;border:1px solid #3a526b;border-radius:5px;padding:6px;}
            QPushButton:hover {background:#31556a;} QPushButton:disabled {color:#718394;}
            QPlainTextEdit {background:#0c1622;border:1px solid #35475b;font-family:Consolas;}
            QScrollArea {border:0;}''')
        root=QWidget();outer=QVBoxLayout(root);self.setCentralWidget(root)
        title=QLabel('RIDEGUARD   /   SENSOR LAB');title.setStyleSheet('font-size:23px;font-weight:700;color:#80e1d4;')
        outer.addWidget(title)
        self.provenance=QLabel('Dữ liệu tổng hợp • mô hình phẳng • không đo độ chính xác an toàn giao thông')
        self.provenance.setWordWrap(True);outer.addWidget(self.provenance)
        split=QSplitter();outer.addWidget(split,1)
        left=QWidget();controls=QVBoxLayout(left)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(left);scroll.setMinimumWidth(380);split.addWidget(scroll)
        visual=QWidget();visual_layout=QHBoxLayout(visual);visual_layout.setContentsMargins(0,0,0,0)
        self.road=RoadView(self.session);visual_layout.addWidget(self.road,1)
        self.road.result_provider=self.visible_rule
        self.ai_road=RoadView(self.session);visual_layout.addWidget(self.ai_road,1);self.ai_road.hide()
        self.ai_road.result_provider=self.visible_ai;self.ai_road.caption='AI · Máy tính'
        self.road.scale_peer=self.ai_road;self.ai_road.scale_peer=self.road
        split.addWidget(visual)
        right=QWidget();panels=QVBoxLayout(right)
        right_scroll=QScrollArea();right_scroll.setWidgetResizable(True);right_scroll.setWidget(right);right_scroll.setMinimumWidth(310);split.addWidget(right_scroll)
        split.setSizes([390,700,370])
        connection=self.group(controls,'1 · KẾT NỐI ESP32')
        self.port=QComboBox();self.scan_ports();connection.addRow('Cổng',self.port)
        self.baud=QComboBox();self.baud.addItems(['921600','460800','115200']);connection.addRow('Baud',self.baud)
        self.button(connection,'Quét cổng',self.scan_ports)
        self.connect_button=self.button(connection,'Kết nối',self.connect_port)
        self.connection_label=QLabel('Chỉ mô phỏng · chưa kết nối');self.connection_label.setWordWrap(True);connection.addRow(self.connection_label)
        mode=self.group(controls,'2 · NGUỒN / ĐIỀU KHIỂN')
        self.compare=QCheckBox('So sánh Bộ luật / AI');mode.addRow(self.compare)
        self.compare.toggled.connect(self.toggle_comparison)
        self.dataset_session=QComboBox();mode.addRow('Dataset session',self.dataset_session)
        self.dataset_root=self.repo/self.ml_config['dataset']
        manifest=self.dataset_root/'sessions.json'
        if manifest.exists():
            for row in json.loads(manifest.read_text()):
                self.dataset_session.addItem(f"{row['session_id']} · {row['split']} · {row['scenario']}",row['session_id'])
        self.button(mode,'Nạp session dataset (radar + IMU)',lambda:self.safe(self.load_dataset,'Nạp dataset'))
        self.scenario=QComboBox();self.scenario.addItems(scenarios.NAMES);mode.addRow(self.scenario)
        self.import_radar=QComboBox();self.import_radar.addItems(['LEFT','RIGHT']);mode.addRow('CSV thuộc radar',self.import_radar)
        self.button(mode,'Nhập CSV / log D/E',self.import_recording)
        self.button(mode,'Về mô phỏng tổng hợp',self.synthetic)
        self.synthetic_imu=QCheckBox('Replay + IMU giả lập (chưa đồng bộ)');mode.addRow(self.synthetic_imu)
        self.imu_profile=QComboBox()
        self.imu_profile.addItems(['user_table_v1','physical_only'])
        self.imu_profile.setToolTip('user_table_v1: đúng 14 cột + RAW/unit; hệ số từ bảng người dùng, chưa xác minh phần cứng. physical_only: tương thích log cũ, không kiểm hệ số RAW.')
        mode.addRow('Profile CSV IMU',self.imu_profile)
        self.imu_mount=QComboBox()
        self.imu_mount.addItems(['identity','xyz<-x,z,y','xyz<-x,y,z','xyz<-x,-y,z','identity,yaw=-1'])
        self.imu_mount.setToolTip('Trục cảm biến → trục xe (X tiến, Y trái, Z lên).\n'
                                  'Dạng "xyz<-x,z,y": X xe ← X cảm biến, Y xe ← Z, Z xe ← Y.\n'
                                  'Dấu "-" nghĩa là đảo chiều trục đó; "yaw=-1" đảo chiều yaw.')
        mode.addRow('Ánh xạ trục IMU',self.imu_mount)
        self.mount_roll=self.number(mode,'Mount roll (độ)',-180,180,0)
        self.mount_pitch=self.number(mode,'Mount pitch (độ)',-180,180,0)
        self.mount_yaw=self.number(mode,'Mount yaw (độ)',-180,180,0)
        self.imu_offset=QDoubleSpinBox();self.imu_offset.setDecimals(3);self.imu_offset.setRange(-600000,600000);self.imu_offset.setSuffix(' ms')
        self.imu_offset.setToolTip('Lệch đồng hồ giữa log IMU và log radar, tính bằng mili-giây.\n'
                                   'Hai log thường dùng hai đồng hồ khác nhau nên phải canh thủ công.')
        mode.addRow('Lệch đồng hồ IMU',self.imu_offset)
        self.synced_logs=QCheckBox('Log đồng bộ (radar + IMU cùng đồng hồ)')
        self.synced_logs.setToolTip('Bật khi log radar và log IMU đến từ cùng một bản ghi phần cứng\n'
                                    '(capture_hardware.py): cả hai giữ nguyên đồng hồ gốc nên không\n'
                                    'được dời riêng. Tắt khi dùng hai nguồn ghi độc lập.')
        mode.addRow(self.synced_logs)
        self.imu_label=QLabel('Chưa nạp log IMU');self.imu_label.setWordWrap(True);mode.addRow(self.imu_label)
        self.button(mode,'Nhập CSV IMU 14 cột',self.import_imu)
        self.button(mode,'Xuất CSV IMU từ mô hình phiên này',self.export_imu)
        self.button(mode,'Bỏ log IMU',self.clear_imu)
        self.speed=QComboBox();self.speed.addItems(['0.5×','1×','2×']);self.speed.setCurrentIndex(1);mode.addRow('Tốc độ thời gian',self.speed)
        self.button(mode,'Bắt đầu / Reset phiên',self.start)
        self.pause_button=self.button(mode,'Tạm dừng / Chạy tiếp',self.pause)
        self.button(mode,'Dừng phiên',lambda:self.safe(lambda:self.session.command('SESSION_STOP','STOPPED')))
        self.review_time=QSpinBox();self.review_time.setRange(0,100000000);self.review_time.setSingleStep(50);self.review_time.setSuffix(' ms')
        mode.addRow('Mốc xem lại sau STOP',self.review_time)
        self.button(mode,'Xem lại đồng bộ (không phát Serial)',lambda:self.safe(self.review_at,'Xem lại'))
        self.status=QLabel('STOPPED');mode.addRow(self.status)
        edit=self.group(controls,'3 · XE / QUỸ ĐẠO')
        self.vehicle=QComboBox();self.vehicle.addItems(['EGO','ID31']);edit.addRow(self.vehicle)
        self.x=self.number(edit,'Dọc X (m)',-10000,10000,0)
        self.y=self.number(edit,'Ngang Y trái (m)',-100,100,0)
        self.velocity=self.number(edit,'Tốc độ (km/h)',0,200,36)
        self.heading=self.number(edit,'Hướng (độ)',-180,180,0)
        self.duration=self.number(edit,'Thời gian chuyển làn (s)',2,12,4)
        self.button(edit,'Đọc xe đang chọn',self.read_vehicle)
        self.button(edit,'Áp dụng khi tạm dừng',self.edit_vehicle)
        self.button(edit,'Chuyển làn trái +3.5 m',lambda:self.lane(3.5))
        self.button(edit,'Chuyển làn phải −3.5 m',lambda:self.lane(-3.5))
        self.button(edit,'Đi thẳng theo hướng đường',self.straight)
        sensors=self.group(controls,'4 · THAM SỐ (PHIÊN MỚI)')
        self.range=self.number(sensors,'Tầm mô phỏng (m)',5,100,40)
        self.fov=self.number(sensors,'Nửa góc quét (độ)',5,75,60)
        self.period=self.number(sensors,'Chu kỳ radar (ms)',40,500,65)
        self.imu_period=self.number(sensors,'Chu kỳ IMU (ms)',10,100,10)
        self.max_targets=self.number(sensors,'Giới hạn mục tiêu',1,4,4)
        self.noise=self.number(sensors,'Nhiễu Y sigma (m)',0,1,0)
        self.dropout=self.number(sensors,'Xác suất mất khung',0,1,0)
        self.unassociated=self.number(sensors,'P(associated=0)',0,1,0)
        self.yaw=self.number(sensors,'Ngưỡng yaw (độ/s)',.2,30,2)
        self.confirm=self.number(sensors,'Xác nhận chuyển hướng (ms)',50,2000,200)
        self.near=self.number(sensors,'Vùng gần (m)',1,100,18)
        controls.addStretch()
        panels.addWidget(QLabel('DỮ LIỆU ĐANG GỬI · IMU theo chu kỳ đã chọn / radar riêng'))
        self.sent=QPlainTextEdit();self.sent.setReadOnly(True);panels.addWidget(self.sent,1)
        self.imu_values=QLabel('IMU: chưa có mẫu');self.imu_values.setWordWrap(True);panels.addWidget(self.imu_values)
        self.imu_plot=ImuPlot();panels.addWidget(self.imu_plot)
        self.output_title=QLabel('KẾT QUẢ THỰC TỪ ESP32');panels.addWidget(self.output_title)
        self.reason=QLabel('Chưa có phản hồi ESP32\n? Mất/thiếu dữ liệu');self.reason.setWordWrap(True);panels.addWidget(self.reason)
        self.ai_reason=QLabel('AI: chưa bật');self.ai_reason.setWordWrap(True);panels.addWidget(self.ai_reason)
        self.comparison_label=QLabel('Chưa đủ kết quả để đối chiếu');self.comparison_label.setWordWrap(True);panels.addWidget(self.comparison_label)
        self.received=QPlainTextEdit();self.received.setReadOnly(True);panels.addWidget(self.received,1)
        self.timeline_area=QGroupBox('SO SÁNH THEO THỜI GIAN CẢM BIẾN');timeline_layout=QVBoxLayout(self.timeline_area)
        timeline_layout.addWidget(QLabel('Xám: NONE · Vàng: MONITOR · Đỏ: HIGH · Tím: thiếu/UNKNOWN/không nhãn · Hồng: bất đồng (không phải sai với nhãn)'))
        self.timeline=ComparisonTimeline();timeline_layout.addWidget(self.timeline)
        self.timeline.selected.connect(self.jump_timeline)
        navigation=QHBoxLayout();timeline_layout.addLayout(navigation)
        self.difference_select=QComboBox();navigation.addWidget(self.difference_select,1)
        self.difference_prev=QPushButton('← Đoạn trước');navigation.addWidget(self.difference_prev)
        self.difference_next=QPushButton('Đoạn sau →');navigation.addWidget(self.difference_next)
        self.difference_jump=QPushButton('Nhảy tới đoạn đã chọn (sau STOP)');navigation.addWidget(self.difference_jump)
        for button in (self.difference_prev,self.difference_next,self.difference_jump):button.setEnabled(False)
        self.difference_prev.clicked.connect(lambda:self.step_difference(-1))
        self.difference_next.clicked.connect(lambda:self.step_difference(1))
        self.difference_jump.clicked.connect(self.jump_difference)
        self.timeline_summary=QPlainTextEdit();self.timeline_summary.setReadOnly(True);self.timeline_summary.setMaximumHeight(95)
        timeline_layout.addWidget(self.timeline_summary)
        outer.addWidget(self.timeline_area);self.timeline_area.hide()
        log_buttons=QHBoxLayout();outer.addLayout(log_buttons)
        toggle=QPushButton('Hiện / ẩn log');toggle.clicked.connect(lambda:self.logs.setVisible(not self.logs.isVisible()));log_buttons.addWidget(toggle)
        export=QPushButton('Xuất log TX/RX + cấu hình');export.clicked.connect(self.export_log);log_buttons.addWidget(export)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumHeight(140);self.logs.hide();outer.addWidget(self.logs)
        self.timer=QTimer();self.timer.timeout.connect(self.tick);self.timer.start(16)
        self.panel_timer=QTimer();self.panel_timer.timeout.connect(self.refresh_panels);self.panel_timer.start(150)
        for combo in left.findChildren(QComboBox):
            combo.setMinimumContentsLength(10)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)

    def group(self,layout,title):
        group=QGroupBox(title);form=QFormLayout(group);form.setRowWrapPolicy(QFormLayout.WrapLongRows);layout.addWidget(group);return form

    def button(self,form,title,action):
        button=QPushButton(title);button.clicked.connect(action);form.addRow(button);return button

    def number(self,form,title,lo,hi,value):
        widget=QDoubleSpinBox();widget.setRange(lo,hi);widget.setValue(value);widget.setDecimals(2);form.addRow(title,widget);return widget

    def safe(self,action,operation='Thao tác'):
        try:
            action()
            return True
        except Exception as error:
            self.report_error(error,operation)
            return False

    def report_error(self,error,operation):
        record=dict(time=datetime.now().astimezone().isoformat(),operation=operation,
            error_type=type(error).__name__,message=str(error),file=self.imu_import_attempt,
            profile=self.imu_profile.currentText(),mount=self.imu_mount.currentText(),
            mount_degrees=[self.mount_roll.value(),self.mount_pitch.value(),self.mount_yaw.value()],
            offset_ms=self.imu_offset.value(),keep_original_clock=self.synced_logs.isChecked(),
            speed=self.speed.currentText(),state=self.session.state,
            traceback=''.join(traceback.format_exception(type(error),error,error.__traceback__)))
        saved=f'Log lỗi: {self.error_log_path.resolve()}'
        try:
            with self.error_log_path.open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(record,ensure_ascii=False)+'\n')
        except OSError as log_error:
            saved=f'Không ghi được file log: {log_error}. Chi tiết vẫn hiện bên dưới.'
        self.session.events.append(f'LỖI [{operation}]: {error}\n{saved}')
        self.logs.setPlainText('\n'.join(self.session.events[-25:]));self.logs.show()
        box=QMessageBox(self);box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f'RideGuard — {operation}')
        box.setTextFormat(Qt.PlainText)
        box.setText(str(error))
        box.setInformativeText(saved)
        box.setDetailedText(json.dumps(record,ensure_ascii=False,indent=2))
        box.exec()

    def scan_ports(self):
        self.port.clear()
        for device,description in ports():
            self.port.addItem(f'{device} · {description}',device)

    def connect_port(self):
        def action():
            if self.session.link:
                self.session.disconnect()
            else:
                if not self.port.currentData():
                    raise ValueError('Không có cổng Serial; vẫn có thể xem mô phỏng offline.')
                self.session.link=SerialLink(self.port.currentData(),int(self.baud.currentText()))
                self.session.state='STOPPED';self.session.result=None
        self.safe(action)
        self.refresh_panels()

    def link_ready(self):
        link=self.session.link
        ready=getattr(link,'ready',None)
        return link is not None and (ready is None or ready.is_set())

    def start(self):
        def action():
            if self.compare.isChecked():
                if not (self.repo/self.ml_config['artifacts']/'trusted-model.json').exists():
                    raise ValueError('MODEL_MISSING: chạy ai/sensor_lab.py train trước; không dùng NONE thay model thiếu')
                if self.ai:self.ai.close();self.ai=None
            cfg=self.session.radar.config
            cfg.range_m=self.range.value();cfg.half_fov_deg=self.fov.value()
            cfg.period_ms=int(self.period.value());cfg.max_targets=int(self.max_targets.value())
            cfg.noise_m=self.noise.value();cfg.dropout=self.dropout.value();cfg.unassociated=self.unassociated.value()
            speed=[.5,1,2][self.speed.currentIndex()]
            self.session.playback_speed=speed
            self.session.imu_period_ms=int(self.imu_period.value())
            if not self.session.imu_recording and self.session.link and budget_bytes_per_second(cfg.period_ms,cfg.max_targets,speed,
                                                               self.session.imu_period_ms)>int(self.baud.currentText())/10*.8:
                raise ValueError('Vượt ngân sách Serial 80%. Chọn 921600, giảm tốc độ, tăng chu kỳ IMU/radar hoặc giảm số mục tiêu.')
            self.session.rules.update(yaw_threshold_dps=self.yaw.value(),confirm_ms=int(self.confirm.value()),near_m=self.near.value())
            self.session.replay_imu=self.synthetic_imu.isChecked()
            if self.session.imu_recording:
                if self.session.imu_recording.profile!=self.imu_profile.currentText():
                    raise ValueError('Profile changed: re-import CSV before starting')
                recorded=self.session.imu_recording
                angles=[self.mount_roll.value(),self.mount_pitch.value(),self.mount_yaw.value()]
                if recorded.mount!=self.imu_mount.currentText() or recorded.metadata.get('applied_rotation_deg')!=angles or recorded.metadata.get('import_rebase')!=(not self.synced_logs.isChecked()):
                    raise ValueError('Mount/clock mode changed: re-import CSV before starting')
                self.session.imu_offset_ms=self.imu_offset.value()
            if self.session.link and not self.session.wait_link_ready(8):
                raise ValueError('Serial chưa sẵn sàng. Đóng mọi Serial Monitor, rồi thử lại.')
            self.session.start(self.scenario.currentIndex())
            self.rule_history=[];self.compare_seen=set();self.review_pair=None;self.visual_history=[];self.visual_tick=-1
            self.timeline_signature=None;self.timeline_rows=[];self.timeline.set_rows([])
            self.reference_labels={}
            if self.session.dataset:
                # Evaluation-only labels stay in the UI/reporting path.
                from .ml.comparison import label_map
                self.reference_labels=label_map(self.session.dataset.folder)
            if self.compare.isChecked():
                from .ml.bridge import Bridge
                self.ai=Bridge(self.repo,self.session.envelope.session)
                self.session.sensor_observer=self.observe_sensors
                self.session.result_observer=self.observe_rule
                self.common_hash=__import__('hashlib').sha256();self.common_count=0
            else:
                self.session.sensor_observer=None;self.session.result_observer=None
            self.vehicle.clear();self.vehicle.addItems(['EGO',*[f'ID{c.track_id}' for c in self.session.world.targets]])
            self.accumulator=0
        self.safe(action,operation='Bắt đầu phiên')

    def toggle_comparison(self,enabled):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            self.compare.blockSignals(True);self.compare.setChecked(not enabled);self.compare.blockSignals(False)
            return
        self.ai_road.setVisible(enabled)
        self.timeline_area.setVisible(enabled)
        if enabled:self.resize(1880,980)
        self.road.caption='Bộ luật · ESP32' if enabled else 'GÓC NHÌN TỪ TRÊN • CAMERA BÁM XE'
        if not enabled and self.ai:self.ai.close();self.ai=None

    def load_dataset(self):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            raise ValueError('Dừng phiên/chờ ACK trước khi đổi dataset')
        from .ml.dataset_replay import DatasetReplay
        replay=DatasetReplay(self.dataset_root,self.dataset_session.currentData())
        replay.load_visual_oracle()
        self.reset_comparison_view()
        self.session.dataset=replay;self.session.replay=None;self.session.clear_imu()
        self.session.result=None;self.session.last_sent={};self.session.world.targets=[]
        self.session.world.t=0;replay.update_visual_only(self.session.world)
        self.speed.setCurrentIndex(0);self.synced_logs.setChecked(True)
        self.session.radar.config.max_targets=3;self.max_targets.setValue(3)
        self.session.events.append(f"Dataset {replay.meta['session_id']}: {len(replay.events)} sensor events; {replay.empty} OK/0; {replay.dropped} DROPPED không phát; Oracle chỉ dùng vẽ")
        self.imu_label.setText('Dataset: 4800 mẫu IMU 200 Hz; timestamp chung; phát 0.5× không bỏ mẫu')

    def reset_comparison_view(self):
        if self.ai:self.ai.close();self.ai=None
        self.session.sensor_observer=None;self.session.result_observer=None
        self.rule_history=[];self.review_pair=None;self.visual_history=[];self.visual_tick=-1
        self.reference_labels={};self.timeline_signature=None;self.timeline_rows=[]
        self.timeline.set_rows([]);self.timeline.cursor_ms=None
        self.timeline.enabled_review=False
        for button in (self.difference_prev,self.difference_next,self.difference_jump):button.setEnabled(False)
        self.timeline_summary.clear();self.difference_select.clear()

    def observe_sensors(self,events,t_us):
        for event in events:self.common_hash.update(event.canonical());self.common_count+=1
        self.ai.submit(events,t_us)
        if t_us//50000!=self.visual_tick:
            import copy
            self.visual_tick=t_us//50000
            if len(self.visual_history)<5000:
                self.visual_history.append((t_us//1000,copy.deepcopy(self.session.world),copy.deepcopy(self.session.last_sent)))
        if events:self.session.record('COMMON_STREAM',dict(type='STREAM_CHECKPOINT',t_ms=t_us//1000,
            session_id=self.session.envelope.session,sensor_count=self.common_count,stream_sha256=self.common_hash.hexdigest()))

    def observe_rule(self,result):
        self.rule_history.append(result)
        self.rule_history=self.rule_history[-2000:]

    def visible_ai(self):
        if self.review_pair is not None:return self.review_pair[1]
        return self.ai.visible(round(self.session.world.t*1000),self.session.state) if self.ai else None

    def visible_rule(self):
        if self.review_pair is not None:return self.review_pair[0]
        return self.session.visible_result()

    def review_at(self):
        if self.session.state!='STOPPED' or self.session.pending:
            raise ValueError('Chỉ xem lại sau STOP và ACK; không chạy lại inference hoặc gửi dữ liệu')
        if not self.visual_history or not self.ai:raise ValueError('Chưa có lượt chạy so sánh để xem lại')
        import copy
        stamp,world,sent=min(self.visual_history,key=lambda row:abs(row[0]-self.review_time.value()))
        def nearest(rows):
            candidates=[r for r in rows if r['session_id']==self.session.envelope.session and abs(r['t_ms']-stamp)<=25 and not r.get('paused')]
            return min(candidates,key=lambda r:abs(r['t_ms']-stamp)) if candidates else None
        self.review_pair=(nearest(self.rule_history),nearest(self.ai.history))
        self.session.world=copy.deepcopy(world);self.session.last_sent=copy.deepcopy(sent)
        self.timeline.cursor_ms=stamp;self.timeline.update()
        self.refresh_panels();self.road.update();self.ai_road.update()

    def jump_timeline(self,stamp):
        if self.session.state!='STOPPED' or self.session.pending:return
        self.review_time.setValue(stamp);self.safe(self.review_at,'Xem lại timeline')

    def jump_difference(self):
        stamp=self.difference_select.currentData()
        if stamp is not None:self.jump_timeline(stamp)

    def step_difference(self,direction):
        if self.difference_select.count():
            self.difference_select.setCurrentIndex((self.difference_select.currentIndex()+direction)%self.difference_select.count())
            self.jump_difference()

    def refresh_timeline(self):
        if not self.compare.isChecked() or not self.ai:return
        from .ml.comparison import align,summarize
        # Freeze end against replay cursor movement; review is read-only.
        end_ms=self.visual_history[-1][0] if self.visual_history else round(self.session.world.t*1000)
        signature=(len(self.rule_history),self.rule_history[-1].get('seq') if self.rule_history else None,
                   len(self.ai.history),self.ai.history[-1]['t_ms'] if self.ai.history else None,end_ms)
        stopped=self.session.state=='STOPPED' and not self.session.pending
        self.timeline.enabled_review=stopped
        for button in (self.difference_prev,self.difference_next,self.difference_jump):button.setEnabled(stopped)
        if signature==self.timeline_signature:return
        self.timeline_signature=signature
        self.timeline_rows=align(self.rule_history,list(self.ai.history),self.reference_labels,
            self.session.envelope.session,end_ms=end_ms)
        summary=summarize(self.timeline_rows);self.timeline.set_rows(self.timeline_rows)
        selected=self.difference_select.currentData();self.difference_select.clear()
        for segment in summary['disagreement_segments']:
            self.difference_select.addItem(f"{segment['side']} · {segment['start_ms']/1000:.2f}–{segment['end_ms']/1000:.2f}s",segment['start_ms'])
        index=self.difference_select.findData(selected)
        if index>=0:self.difference_select.setCurrentIndex(index)
        lines=['HIGH: tính trên các ô 50 ms có kết quả; khoảng thiếu không được kéo dài.']
        for source,sides in summary['sources'].items():
            parts=[]
            for side,values in sides.items():
                first=f"{values['first_high_observation_ms']/1000:.3f}s (lưới {values['first_high_ms']/1000:.2f}s)" if values['first_high_ms'] is not None else 'chưa quan sát'
                parts.append(f"{side}: HIGH đầu {first}; tổng {values['high_ms']/1000:.2f}s; thiếu {values['unknown_ms']/1000:.2f}s")
            lines.append(source.upper()+' · '+' | '.join(parts))
        scoring=summary['scoring']
        if scoring['labelled_after_warmup_cells']:
            lines.append(f"Nhãn: {scoring['common_valid_cells']}/{scoring['labelled_after_warmup_cells']} mốc×bên chung hợp lệ (sau warm-up; không chấm đuôi thiếu nhãn); HIGH thiếu kết quả: ESP32 {scoring['high_uncovered_labelled']['esp32']}, AI {scoring['high_uncovered_labelled']['ai']}")
            if scoring['common_valid_cells']:
                parts=[]
                for source,m in scoring['predictors'].items():
                    parts.append(f"{source}: bỏ sót HIGH {m['high_missed_cells']} mốc; báo HIGH nhầm {m['high_false_cells']} mốc; macro-F1 {m['macro_f1']:.3f}")
                lines.append(' | '.join(parts))
            else:lines.append('Chưa có mốc chung hợp lệ để chấm; không suy ra accuracy từ mức đồng ý.')
        else:lines.append('Chưa có nhãn để chấm; mức đồng ý không phải độ chính xác.')
        self.timeline_summary.setPlainText('\n'.join(lines))

    def pause(self):
        self.safe(lambda:self.session.command('RESUME' if self.session.state=='PAUSED' else 'PAUSE',
                                              'RUNNING' if self.session.state=='PAUSED' else 'PAUSED'))

    def import_recording(self):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên và chờ ACK trước khi đổi nguồn.');return
        path,_=QFileDialog.getOpenFileName(self,'Radar CSV / D,E logger',str(Path(__file__).parent.parent/'samples'),'Radar (*.csv *.txt *.log);;All (*)')
        if path:
            rebase=not self.synced_logs.isChecked()
            def action():
                recording=self.session.load_replay(path,self.import_radar.currentText(),rebase=rebase)
                self.reset_comparison_view()
                self.session.dataset=None
                self.session.result=None;self.session.last_sent={}
                self.session.events.append(
                    f'Đã nhập {len(recording.frames)} khung; giữ nguyên raw CSV/log'
                    + ('' if rebase else '; giữ đồng hồ gốc (log đồng bộ)'))
            self.safe(action)

    def synthetic(self):
        if self.session.state!='STOPPED' or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên trước khi đổi nguồn.');return
        self.session.dataset=None;self.session.replay=None;self.session.clear_imu();self.session.last_sent={};self.session.result=None
        self.reset_comparison_view()
        self.imu_label.setText('Chưa nạp log IMU')

    def import_imu(self):
        """Import a BMI160 IMU log (timestamp_us, accel g, gyro dps)."""
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên và chờ ACK trước khi đổi nguồn.');return
        path,_=QFileDialog.getOpenFileName(self,'CSV IMU — 14 cột / physical_only legacy',
                                          str(Path(__file__).parent.parent/'samples'),
                                          'IMU CSV (*.csv *.txt *.log);;All (*)')
        if not path:
            return
        self.safe(lambda:self.load_imu_path(path),operation='Nhập CSV IMU')

    def load_imu_path(self,path):
        self.imu_import_attempt=str(Path(path).resolve())
        if self.session.dataset:
            raise ValueError('Đang dùng dataset đồng bộ; chọn Về mô phỏng tổng hợp trước khi nhập IMU riêng')
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            raise ValueError('Dừng phiên trước khi nhập IMU')
        recording=self.session.load_imu(path,mount=self.imu_mount.currentText(),offset_ms=self.imu_offset.value(),
            rebase=not self.synced_logs.isChecked(),profile=self.imu_profile.currentText(),
            rotation_deg=(self.mount_roll.value(),self.mount_pitch.value(),self.mount_yaw.value()))
        self.reset_comparison_view()
        self.session.result=None
        self.imu_label.setText(imu_import.describe(recording))
        self.session.events.append('CSV IMU đã nhập; timestamp gốc µs giữ nguyên, RAW không truyền xuống bo')
        self.refresh_panels()

    def export_imu(self):
        path,_=QFileDialog.getSaveFileName(self,'Xuất IMU tổng hợp 14 cột','live-imu.csv','CSV (*.csv)')
        if path:self.safe(lambda:self.session.export_imu(path))

    def clear_imu(self):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên trước khi đổi nguồn.');return
        def action():
            self.session.clear_imu();self.imu_label.setText('Chưa nạp log IMU')
        self.safe(action)

    def chosen(self):
        cars=[self.session.world.ego,*self.session.world.targets]
        return cars[min(max(0,self.vehicle.currentIndex()),len(cars)-1)]

    def read_vehicle(self):
        car=self.chosen();self.road.selected=car.track_id
        self.x.setValue(car.x);self.y.setValue(car.y);self.velocity.setValue(car.speed*3.6);self.heading.setValue(math.degrees(car.heading))

    def edit_vehicle(self):
        if self.session.dataset:return
        if self.session.state!='PAUSED' or self.session.pending or self.session.replay:
            QMessageBox.information(self,'Chỉnh xe','Chỉ chỉnh vị trí trong mô phỏng tổng hợp khi đã tạm dừng.');return
        car=self.chosen();car.x=self.x.value();car.y=self.y.value();car.speed=self.velocity.value()/3.6
        car.heading=math.radians(self.heading.value());car.lane=None;car.settle()

    def lane(self,delta):
        if self.session.dataset:return
        if self.session.replay and not self.session.replay_imu:
            return
        self.chosen().change_lane(delta,self.duration.value())

    def straight(self):
        if self.session.dataset:return
        if self.session.state=='PAUSED' and not self.session.pending:
            car=self.chosen();car.lane=None;car.heading=0;car.settle()

    def tick(self):
        now=time.monotonic();elapsed=now-self.last_tick;self.last_tick=now
        self.session.poll()
        if self.ai:
            self.ai.poll(self.session.record)
            if self.ai.error and self.session.state=='RUNNING':
                self.session.disconnect();self.session.events.append(self.ai.error)
        if self.session.state=='RUNNING' and not self.session.pending:
            # Cap catch-up and never flood old sensor messages after a UI stall.
            if elapsed>.25:
                self.accumulator=0
                self.safe(lambda:self.session.command('PAUSE','PAUSED'))
            else:
                self.accumulator+=elapsed*[.5,1,2][self.speed.currentIndex()]
                step=.005 if self.session.imu_recording or self.session.dataset else .01
                while self.accumulator>=step:
                    try:
                        self.session.step(step)
                    except Exception as error:
                        self.session.disconnect()
                        self.report_error(error,'Phát dữ liệu cảm biến');break
                    self.accumulator-=step
        else:
            self.accumulator=0
        self.road.update()
        self.ai_road.update()

    def refresh_panels(self):
        connected=self.session.link is not None
        ready=self.link_ready()
        self.connect_button.setText('Ngắt kết nối' if connected else 'Kết nối')
        if connected and not ready:
            self.connection_label.setText('Đang chờ board khởi động xong (dọn nhiễu boot ROM)…')
        elif connected:
            self.connection_label.setText('Serial đang mở · chờ/kết quả theo phiên')
        else:
            self.connection_label.setText('Chỉ mô phỏng · chưa kết nối')
        self.status.setText(self.session.state+(' · chờ ACK' if self.session.pending else ''))
        if self.session.replay:
            if self.session.imu_recording is not None:
                synced='ĐỒNG BỘ CÙNG ĐỒNG HỒ' if self.synced_logs.isChecked() else 'HAI ĐỒNG HỒ RIÊNG'
                self.provenance.setText(
                    f'CSV RADAR + IMU • NGUỒN GHÉP: đồng bộ CHƯA XÁC MINH ({synced} là khai báo) • '
                    f'mount {self.session.imu_recording.mount} • '
                    f'lệch đồng hồ {self.session.imu_offset_ms} ms • '
                    f'nguồn {self.session.imu_recording.metadata.get("source","UNKNOWN")} • hoạt ảnh EGO minh họa, không tích phân IMU')
            elif self.session.replay_imu:
                self.provenance.setText('Radar CSV + IMU giả lập, nguồn ghép chưa xác minh đồng bộ')
            else:
                self.provenance.setText('RADAR CSV • nguồn chưa xác minh • Chưa có IMU • Y-axis / tan(angle) là giả định tọa độ')
            if self.session.replay_finished:
                self.provenance.setText('ĐÃ HẾT BẢN GHI • Phiên đã dừng (không lặp, không phát khung rỗng). '
                                        'Bấm "Bắt đầu / Reset phiên" để chạy lại.')
        else:
            self.provenance.setText('DỮ LIỆU TỔNG HỢP • IMU thân xe phẳng • Không mô phỏng nghiêng xe / IMU gương')
            if self.session.imu_recording:
                self.provenance.setText('IMU CSV + radar/hoạt ảnh tổng hợp: NGUỒN GHÉP CHƯA ĐỒNG BỘ; EGO minh họa, không tích phân IMU')
        if self.session.dataset:
            self.provenance.setText(f"SYNTHETIC v1 · {self.session.dataset.meta['session_id']} · một stream chung 200 Hz/65 ms · xe: Oracle giả lập chỉ để vẽ · ESP32 mount ±0.4 m khác dataset/AI ±0.25 m · chỉnh xe bị khóa khi replay")
        rec=self.session.imu_recording
        if rec:
            self.imu_label.setText(imu_import.describe(rec))
            end=self.session.imu_index if self.session.state in ('RUNNING','PAUSED') or self.session.imu_index else len(rec.samples)
            samples=rec.samples[max(0,end-2000):end]
            self.imu_plot.set_points([(imu_import.sample_us(s)/1e6,s.ax_mps2,s.ay_mps2,s.az_mps2,s.gx_dps,s.gy_dps,s.gz_dps) for s in samples])
            if samples:
                s=samples[-1]
                self.imu_values.setText(f'IMU preview: a=({s.ax_mps2:.4f}, {s.ay_mps2:.4f}, {s.az_mps2:.4f}) m/s²; ω=({s.gx_dps:.4f}, {s.gy_dps:.4f}, {s.gz_dps:.4f}) °/s')
        elif self.session.dataset:
            from bisect import bisect_right
            dataset=self.session.dataset;end=bisect_right(dataset.imu_times,self.session.world.t)
            self.imu_plot.set_points(dataset.imu_points[max(0,end-2000):end])
            sample=self.session.last_sent.get('IMU')
            if sample:self.imu_values.setText(f"IMU dataset: t={sample['t_ms']} ms · a=({sample['ax_mps2']:.3f}, {sample['ay_mps2']:.3f}, {sample['az_mps2']:.3f}) m/s² · yaw={sample['gz_dps']:.3f} °/s")
        else:
            rows=self.session.live_imu_rows[-1000:]
            self.imu_plot.set_points([(r[0]/1e6,*(float(v)*9.80665 for v in r[7:10]),*(float(v) for v in r[10:13])) for r in rows])
        self.sent.setPlainText(json.dumps(self.session.last_sent,ensure_ascii=False,indent=2))
        result=self.visible_rule()
        self.received.setPlainText(json.dumps(result,indent=2) if result else 'Chưa có phản hồi ESP32 hợp lệ / mới trong 1 giây.')
        if result:
            lines=[f"ESP32: xử lý t={result['t_ms']} ms · tuổi nhận trên PC={1000*(time.monotonic()-self.session.result_wall):.0f} ms"]
            for side,title in [('left','Trái'),('right','Phải')]:
                state=result[side];lines.append(f"{title}: {state['level']} · {state.get('reason_code','')}\nradar={state.get('radar_id')} / track={state.get('track_id')}")
            self.reason.setText('\n'.join(lines))
        else:
            stale=self.session.link is not None and self.session.result is not None and time.monotonic()-self.session.result_wall>1
            self.reason.setText('? Mất/thiếu dữ liệu\n'+('Phản hồi ESP32 quá hạn (>1000 ms)' if stale else 'Chưa có phản hồi ESP32'))
        self.logs.setPlainText('\n'.join(self.session.events[-25:]))
        if self.ai:
            from .ml.bridge import match_result
            ai_result=self.visible_ai()
            if ai_result:
                self.ai_reason.setText('AI · Máy tính | t='+str(ai_result['t_ms'])+' ms | tuổi sensor='+str(round(self.session.world.t*1000)-ai_result['t_ms'])+f' ms | tuổi nhận PC={1000*(time.monotonic()-self.ai.latest_wall):.0f} ms\n'+
                    '\n'.join(f"{s}: {ai_result[s]['level']} · {ai_result[s]['reason_code']} · Điểm mô hình "+', '.join(f'{k}={v:.3f}' for k,v in ai_result[s]['scores'].items()) for s in ('left','right')))
            else:self.ai_reason.setText('AI: '+(self.ai.error or ('PAUSE / STOPPED' if self.session.state!='RUNNING' else 'Đang nạp model / warm-up / kết quả stale')))
            for rule in self.rule_history:
                if rule['seq'] in self.compare_seen:continue
                comparison=match_result(rule,self.ai.history,self.session.envelope.session)
                if comparison:
                    self.compare_seen.add(rule['seq']);self.session.record('COMPARISON',dict(type='MATCH',**comparison))
            matched=match_result(result,self.ai.history,self.session.envelope.session) if ai_result and (self.session.state=='RUNNING' or self.review_pair is not None) else None
            self.comparison_label.setText(('Đối chiếu cùng phiên, dung sai ±25 ms: '+json.dumps(matched,ensure_ascii=False)) if matched else 'Chưa đủ kết quả để đối chiếu (thiếu bên / warm-up / UNKNOWN / lệch timestamp)')
            if self.review_pair is not None:
                self.provenance.setText('XEM LẠI ĐÃ GHI · không realtime, không gửi Serial · '+self.provenance.text())
                self.ai_reason.setText('XEM LẠI · '+('\n'.join(self.ai_reason.text().splitlines()[1:])) if ai_result else 'XEM LẠI · thiếu kết quả AI tại mốc này')
                self.reason.setText('XEM LẠI · '+('\n'.join(self.reason.text().splitlines()[1:])) if result else 'XEM LẠI · thiếu RESULT ESP32 tại mốc này')
        else:self.ai_reason.setText('AI: chưa bật');self.comparison_label.setText('Chưa đủ kết quả để đối chiếu')
        if result and getattr(self,'last_panel',None)!=(result['session_id'],result['seq']):
            self.last_panel=(result['session_id'],result['seq'])
            self.session.mark('panel',result)
        self.refresh_timeline()

    def export_log(self):
        if not self.session.log:
            return
        path,_=QFileDialog.getSaveFileName(self,'Xuất bằng chứng','rideguard-session.jsonl','JSON Lines (*.jsonl)')
        if path:
            self.session.log.flush()
            if Path(path).resolve()!=self.session.log_path.resolve():
                shutil.copyfile(self.session.log_path,path)
            if self.session.replay:
                Path(path+'.source.txt').write_text(self.session.replay.raw,encoding='utf-8')
            if self.timeline_rows:
                from .ml.comparison import export_comparison
                export_comparison(self.timeline_rows,Path(path+'.comparison'))

    def closeEvent(self,event):
        if self.ai:self.ai.close()
        self.session.close();event.accept()
