"""Presentation-only control pages. Switching pages never changes a Session."""
from PySide6.QtCore import QObject, QEvent, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QWheelEvent
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox,
    QCheckBox, QDoubleSpinBox, QSpinBox, QGroupBox, QScrollArea, QPushButton, QApplication, QSlider)
from . import scenarios


class WheelGuard(QObject):
    """Forward pixel/angle wheels to the nearest scroll viewport, not the editor."""
    def eventFilter(self, widget, event):
        if event.type() == QEvent.Wheel:
            if isinstance(widget, (QSpinBox, QDoubleSpinBox, QSlider)) or (
                isinstance(widget, QComboBox) and not widget.view().isVisible()):
                parent = widget.parentWidget()
                while parent is not None and not isinstance(parent,QScrollArea):
                    parent = parent.parentWidget()
                if parent is not None:
                    viewport = parent.viewport()
                    forwarded = QWheelEvent(viewport.mapFromGlobal(event.globalPosition().toPoint()),
                        event.globalPosition(),event.pixelDelta(),event.angleDelta(),event.buttons(),
                        event.modifiers(),event.phase(),event.inverted())
                    QApplication.sendEvent(viewport,forwarded)
                event.accept()
                return True
        return False


def note(form, text):
    label = QLabel(text); label.setWordWrap(True); form.addRow(label)
    return label


