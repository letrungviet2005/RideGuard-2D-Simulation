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
    QPlainTextEdit, QFileDialog, QMessageBox, QCheckBox, QSplitter, QScrollArea, QTabBar, QStackedWidget)
from .session import Session
from .serial_link import SerialLink, ports
from .protocol import BAUD, budget_bytes_per_second
from .scene import RoadView
from . import scenarios, replay, imu_import
from .imu_plot import ImuPlot
from .comparison_timeline import ComparisonTimeline
from .control_panel import build_pages, install_wheel_guard, open_field_guide


class Window(QMainWindow):
    STEER_BUTTON_LEVEL=.6  # hold-buttons; arrow keys use full lock (1.0); slider any level
    def __init__(self, log_dir):
        super().__init__()
        self.setWindowTitle('RideGuard • Phòng mô phỏng 2D / ESP32')
        self.resize(1366,768)
        self.session=Session(log_dir)
        self.session.rule_source="pc";self.capabilities_asked=False
        self.vehicle_dirty=set();self.vehicle_form_loading=False;self.vehicle_form_id=None
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
            QTabBar::tab {background:#20354a;padding:7px;border:1px solid #3a526b;}
            QTabBar::tab:selected {background:#31556a;color:#80e1d4;font-weight:600;}
            QPlainTextEdit {background:#0c1622;border:1px solid #35475b;font-family:Consolas;}
            QScrollArea {border:0;}''')
        root=QWidget();outer=QVBoxLayout(root);self.setCentralWidget(root)
        title=QLabel('RIDEGUARD   /   SENSOR LAB');title.setStyleSheet('font-size:23px;font-weight:700;color:#80e1d4;')
        outer.addWidget(title)
        self.provenance=QLabel('Dữ liệu tổng hợp • mô hình phẳng • không đo độ chính xác an toàn giao thông')
        self.provenance.setWordWrap(True);outer.addWidget(self.provenance)
        self.control_status=QLabel('Nguồn: Tình huống dựng sẵn | EGO: Theo kịch bản')
        self.control_status.setStyleSheet('font-weight:700;color:#ffd166;');self.control_status.setWordWrap(True);outer.addWidget(self.control_status)
        self.tabs=QTabBar();self.tabs.setExpanding(True);self.tabs.setUsesScrollButtons(False)
        for title in ('Kết nối ESP32','Nguồn & điều khiển','Xe & quỹ đạo','Tham số phiên mới'):self.tabs.addTab(title)
        outer.addWidget(self.tabs)
        session_row=QHBoxLayout();outer.addLayout(session_row)
        self.start_button=QPushButton('Bắt đầu / Reset phiên');self.start_button.clicked.connect(self.start);session_row.addWidget(self.start_button)
        self.pause_button=QPushButton('Tạm dừng / Chạy tiếp');self.pause_button.clicked.connect(self.pause);session_row.addWidget(self.pause_button)
        self.stop_button=QPushButton('Dừng phiên');self.stop_button.clicked.connect(lambda:self.safe(lambda:self.session.command('SESSION_STOP','STOPPED')));session_row.addWidget(self.stop_button)
        self.status=QLabel('STOPPED');session_row.addWidget(self.status,1)
        self.connection_shortcut=QPushButton('Kết nối ESP32 →');self.connection_shortcut.clicked.connect(lambda:self.tabs.setCurrentIndex(0));session_row.addWidget(self.connection_shortcut)
        self.session_summary=QLabel();self.session_summary.setWordWrap(True);outer.addWidget(self.session_summary)
        split=QSplitter();outer.addWidget(split,1)
        self.control_stack=QStackedWidget();self.control_stack.setMinimumWidth(320);split.addWidget(self.control_stack)
        self.tabs.currentChanged.connect(self.control_stack.setCurrentIndex)
        visual=QWidget();visual_layout=QHBoxLayout(visual);visual_layout.setContentsMargins(0,0,0,0)
        self.road=RoadView(self.session);visual_layout.addWidget(self.road,1)
        self.road.setMinimumSize(220,200)
        self.road.caption='Bộ luật · ESP32'
        self.road.result_provider=self.visible_rule
        self.ai_road=RoadView(self.session);visual_layout.addWidget(self.ai_road,1);self.ai_road.setMinimumSize(220,200)
        self.ai_road.result_provider=self.visible_ai;self.ai_road.caption='AI · Máy tính'
        self.road.scale_peer=self.ai_road;self.ai_road.scale_peer=self.road
        split.addWidget(visual)
        right=QWidget();panels=QVBoxLayout(right)
        right_scroll=QScrollArea();right_scroll.setWidgetResizable(True);right_scroll.setWidget(right);right_scroll.setMinimumWidth(230);split.addWidget(right_scroll)
        split.setSizes([350,740,250])
        build_pages(self,self.control_stack)
        self.tabs.setCurrentIndex(1)
        input_title=QLabel('DỮ LIỆU ĐANG GỬI · IMU / radar theo nguồn đã chọn')
        input_title.setWordWrap(True);panels.addWidget(input_title)
        self.sent=QPlainTextEdit();self.sent.setReadOnly(True);self.sent.setMinimumHeight(60);panels.addWidget(self.sent,1)
        self.imu_values=QLabel('IMU: chưa có mẫu');self.imu_values.setWordWrap(True);panels.addWidget(self.imu_values)
        self.imu_plot=ImuPlot();panels.addWidget(self.imu_plot)
        self.output_title=QLabel('KẾT QUẢ THỰC TỪ ESP32');panels.addWidget(self.output_title)
        self.reason=QLabel('Chưa có phản hồi ESP32\n? Mất/thiếu dữ liệu');self.reason.setWordWrap(True);panels.addWidget(self.reason)
        self.ai_reason=QLabel('AI: chưa bật');self.ai_reason.setWordWrap(True);panels.addWidget(self.ai_reason)
        self.comparison_label=QLabel('Chưa đủ kết quả để đối chiếu');self.comparison_label.setWordWrap(True);panels.addWidget(self.comparison_label)
        self.received=QPlainTextEdit();self.received.setReadOnly(True);self.received.setMinimumHeight(60);panels.addWidget(self.received,1)
        self.timeline_area=QGroupBox('SO SÁNH THEO THỜI GIAN CẢM BIẾN');timeline_layout=QVBoxLayout(self.timeline_area)
        self.timeline_area.setStyleSheet('QGroupBox {padding:4px;margin-top:10px;}');timeline_layout.setContentsMargins(8,12,8,6);timeline_layout.setSpacing(3)
        legend=QLabel('Xám: NONE · Vàng: MONITOR · Đỏ: HIGH · Tím: thiếu/UNKNOWN/không nhãn · Hồng: bất đồng ≠ sai với nhãn. Xem lại: Dừng phiên và chờ ACK.');legend.setWordWrap(True);timeline_layout.addWidget(legend)
        self.timeline=ComparisonTimeline();self.timeline.setStyleSheet('font-size:11px;');timeline_layout.addWidget(self.timeline)
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
        self.timeline_summary=QPlainTextEdit();self.timeline_summary.setReadOnly(True);self.timeline_summary.setFixedHeight(40)
        timeline_layout.addWidget(self.timeline_summary)
        review=QHBoxLayout();timeline_layout.addLayout(review)
        self.review_time=QSpinBox();self.review_time.setRange(0,100000000);self.review_time.setSingleStep(50);self.review_time.setSuffix(' ms')
        review.addWidget(QLabel('Mốc xem lại sau STOP'));review.addWidget(self.review_time)
        self.review_button=QPushButton('Xem lại đồng bộ (không phát Serial)');self.review_button.clicked.connect(lambda:self.safe(self.review_at,'Xem lại'));review.addWidget(self.review_button)
        outer.addWidget(self.timeline_area)
        log_buttons=QHBoxLayout();outer.addLayout(log_buttons)
        toggle=QPushButton('Hiện / ẩn log');toggle.clicked.connect(lambda:self.logs.setVisible(not self.logs.isVisible()));log_buttons.addWidget(toggle)
        export=QPushButton('Xuất log TX/RX + cấu hình');export.clicked.connect(self.export_log);log_buttons.addWidget(export)
        help_button=QPushButton('Giải thích từng trường');help_button.clicked.connect(lambda:open_field_guide(self));log_buttons.addWidget(help_button)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumHeight(140);self.logs.hide();outer.addWidget(self.logs)
        self.timer=QTimer();self.timer.timeout.connect(self.tick);self.timer.start(16)
        self.panel_timer=QTimer();self.panel_timer.timeout.connect(self.refresh_panels);self.panel_timer.start(150)
        install_wheel_guard(self)
        if (self.repo/self.ml_config['artifacts']/'trusted-model.json').exists():self.compare.setChecked(True)
        self.refresh_panels()

    def change_rule_source(self,index):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            self.rule_source.blockSignals(True);self.rule_source.setCurrentIndex(0 if self.session.rule_source=='pc' else 1);self.rule_source.blockSignals(False);return
        if self.session.link:self.session.disconnect()
        self.session.rule_source='pc' if index==0 else 'esp32'
        self.session.applied_rules={}
        self.session.result=None;self.session.pc_rules=None;self.reset_comparison_view()
        self.capabilities_asked=False
        if index==1:self.scan_ports()
        self.refresh_panels()

    def sync_steer_widgets(self):
        ego=self.session.world.ego
        value=round(ego.steer*100) if ego.control=='manual' else 0
        self.steer_slider.blockSignals(True);self.steer_slider.setValue(value);self.steer_slider.blockSignals(False)

    def set_steer_sensitivity(self,value):
        self.session.steer_sensitivity=value
        self.session.world.ego.steer_sensitivity=value
        self.session.record('META',dict(type='STEER_SENSITIVITY',t_ms=round(self.session.world.t*1000),value=value))

    def set_steer(self,value,from_slider=False):
        """Live EGO steering from slider, hold-buttons or arrow keys."""
        reason=self.session.steer_reason()
        if reason:
            self.sync_steer_widgets();self.steer_hint.setText('Tay lái bị khóa: '+reason)
            return
        try:self.session.steer(value)
        except ValueError as error:
            self.steer_hint.setText(str(error))
        self.sync_steer_widgets();self.refresh_controls()
        self.road.update();self.ai_road.update()

    def release_steering(self):
        try:self.session.release_ego()
        except ValueError as error:
            self.steer_hint.setText('Tay lái bị khóa: '+str(error));return
        self.sync_steer_widgets();self.refresh_controls()

    def set_imu_mode(self,index):
        self.session.imu_mode='manual' if index else 'auto'
        if not index:self.gyro_value.setValue(0)
        self.session.record('META',dict(type='IMU_MODE',imu_mode=self.session.imu_mode,t_ms=round(self.session.world.t*1000)))
        self.refresh_controls()

    def set_manual_gyro(self,value):
        self.gyro_slider.blockSignals(True);self.gyro_slider.setValue(round(value*10));self.gyro_slider.blockSignals(False)
        self.session.manual_gz=value
        self.gyro_direction.setText(f'{value:+.1f} °/s · '+('trái (+)' if value>0 else 'phải (−)' if value<0 else 'không quay'))
        self.session.record('META',dict(type='MANUAL_IMU_CHANGE',imu_mode=self.session.imu_mode,gz_dps=value,
            t_ms=round(self.session.world.t*1000),applies='next scheduled synthetic IMU; no extra sample'))

    def show_source_options(self):
        for index,group in enumerate(self.source_groups):
            group.setVisible(index==self.source_choice.currentIndex())
        self.refresh_controls()

    def refresh_controls(self):
        if not hasattr(self,'review_button') or not hasattr(self,'loaded_scenario'):return
        stopped=self.session.state in ('STOPPED','ERROR') and not self.session.pending
        synthetic=not self.session.dataset and not self.session.replay and not self.session.imu_recording
        edit_reason=self.session.vehicle_edit_reason()
        editable=not edit_reason
        moving=not self.session.vehicle_edit_reason(paused=False)
        loaded=('Dataset '+self.session.dataset.meta['session_id'] if self.session.dataset else
                ('CSV/log radar'+(' + IMU' if self.session.imu_recording else '') if self.session.replay else
                 'CSV IMU + radar tổng hợp' if self.session.imu_recording else 'Kịch bản: '+scenarios.NAMES[self.loaded_scenario]))
        self.source_label.setText('Đang chọn ≠ đã nạp. Xem Nguồn phía trên; nạp lỗi giữ nguồn cũ.')
        self.session_summary.setText(self.rule_title()+' | '+self.session.state+' | Nguồn: '+loaded)
        applied=self.session.applied_rules
        if applied:
            yellow=str(applied['yellow_m'])+' m' if applied.get('yellow_mode')=='distance' else 'phát hiện'
            confirmation='chờ ACK' if self.session.rule_source=='esp32' and self.session.applied_firmware is None else 'đã áp dụng'
            self.session_summary.setText(self.session_summary.text()+f" | Luật {confirmation}: vàng {yellow}, đỏ {applied['near_m']} m | IMU {self.session.imu_mode}")
        self.connection_shortcut.setVisible(self.session.rule_source=='esp32' and self.session.link is None)
        self.rule_source.setEnabled(stopped)
        self.rule_source_note.setText('PC không mở COM / không chờ ACK; không phải bằng chứng phần cứng.' if self.session.rule_source=='pc' else
            'Firmware: '+self.session.capabilities.get('rule_version','chưa xác nhận')+' · vàng riêng: '+('có khai báo hỗ trợ; cần START ACK đúng cấu hình' if self.session.capabilities.get('yellow_distance_v1') else 'chưa xác nhận, dùng vàng kiểu cũ hoặc chuyển PC'))
        self.open_log_button.setEnabled(stopped)
        self.connect_button.setEnabled(self.session.rule_source=='esp32')
        for widget in (self.port,self.baud,self.scan_button):widget.setEnabled(self.session.rule_source=='esp32' and self.session.link is None)
        self.imu_mode.setEnabled(synthetic and not self.session.pending)
        for widget in (self.gyro_slider,self.gyro_value,self.gyro_zero,self.gyro_auto):widget.setEnabled(synthetic and not self.session.pending and self.imu_mode.currentIndex()==1)
        prefix=('PC đang áp dụng: ' if self.session.rule_source=='pc' else 'Bo đã ACK cấu hình: ' if self.session.applied_firmware is not None else 'Đã yêu cầu, chưa ACK: ')
        self.applied_label.setText(prefix+json.dumps(self.session.applied_firmware or applied,ensure_ascii=False) if applied else 'Chưa có phiên: các ô là cấu hình chuẩn bị.')
        for widget in (self.synthetic_button,self.dataset_button,self.radar_button,self.imu_button,
                       self.dataset_collection,
                       self.clear_imu_button,self.compare,self.ai_variant,self.speed,self.synthetic_imu,
                       self.import_radar,self.imu_profile,self.imu_mount,self.mount_roll,self.mount_pitch,
                       self.mount_yaw,self.imu_offset,self.synced_logs):widget.setEnabled(stopped)
        self.start_button.setEnabled(not self.session.pending)
        self.setup_panel.refresh()
        self.steer_sensitivity.setEnabled(not (self.session.dataset or self.session.replay or self.session.imu_recording or self.session.pending))
        self.pause_button.setEnabled(self.session.state in ('RUNNING','PAUSED') and not self.session.pending)
        self.pause_button.setText('Chạy tiếp' if self.session.state=='PAUSED' else 'Tạm dừng')
        self.stop_button.setEnabled(self.session.state in ('RUNNING','PAUSED') and not self.session.pending)
        for widget in (self.x,self.y,self.velocity,self.heading,self.apply_vehicle_button):widget.setEnabled(editable)
        for widget in (self.duration,self.lane_left,self.lane_right):widget.setEnabled(moving)
        self.straight_button.setEnabled(editable)
        steer_reason=self.session.steer_reason()
        for widget in (self.steer_slider,self.steer_left_button,self.steer_right_button,self.steer_center_button):
            widget.setEnabled(not steer_reason)
        self.steer_release_button.setEnabled(not steer_reason and self.session.world.ego.control=='manual')
        ego=self.session.world.ego
        self.steer_hint.setText(('Tay lái bị khóa: '+steer_reason) if steer_reason else
            ('EGO đang do bạn lái; kịch bản không giành lại.' if ego.control=='manual' else 'EGO đang theo kịch bản; bẻ lái bất kỳ lúc nào để tiếp quản.'))
        self.control_status.setText(self.session.driver_text()+(f' | Tình huống: {scenarios.NAMES[self.session.scenario]}' if not (self.session.dataset or self.session.replay or self.session.imu_recording) and self.session.state!='STOPPED' else
            (f' | Tình huống đã nạp: {scenarios.NAMES[self.loaded_scenario]}' if not (self.session.dataset or self.session.replay or self.session.imu_recording) else '')))
        self.edit_hint.setText(edit_reason or 'PAUSED: chỉ trường đã sửa được áp dụng; tốc độ giữ đường chạy, kéo xe thay đường chạy của đúng xe.')
        self.lane_hint.setText(self.session.vehicle_edit_reason(paused=False) or 'Quỹ đạo tay thay lệnh hẹn giờ cho đúng xe; xe chưa chỉnh vẫn theo kịch bản.')
        review=self.session.state=='STOPPED' and not self.session.pending and bool(self.visual_history) and self.ai is not None
        self.review_button.setEnabled(review);self.review_time.setEnabled(review)
        self.difference_select.setEnabled(review)
        for button in (self.difference_prev,self.difference_next,self.difference_jump):
            button.setEnabled(review and self.difference_select.count()>0)
        self.timeline.enabled_review=review
        self.export_imu_button.setEnabled(bool(self.session.live_imu_rows) and not self.session.live_imu_overflow)

    def group(self,layout,title):
        group=QGroupBox(title);form=QFormLayout(group);form.setRowWrapPolicy(QFormLayout.WrapLongRows);layout.addWidget(group);return form

    def button(self,form,title,action):
        button=QPushButton(title);button.clicked.connect(action);form.addRow(button);return button

    def number(self,form,title,lo,hi,value):
        widget=QDoubleSpinBox();widget.setRange(lo,hi);widget.setValue(value);widget.setDecimals(2);form.addRow(title,widget);return widget

    def safe(self,action,operation='Thao tác'):
        try:
            action()
            self.refresh_controls()
            return True
        except Exception as error:
            self.report_error(error,operation)
            self.refresh_controls()
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
        if self.session.rule_source=='pc':return
        self.port.clear()
        for device,description in ports():
            self.port.addItem(f'{device} · {description}',device)

    def connect_port(self):
        def action():
            if self.session.rule_source=='pc':raise ValueError('Chọn ESP32 thật trước khi mở COM')
            if self.session.link:
                self.session.disconnect()
            else:
                if not self.port.currentData():
                    raise ValueError('Không có cổng Serial; vẫn có thể xem mô phỏng offline.')
                self.session.link=SerialLink(self.port.currentData(),int(self.baud.currentText()))
                self.session.state='STOPPED';self.session.result=None;self.capabilities_asked=False
        self.safe(action)
        self.refresh_panels()

    def link_ready(self):
        link=self.session.link
        ready=getattr(link,'ready',None)
        return link is not None and (ready is None or ready.is_set())

    def start(self):
        def action():
            if not (self.session.dataset or self.session.replay or self.session.imu_recording) and self.setup_panel.dirty():
                raise ValueError('Còn thay đổi chưa lưu. Bấm Lưu cấu hình đầu phiên trước khi Bắt đầu / Chạy lại.')
            if self.compare.isChecked():
                selected_config=json.loads((self.repo/self.ai_variant.currentData()).read_text())
                if not (self.repo/selected_config['artifacts']/'trusted-model.json').exists():
                    raise ValueError('MODEL_MISSING: chưa có artifact của Cấu hình AI đang chọn; xem hướng dẫn train đúng phiên bản.')
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
            self.session.rules.update(yaw_threshold_dps=self.yaw.value(),confirm_ms=int(self.confirm.value()),near_m=self.near.value(),
                yellow_mode='distance' if self.yellow_mode.currentIndex() else 'detected',yellow_m=self.yellow.value())
            if self.session.rule_source=='esp32' and not self.session.link:raise ValueError('ESP32 thật chưa kết nối; không tự chuyển sang PC')
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
            self.session.start(self.loaded_scenario)
            self.sync_steer_widgets()
            self.gyro_value.setValue(0);self.session.manual_gz=0.;self.set_manual_gyro(0)
            self.rule_history=[];self.compare_seen=set();self.review_pair=None;self.visual_history=[];self.visual_tick=-1
            self.timeline_signature=None;self.timeline_rows=[];self.timeline.set_rows([])
            self.reference_labels={}
            if self.session.dataset:
                # Evaluation-only labels stay in the UI/reporting path.
                from .ml.comparison import label_map
                self.reference_labels=label_map(self.session.dataset.folder)
            if self.compare.isChecked():
                from .ml.bridge import Bridge
                self.ai=Bridge(self.repo,self.session.envelope.session,self.ai_variant.currentData())
                self.session.sensor_observer=self.observe_sensors
                self.session.result_observer=self.observe_rule
                self.common_hash=__import__('hashlib').sha256();self.common_count=0
            else:
                self.session.sensor_observer=None;self.session.result_observer=None
            self.vehicle.clear();self.vehicle.addItems(['EGO',*[f'ID{c.track_id}' for c in self.session.world.targets]])
            self.accumulator=0
        self.safe(action,operation='Bắt đầu phiên')
        self.refresh_controls()

    def toggle_comparison(self,enabled):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            self.compare.blockSignals(True);self.compare.setChecked(not enabled);self.compare.blockSignals(False)
            return
        # Both views and timeline stay mounted; comparison only enables inference.
        if not enabled and self.ai:self.ai.close();self.ai=None

    def select_dataset_collection(self):
        # Browsing a manifest is not loading it into the active Session.
        self.dataset_root=self.repo/self.dataset_collection.currentData()
        self.dataset_session.clear();path=self.dataset_root/'sessions.json'
        if path.exists():
            for row in json.loads(path.read_text()):
                self.dataset_session.addItem(f"{row['session_id']} · {row['split']} · {row['scenario']}",row['session_id'])

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
        self.imu_label.setText(f'Dataset: {len(replay.imu_points)} mẫu IMU; timestamp chung; phát 0.5× không bỏ mẫu')

    def reset_comparison_view(self):
        self.session.historical_no_geometry=False
        self.session.reviewing_history=False
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

    def rule_title(self):
        source=(self.review_pair[0].get('rule_source','esp32') if self.review_pair and self.review_pair[0] else self.session.rule_source)
        return 'Bộ luật · Máy tính' if source=='pc' else 'Bộ luật · ESP32 thật'

    def visible_rule(self):
        if self.review_pair is not None:return self.review_pair[0]
        return self.session.visible_result()

    def review_at(self):
        if self.session.state!='STOPPED' or self.session.pending:
            raise ValueError('Chỉ xem lại sau STOP và ACK; không chạy lại inference hoặc gửi dữ liệu')
        if not self.visual_history or not self.ai:raise ValueError('Chưa có lượt chạy so sánh để xem lại')
        self.session.reviewing_history=True
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

    def open_comparison_log(self):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            raise ValueError('Dừng phiên/chờ ACK trước khi mở log')
        path,_=QFileDialog.getOpenFileName(self,'Mở log so sánh đã ghi',str(self.session.log_dir),'JSON Lines (*.jsonl)')
        if not path:return
        from .ml.comparison import read_log
        rules,ai,sid=read_log(path)
        if not rules or not ai:raise ValueError('Log cần kết quả bộ luật và AI cùng một phiên')
        sources={r.get('rule_source','esp32') for r in rules}
        if len(sources)!=1:raise ValueError('Không trộn nguồn PC/ESP32 trong một lịch sử')
        from types import SimpleNamespace
        import copy
        self.reset_comparison_view()
        self.session.disconnect()
        if self.session.log:self.session.log.close();self.session.log=None
        self.session.state='STOPPED';self.session.envelope.session=sid
        self.session.rule_source=next(iter(sources))
        self.rule_source.blockSignals(True);self.rule_source.setCurrentIndex(0 if self.session.rule_source=='pc' else 1);self.rule_source.blockSignals(False)
        self.session.pc_rules=None;self.session.applied_rules=rules[-1].get('applied_rules',{})
        self.ai=SimpleNamespace(history=ai,latest_wall=0,error=None,close=lambda:None,visible=lambda *args:None,poll=lambda *args:None)
        self.rule_history=rules;self.reference_labels={}
        world=copy.deepcopy(self.session.world);world.targets=[]
        for stamp in sorted({r['t_ms'] for r in rules}):
            world.t=stamp/1000;self.visual_history.append((stamp,copy.deepcopy(world),{}))
        self.compare.blockSignals(True);self.compare.setChecked(True);self.compare.blockSignals(False)
        self.session.historical_no_geometry=True
        self.review_time.setValue(rules[0]['t_ms']);self.review_at()

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
            lines.append((self.rule_title() if source=='esp32' else 'AI')+' · '+' | '.join(parts))
        scoring=summary['scoring']
        if scoring['labelled_after_warmup_cells']:
            lines.append(f"Nhãn: {scoring['common_valid_cells']}/{scoring['labelled_after_warmup_cells']} mốc×bên chung hợp lệ (sau warm-up; không chấm đuôi thiếu nhãn); HIGH thiếu kết quả: Bộ luật {scoring['high_uncovered_labelled']['esp32']}, AI {scoring['high_uncovered_labelled']['ai']}")
            if scoring['common_valid_cells']:
                parts=[]
                for source,m in scoring['predictors'].items():
                    parts.append(f"{self.rule_title() if source=='esp32' else source}: bỏ sót HIGH {m['high_missed_cells']} mốc; báo HIGH nhầm {m['high_false_cells']} mốc; macro-F1 {m['macro_f1']:.3f}")
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
        path,_=QFileDialog.getOpenFileName(self,'Radar CSV / D,E logger',str(self.repo/'tools/samples'),'Radar (*.csv *.txt *.log);;All (*)')
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
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên trước khi đổi nguồn.');return
        self.session.dataset=None;self.session.replay=None;self.session.clear_imu();self.session.last_sent={};self.session.result=None
        # No residue of a previous replay may reach the synthetic loop.
        self.session.replay_index=0;self.session.replay_finished=False;self.session.replay_imu=False
        self.reset_comparison_view()
        self.imu_label.setText('Chưa nạp log IMU')
        self.loaded_scenario=self.scenario.currentIndex()
        self.setup_panel.load(self.loaded_scenario)
        self.refresh_controls()

    def import_imu(self):
        """Import a BMI160 IMU log (timestamp_us, accel g, gyro dps)."""
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên và chờ ACK trước khi đổi nguồn.');return
        path,_=QFileDialog.getOpenFileName(self,'CSV IMU — 14 cột / physical_only legacy',
                                          str(self.repo/'tools/samples'),
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

    def vehicle_field_changed(self,key):
        if not self.vehicle_form_loading:self.vehicle_dirty.add(key)

    def select_vehicle(self,track_id):
        index=self.vehicle.findText('EGO' if track_id==0 else f'ID{track_id}')
        if index>=0:self.vehicle.setCurrentIndex(index)
        self.read_vehicle()

    def vehicle_dragged(self,track_id):
        self.select_vehicle(track_id)
        car=self.chosen()
        self.vehicle_feedback.setText(f'Đã kéo: {self.vehicle.currentText()} · X={car.x:.2f} m, Y={car.y:.2f} m · giữ tốc độ/hướng')
        self.record_vehicle_edit(car,'drag')

    def record_vehicle_edit(self,car,operation):
        self.session.record('META',dict(type='MANUAL_VEHICLE_EDIT',operation=operation,
            t_ms=round(self.session.world.t*1000),track_id=car.track_id,x=car.x,y=car.y,
            speed_mps=car.speed,heading_rad=car.heading,manual_fields=sorted(car.manual_fields)))
        self.road.update();self.ai_road.update()

    def read_vehicle(self):
        car=self.chosen();self.road.selected=self.ai_road.selected=car.track_id
        self.vehicle_form_loading=True
        try:
            self.x.setValue(car.x);self.y.setValue(car.y);self.velocity.setValue(car.speed*3.6);self.heading.setValue(math.degrees(car.heading))
        finally:self.vehicle_form_loading=False
        self.vehicle_form_id=car.track_id;self.vehicle_dirty.clear()
        self.road.update();self.ai_road.update()

    def edit_vehicle(self):
        reason=self.session.vehicle_edit_reason()
        if reason:self.vehicle_feedback.setText(reason);return
        car=self.chosen()
        if self.vehicle_form_id!=car.track_id:
            self.read_vehicle();self.vehicle_feedback.setText('Đã đọc xe hiện tại; sửa trường cần đổi rồi Áp dụng.');return
        for widget in (self.x,self.y,self.velocity,self.heading):widget.interpretText()
        values=dict(x=self.x.value(),y=self.y.value(),speed=self.velocity.value()/3.6,heading=math.radians(self.heading.value()))
        changes={key:values[key] for key in self.vehicle_dirty}
        car.apply_manual(**changes)
        if not all(math.isclose(getattr(car,k),v,abs_tol=1e-9) for k,v in changes.items()):
            self.vehicle_feedback.setText('Chưa xác nhận được thay đổi; hãy Đọc xe đang chọn.');return
        self.read_vehicle();self.record_vehicle_edit(car,'apply')
        self.vehicle_feedback.setText(f'Đã áp dụng: {self.vehicle.currentText()} · {car.speed*3.6:g} km/h')
        if 'speed' in changes:
            self.profile_feedback.setText(f'{self.vehicle.currentText()}: lịch gia tốc dừng đến hết phiên; cấu hình đầu phiên giữ nguyên.')

    def lane(self,delta):
        if self.session.vehicle_edit_reason(paused=False):return
        self.chosen().change_lane(delta,self.duration.value(),manual=True)
        self.record_vehicle_edit(self.chosen(),'lane')

    def straight(self):
        if self.session.vehicle_edit_reason():return
        self.chosen().go_straight();self.read_vehicle();self.record_vehicle_edit(self.chosen(),'straight')

    def tick(self):
        now=time.monotonic();elapsed=now-self.last_tick;self.last_tick=now
        if self.session.rule_source=='esp32' and self.link_ready() and not self.capabilities_asked:
            self.session.send('GET_CAPABILITIES');self.capabilities_asked=True
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
        title=self.rule_title();self.road.caption=title
        self.timeline.rule_caption='Luật PC' if title.endswith('Máy tính') else 'ESP32 thật'
        self.timeline_area.setTitle('SO SÁNH THEO THỜI GIAN CẢM BIẾN · '+title)
        self.output_title.setText('KẾT QUẢ · '+title);self.timeline.update()
        connected=self.session.link is not None
        ready=self.link_ready()
        self.connect_button.setText('Ngắt kết nối' if connected else 'Kết nối')
        if self.session.rule_source=='pc':
            self.connection_label.setText('Bộ luật PC · không mở COM / không chờ ACK')
        elif connected and not ready:
            self.connection_label.setText('Đang chờ board khởi động xong (dọn nhiễu boot ROM)…')
        elif connected:
            self.connection_label.setText('COM đã mở; '+('SESSION_START đã ACK' if self.session.state in ('RUNNING','PAUSED') and not (self.session.pending and self.session.pending[0]=='SESSION_START') else 'chưa xác nhận SESSION_START của phiên hoạt động'))
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
        self.received.setPlainText(json.dumps(result,indent=2) if result else 'Chưa có kết quả hợp lệ / mới trong 1 giây · '+title)
        if result:
            lines=[f"{title}: xử lý t={result['t_ms']} ms · tuổi nhận trên PC={1000*(time.monotonic()-self.session.result_wall):.0f} ms"]
            for side,title in [('left','Trái'),('right','Phải')]:
                state=result[side];lines.append(f"{title}: {state['level']} · {state.get('reason_code','')}\nradar={state.get('radar_id')} / track={state.get('track_id')}")
            self.reason.setText('\n'.join(lines))
        else:
            stale=self.session.link is not None and self.session.result is not None and time.monotonic()-self.session.result_wall>1
            self.reason.setText('? Mất/thiếu dữ liệu\n'+('Phản hồi quá hạn (>1000 ms)' if stale else 'Chưa có kết quả · '+title))
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
        self.refresh_controls()

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
        self.timer.stop();self.panel_timer.stop()
        if self.ai:self.ai.close()
        self.session.close();event.accept()
