import json
import math
from pathlib import Path
import shutil
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QFormLayout, QGroupBox,
    QPlainTextEdit, QFileDialog, QMessageBox, QCheckBox, QSplitter, QScrollArea)
from .session import Session
from .serial_link import SerialLink, ports
from .protocol import BAUD, budget_bytes_per_second
from .scene import RoadView
from . import scenarios, replay


class Window(QMainWindow):
    def __init__(self, log_dir):
        super().__init__()
        self.setWindowTitle('RideGuard • Phòng mô phỏng 2D / ESP32')
        self.resize(1460,900)
        self.session=Session(log_dir)
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
        self.road=RoadView(self.session);split.addWidget(self.road)
        right=QWidget();panels=QVBoxLayout(right);right.setMinimumWidth(310);split.addWidget(right)
        split.setSizes([390,700,370])
        connection=self.group(controls,'1 · KẾT NỐI ESP32')
        self.port=QComboBox();self.scan_ports();connection.addRow('Cổng',self.port)
        self.baud=QComboBox();self.baud.addItems(['921600','460800','115200']);connection.addRow('Baud',self.baud)
        self.button(connection,'Quét cổng',self.scan_ports)
        self.connect_button=self.button(connection,'Kết nối',self.connect_port)
        self.connection_label=QLabel('Chỉ mô phỏng · chưa kết nối');self.connection_label.setWordWrap(True);connection.addRow(self.connection_label)
        mode=self.group(controls,'2 · NGUỒN / ĐIỀU KHIỂN')
        self.scenario=QComboBox();self.scenario.addItems(scenarios.NAMES);mode.addRow(self.scenario)
        self.import_radar=QComboBox();self.import_radar.addItems(['LEFT','RIGHT']);mode.addRow('CSV thuộc radar',self.import_radar)
        self.button(mode,'Nhập CSV / log D/E',self.import_recording)
        self.button(mode,'Về mô phỏng tổng hợp',self.synthetic)
        self.synthetic_imu=QCheckBox('Replay + IMU giả lập (chưa đồng bộ)');mode.addRow(self.synthetic_imu)
        self.speed=QComboBox();self.speed.addItems(['0.5×','1×','2×']);self.speed.setCurrentIndex(1);mode.addRow('Tốc độ thời gian',self.speed)
        self.button(mode,'Bắt đầu / Reset phiên',self.start)
        self.pause_button=self.button(mode,'Tạm dừng / Chạy tiếp',self.pause)
        self.button(mode,'Dừng phiên',lambda:self.safe(lambda:self.session.command('SESSION_STOP','STOPPED')))
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
        self.output_title=QLabel('KẾT QUẢ THỰC TỪ ESP32');panels.addWidget(self.output_title)
        self.reason=QLabel('Chưa có phản hồi ESP32\n? Mất/thiếu dữ liệu');self.reason.setWordWrap(True);panels.addWidget(self.reason)
        self.received=QPlainTextEdit();self.received.setReadOnly(True);panels.addWidget(self.received,1)
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

    def safe(self,action):
        try:
            action()
        except Exception as error:
            QMessageBox.warning(self,'RideGuard',str(error))

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
            cfg=self.session.radar.config
            cfg.range_m=self.range.value();cfg.half_fov_deg=self.fov.value()
            cfg.period_ms=int(self.period.value());cfg.max_targets=int(self.max_targets.value())
            cfg.noise_m=self.noise.value();cfg.dropout=self.dropout.value();cfg.unassociated=self.unassociated.value()
            speed=[.5,1,2][self.speed.currentIndex()]
            self.session.imu_period_ms=int(self.imu_period.value())
            if self.session.link and budget_bytes_per_second(cfg.period_ms,cfg.max_targets,speed,
                                                               self.session.imu_period_ms)>int(self.baud.currentText())/10*.8:
                raise ValueError('Vượt ngân sách Serial 80%. Chọn 921600, giảm tốc độ, tăng chu kỳ IMU/radar hoặc giảm số mục tiêu.')
            self.session.rules.update(yaw_threshold_dps=self.yaw.value(),confirm_ms=int(self.confirm.value()),near_m=self.near.value())
            self.session.replay_imu=self.synthetic_imu.isChecked()
            if self.session.link and not self.session.wait_link_ready(8):
                raise ValueError('Serial chưa sẵn sàng. Đóng mọi Serial Monitor, rồi thử lại.')
            self.session.start(self.scenario.currentIndex())
            self.vehicle.clear();self.vehicle.addItems(['EGO',*[f'ID{c.track_id}' for c in self.session.world.targets]])
            self.accumulator=0
        self.safe(action)

    def pause(self):
        self.safe(lambda:self.session.command('RESUME' if self.session.state=='PAUSED' else 'PAUSE',
                                              'RUNNING' if self.session.state=='PAUSED' else 'PAUSED'))

    def import_recording(self):
        if self.session.state not in ('STOPPED','ERROR') or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên và chờ ACK trước khi đổi nguồn.');return
        path,_=QFileDialog.getOpenFileName(self,'Radar CSV / D,E logger',str(Path(__file__).parent.parent/'samples'),'Radar (*.csv *.txt *.log);;All (*)')
        if path:
            def action():
                self.session.replay=replay.load(path,self.import_radar.currentText())
                self.session.result=None;self.session.last_sent={}
                self.session.events.append(f'Đã nhập {len(self.session.replay.frames)} khung; giữ nguyên raw CSV/log')
            self.safe(action)

    def synthetic(self):
        if self.session.state!='STOPPED' or self.session.pending:
            QMessageBox.information(self,'Nguồn dữ liệu','Dừng phiên trước khi đổi nguồn.');return
        self.session.replay=None;self.session.last_sent={};self.session.result=None

    def chosen(self):
        cars=[self.session.world.ego,*self.session.world.targets]
        return cars[min(max(0,self.vehicle.currentIndex()),len(cars)-1)]

    def read_vehicle(self):
        car=self.chosen();self.road.selected=car.track_id
        self.x.setValue(car.x);self.y.setValue(car.y);self.velocity.setValue(car.speed*3.6);self.heading.setValue(math.degrees(car.heading))

    def edit_vehicle(self):
        if self.session.state!='PAUSED' or self.session.pending or self.session.replay:
            QMessageBox.information(self,'Chỉnh xe','Chỉ chỉnh vị trí trong mô phỏng tổng hợp khi đã tạm dừng.');return
        car=self.chosen();car.x=self.x.value();car.y=self.y.value();car.speed=self.velocity.value()/3.6
        car.heading=math.radians(self.heading.value());car.lane=None;car.settle()

    def lane(self,delta):
        if self.session.replay and not self.session.replay_imu:
            return
        self.chosen().change_lane(delta,self.duration.value())

    def straight(self):
        if self.session.state=='PAUSED' and not self.session.pending:
            car=self.chosen();car.lane=None;car.heading=0;car.settle()

    def tick(self):
        now=time.monotonic();elapsed=now-self.last_tick;self.last_tick=now
        self.session.poll()
        if self.session.state=='RUNNING' and not self.session.pending:
            # Cap catch-up and never flood old sensor messages after a UI stall.
            if elapsed>.25:
                self.accumulator=0
                self.safe(lambda:self.session.command('PAUSE','PAUSED'))
            else:
                self.accumulator+=elapsed*[.5,1,2][self.speed.currentIndex()]
                while self.accumulator>=.01:
                    try:
                        self.session.step()
                    except Exception as error:
                        self.session.events.append(str(error));self.session.disconnect();break
                    self.accumulator-=.01
        else:
            self.accumulator=0
        self.road.update()

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
            self.provenance.setText('Radar ghi thực + IMU giả lập, chưa đồng bộ đo thực' if self.session.replay_imu else
                                    'RADAR GHI THỰC • Chưa có dữ liệu chuyển động xe mình • Y-axis / tan(angle) là giả định tọa độ')
        else:
            self.provenance.setText('DỮ LIỆU TỔNG HỢP • IMU thân xe phẳng • Không mô phỏng nghiêng xe / IMU gương')
        self.sent.setPlainText(json.dumps(self.session.last_sent,ensure_ascii=False,indent=2))
        result=self.session.visible_result()
        self.received.setPlainText(json.dumps(result,indent=2) if result else 'Chưa có phản hồi ESP32 hợp lệ / mới trong 1 giây.')
        if result:
            lines=[]
            for side,title in [('left','Trái'),('right','Phải')]:
                state=result[side];lines.append(f"{title}: {state['level']} · {state.get('reason_code','')}\nradar={state.get('radar_id')} / track={state.get('track_id')}")
            self.reason.setText('\n'.join(lines))
        else:
            self.reason.setText('? Mất/thiếu dữ liệu\nChưa có phản hồi ESP32')
        self.logs.setPlainText('\n'.join(self.session.events[-25:]))

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

    def closeEvent(self,event):
        self.session.close();event.accept()