def build_pages(w, stack):
    layouts = []
    for _ in range(4):
        body = QWidget(); layout = QVBoxLayout(body)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(body)
        stack.addWidget(scroll); layouts.append(layout)
    execution=w.group(layouts[0],'Nơi chạy bộ luật')
    w.rule_source=QComboBox();w.rule_source.addItems(['Máy tính – không cần ESP32','ESP32 thật – qua USB/Serial'])
    execution.addRow('Nguồn kết quả bộ luật',w.rule_source)
    w.rule_source.currentIndexChanged.connect(w.change_rule_source)
    w.rule_source_note=note(execution,'PC không mở COM; chỉ đổi nơi chạy sau Dừng phiên.')
    connection = w.group(layouts[0], 'Cổng Serial')
    w.port = QComboBox(); connection.addRow('Cổng', w.port)
    w.baud = QComboBox(); w.baud.addItems(['921600','460800','115200']); connection.addRow('Baud',w.baud)
    w.scan_button = w.button(connection,'Quét cổng',w.scan_ports)
    w.connect_button = w.button(connection,'Kết nối',w.connect_port)
    w.connection_label = note(connection,'Chưa kết nối ESP32')
    note(connection,'Mở COM ≠ ACK. Bắt đầu / Reset phiên gửi SESSION_START; chỉ ACK đúng phiên mới xác nhận ESP32. AI vẫn chạy được khi không có bo.')

    source = QFormLayout(); source.setRowWrapPolicy(QFormLayout.WrapLongRows)
    layouts[1].addLayout(source)
    w.source_choice = QComboBox(); w.source_choice.addItems(['Kịch bản có sẵn','Dataset có sẵn','CSV hoặc log riêng'])
    source.addRow('Bạn muốn dùng dữ liệu nào?', w.source_choice)
    w.source_label = note(source,'')
    w.source_groups = []
    for title in ('Kịch bản có sẵn','Dataset có sẵn','CSV hoặc log riêng'):
        group = QGroupBox(title); form = QFormLayout(group); form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layouts[1].addWidget(group); w.source_groups.append(group)
    synthetic, dataset, csv = [group.layout() for group in w.source_groups]
    w.scenario = QComboBox(); w.scenario.addItems(scenarios.NAMES); synthetic.addRow('Kịch bản',w.scenario)
    w.synthetic_button = w.button(synthetic,'Về mô phỏng tổng hợp (nạp kịch bản)',w.synthetic)
    w.dataset_collection=QComboBox();w.dataset_collection.addItem('Synthetic v1 (giữ nguyên)','data/RideGuard_Synthetic_v1');w.dataset_collection.addItem('Synthetic v2 · tăng/giảm tốc','data/RideGuard_Synthetic_v2');dataset.addRow('Bộ dataset',w.dataset_collection)
    w.dataset_session = QComboBox(); dataset.addRow('Dataset session',w.dataset_session)
    w.dataset_root = w.repo/w.ml_config['dataset']
    import json
    manifest = w.dataset_root/'sessions.json'
    if manifest.exists():
        for row in json.loads(manifest.read_text()):
            w.dataset_session.addItem(f"{row['session_id']} · {row['split']} · {row['scenario']}",row['session_id'])
    w.dataset_collection.currentIndexChanged.connect(w.select_dataset_collection)
    w.dataset_button = w.button(dataset,'Nạp session dataset (radar + IMU)',lambda:w.safe(w.load_dataset,'Nạp dataset'))
    note(dataset,'Nhãn chỉ để chấm điểm; Oracle chỉ để vẽ. Không đưa nhãn vào inference. Nạp thành công chọn tốc độ 0.5×.')
    w.import_radar = QComboBox(); w.import_radar.addItems(['LEFT','RIGHT']); csv.addRow('CSV thuộc radar',w.import_radar)
    w.radar_button = w.button(csv,'Nhập CSV / log D/E',w.import_recording)
    w.imu_button = w.button(csv,'Nhập CSV IMU 14 cột',w.import_imu)
    w.clear_imu_button = w.button(csv,'Bỏ log IMU',w.clear_imu)
    w.synthetic_imu = QCheckBox('Replay + IMU giả lập (chưa đồng bộ)'); csv.addRow(w.synthetic_imu)
    w.imu_label = note(csv,'Chưa nạp log IMU')
    note(csv,'Muốn thay dataset bằng CSV riêng: chọn Kịch bản có sẵn → Nạp kịch bản trước. Nạp radar và IMU riêng có thể tạo nguồn ghép; không chứng minh thu đồng bộ.')
    w.import_options = QGroupBox('Tùy chọn nhập dữ liệu'); options = QFormLayout(w.import_options)
    options.setRowWrapPolicy(QFormLayout.WrapLongRows); csv.addRow(w.import_options)
    w.imu_profile = QComboBox(); w.imu_profile.addItems(['user_table_v1','physical_only']); options.addRow('Profile CSV IMU',w.imu_profile)
    w.imu_mount = QComboBox(); w.imu_mount.addItems(['identity','xyz<-x,z,y','xyz<-x,y,z','xyz<-x,-y,z','identity,yaw=-1'])
    options.addRow('Ánh xạ trục IMU',w.imu_mount)
    w.mount_roll = w.number(options,'Mount roll (độ)',-180,180,0)
    w.mount_pitch = w.number(options,'Mount pitch (độ)',-180,180,0)
    w.mount_yaw = w.number(options,'Mount yaw (độ)',-180,180,0)
    w.imu_offset = QDoubleSpinBox(); w.imu_offset.setDecimals(3); w.imu_offset.setRange(-600000,600000); w.imu_offset.setSuffix(' ms')
    options.addRow('Lệch đồng hồ IMU',w.imu_offset)
    w.synced_logs = QCheckBox('Log đồng bộ (radar + IMU cùng đồng hồ)'); options.addRow(w.synced_logs)
    note(options,'Profile/trục/góc/đồng bộ: chọn trước khi nhập; đổi phải nhập lại. Offset được cộng vào timestamp và chốt khi Bắt đầu. Xem tài liệu để biết dấu và ví dụ.')
    common = w.group(layouts[1],'Phát dữ liệu')
    w.compare = QCheckBox('So sánh Bộ luật / AI'); common.addRow(w.compare); w.compare.toggled.connect(w.toggle_comparison)
    w.ai_variant = QComboBox()
    w.ai_variant.addItem('RF v1 · gốc (plain)', 'ai/configs/sensor-lab.json')
    w.ai_variant.addItem('RF v1 · + lọc (θ=0.7, k=4)', 'ai/configs/sensor-lab-rf-filtered.json')
    w.ai_variant.addItem('RF v2 · tăng/giảm tốc (plain)', 'ai/configs/sensor-lab-v2.json')
    w.ai_variant.addItem('RF v2 · tăng/giảm tốc + lọc (θ=0.7)', 'ai/configs/sensor-lab-v2-filtered.json')
    if (w.repo/'.local/ai/acceleration-v2/trusted-model.json').exists():
        w.ai_variant.setCurrentIndex(3)
    common.addRow('Cấu hình AI', w.ai_variant)
    w.speed = QComboBox(); w.speed.addItems(['0.5×','1×','2×']); w.speed.setCurrentIndex(1); common.addRow('Tốc độ thời gian',w.speed)
    w.export_imu_button = w.button(common,'Xuất CSV IMU từ mô hình phiên này',w.export_imu)
    w.open_log_button=w.button(common,'Mở log so sánh đã ghi',lambda:w.safe(w.open_comparison_log,'Mở log so sánh'))
    note(common,'Đổi nguồn / tốc độ / so sánh: Dừng phiên và chờ ACK. Chọn nguồn chưa có nghĩa đã nạp. Không bo: AI độc lập, ESP32 hiển thị thiếu kết quả.')

    from .setup_panel import SetupPanel
    w.setup_panel=SetupPanel(w);layouts[2].addWidget(w.setup_panel)
    edit = w.group(layouts[2],'Chỉnh xe trong phiên · PAUSED')
    w.vehicle = QComboBox(); w.vehicle.addItems(['EGO','ID31']); edit.addRow('Xe đang chọn',w.vehicle)
    w.read_vehicle_button = w.button(edit,'Đọc xe đang chọn',w.read_vehicle)
    w.x = w.number(edit,'Dọc X (m)',-10000,10000,0)
    w.y = w.number(edit,'Ngang Y trái (m)',-100,100,0)
    w.velocity = w.number(edit,'Tốc độ (km/h)',0,200,36)
    w.heading = w.number(edit,'Hướng (độ)',-180,180,0)
    w.apply_vehicle_button = w.button(edit,'Áp dụng tại thời điểm này',w.edit_vehicle)
    w.edit_hint = note(edit,'')
    w.vehicle_feedback=note(edit,'Chọn xe để đọc trạng thái hiện tại; chỉ trường đã sửa được áp dụng.')
    w.profile_feedback=note(edit,'Đổi tốc độ tại PAUSED dừng lịch gia tốc của riêng xe đó đến hết phiên. Kéo vị trí/bẻ lái giữ lịch gia tốc.')
    w.vehicle.currentIndexChanged.connect(w.read_vehicle)
    for key,widget in (('x',w.x),('y',w.y),('speed',w.velocity),('heading',w.heading)):
        widget.valueChanged.connect(lambda value,key=key:w.vehicle_field_changed(key))
    for view in (w.road,w.ai_road):
        view.vehiclePicked.connect(w.select_vehicle)
        view.vehicleMoved.connect(w.vehicle_dragged)
        view.steerKey.connect(w.set_steer)
    steering = w.group(layouts[2],'Tay lái EGO trực tiếp (dùng được khi đang CHẠY)')
    w.steer_sensitivity=w.number(steering,'Độ nhạy tay lái (×)',.1,3,1)
    w.steer_sensitivity.setSingleStep(.1);w.steer_sensitivity.setDecimals(1)
    w.steer_sensitivity.valueChanged.connect(w.set_steer_sensitivity)
    note(steering,'1×: hết lái = 15°/s; 0,5× nhẹ hơn (7,5°/s); 2× mạnh hơn (30°/s). Có hiệu lực ngay, không đổi ngưỡng cảnh báo hoặc model AI.')
    w.steer_slider = QSlider(Qt.Horizontal); w.steer_slider.setRange(-100,100)
    steering.addRow('Bẻ lái (− phải · 0 giữa · + trái)',w.steer_slider)
    w.steer_left_button = w.button(steering,'Giữ để bẻ TRÁI',lambda:None)
    w.steer_right_button = w.button(steering,'Giữ để bẻ PHẢI',lambda:None)
    w.steer_left_button.pressed.connect(lambda:w.set_steer(w.STEER_BUTTON_LEVEL))
    w.steer_left_button.released.connect(lambda:w.set_steer(0))
    w.steer_right_button.pressed.connect(lambda:w.set_steer(-w.STEER_BUTTON_LEVEL))
    w.steer_right_button.released.connect(lambda:w.set_steer(0))
    w.steer_center_button = w.button(steering,'Về giữa (ngừng bẻ, giữ hướng hiện tại)',lambda:w.set_steer(0))
    w.steer_release_button = w.button(steering,'Trả quyền EGO cho kịch bản',w.release_steering)
    w.steer_slider.valueChanged.connect(lambda value:w.set_steer(value/100,from_slider=True))
    w.steer_hint = note(steering,'')
    note(steering,'Phím ← / → khi đã bấm vào khung đường (nhả phím = về giữa). Về giữa KHÔNG kéo xe về quỹ đạo mẫu. Kịch bản chỉ lấy lại EGO khi bạn bấm "Trả quyền" hoặc Reset phiên. Xe khác (ID31…) không bị ảnh hưởng.')
    trajectory = w.group(layouts[2],'Lệnh quỹ đạo')
    w.duration = w.number(trajectory,'Thời gian chuyển làn (s)',2,12,4)
    w.lane_left = w.button(trajectory,'Chuyển làn trái +3.5 m',lambda:w.lane(3.5))
    w.lane_right = w.button(trajectory,'Chuyển làn phải −3.5 m',lambda:w.lane(-3.5))
    w.straight_button = w.button(trajectory,'Đi thẳng theo hướng đường',w.straight)
    w.lane_hint = note(trajectory,'')
    manual=w.group(layouts[2],'Thử tín hiệu IMU')
    w.imu_mode=QComboBox();w.imu_mode.addItems(['IMU theo chuyển động EGO','Thử gyro Z bằng tay']);manual.addRow('Nguồn gyro Z',w.imu_mode)
    w.gyro_slider=QSlider(Qt.Horizontal);w.gyro_slider.setRange(-1000,1000);manual.addRow('Gyro Z (thanh kéo)',w.gyro_slider)
    w.gyro_value=w.number(manual,'Gyro Z (°/s)',-100,100,0);w.gyro_value.setDecimals(1);w.gyro_value.setSingleStep(.1)
    w.gyro_slider.valueChanged.connect(lambda value:w.gyro_value.setValue(value/10))
    w.gyro_value.valueChanged.connect(w.set_manual_gyro)
    w.imu_mode.currentIndexChanged.connect(w.set_imu_mode)
    w.gyro_zero=w.button(manual,'Về 0',lambda:w.gyro_value.setValue(0))
    w.gyro_auto=w.button(manual,'Trở lại tự động',lambda:w.imu_mode.setCurrentIndex(0))
    w.gyro_direction=note(manual,'0 °/s · không quay')
    note(manual,'Chỉ thay số đo gyro Z; không làm xe trên hình chuyển làn. Muốn đổi quỹ đạo, dùng nút Chuyển làn. Chỉ nguồn hoàn toàn tổng hợp; PAUSED chỉ chuẩn bị mẫu kế tiếp. Reset đưa giá trị tay về 0.')
    radar = w.group(layouts[3],'Radar mô phỏng')
    note(radar,'Chốt ở Bắt đầu / Reset phiên. Chỉ sinh radar tổng hợp; không sửa CSV/dataset. Đầu vào đã sinh được gửi chung cho ESP32 và AI nếu bật.')
    w.range = w.number(radar,'Tầm mô phỏng (m)',5,100,40)
    w.fov = w.number(radar,'Nửa góc quét (độ)',5,75,60)
    w.period = w.number(radar,'Chu kỳ radar (ms)',40,500,65)
    w.max_targets = w.number(radar,'Giới hạn mục tiêu',1,4,4)
    w.noise = w.number(radar,'Nhiễu Y sigma (m)',0,1,0)
    w.dropout = w.number(radar,'Xác suất mất khung',0,1,0)
    w.unassociated = w.number(radar,'P(associated=0)',0,1,0)
    imu = w.group(layouts[3],'IMU mô phỏng')
    w.imu_period = w.number(imu,'Chu kỳ IMU (ms)',10,100,10)
    note(imu,'Chốt ở Bắt đầu / Reset phiên; chỉ IMU tổng hợp. CSV/dataset giữ nhịp mẫu gốc. Radar CSV + IMU giả lập dùng nhịp vòng replay, không dùng ô chu kỳ này.')
    rules = w.group(layouts[3],'Bộ luật')
    w.yellow_mode=QComboBox();w.yellow_mode.addItems(['Vàng khi phát hiện mục tiêu','Vàng theo khoảng cách']);rules.addRow('Chế độ đèn vàng',w.yellow_mode)
    w.yellow=w.number(rules,'Khoảng cách báo vàng (m)',.01,150,15)
    w.yellow_mode.currentIndexChanged.connect(lambda:w.yellow.setEnabled(w.yellow_mode.currentIndex()==1))
    w.yellow.setEnabled(False)
    w.near = w.number(rules,'Vùng gần báo đỏ (m)',1,100,18)
    w.yaw = w.number(rules,'Ngưỡng yaw (độ/s)',.2,30,2)
    w.confirm = w.number(rules,'Xác nhận chuyển hướng (ms)',50,2000,200)
    for integer in (w.period,w.imu_period,w.max_targets,w.confirm):integer.setDecimals(0)
    note(rules,'Chốt khi Bắt đầu/Reset; 0 < đỏ <= vàng ở chế độ khoảng cách. Chỉ đổi bộ luật đang chọn, không lọc dữ liệu AI. Thử nghiệm, chưa xác minh an toàn ngoài đường.')
    w.applied_label=note(rules,'Chưa có phiên: các ô là cấu hình chuẩn bị.')
    for layout in layouts: layout.addStretch()
    w.source_choice.currentIndexChanged.connect(w.show_source_options)
    w.loaded_scenario = 0
    w.show_source_options()


def install_wheel_guard(w):
    w.wheel_guard = WheelGuard(w)
    for widget in w.findChildren(QWidget):
        if isinstance(widget,(QSpinBox,QDoubleSpinBox,QComboBox,QSlider)):
            widget.installEventFilter(w.wheel_guard)
            if isinstance(widget,QComboBox):
                widget.setMinimumContentsLength(8)
                widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)


def open_field_guide(w):
    path = w.repo/'docs/simulator/GIAI-THICH-TUNG-TRUONG.md'
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        w.report_error(ValueError(f'Không mở được tài liệu: {path}'),'Mở hướng dẫn')
