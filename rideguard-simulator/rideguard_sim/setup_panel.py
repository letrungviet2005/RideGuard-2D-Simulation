"""Pre-session editor, independent of paused live-vehicle editing."""
import math
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QGroupBox,QComboBox,
    QTableWidget,QTableWidgetItem,QPushButton,QLabel,QHBoxLayout)
from . import scenarios
from .vehicle_setup import defaults,validate_setup,apply_setup


class VehicleCard(QGroupBox):
    def __init__(self,w,car,values):
        super().__init__('EGO' if car.track_id==0 else f'ID{car.track_id}')
        self.track_id=car.track_id;form=QFormLayout(self)
        self.x=w.number(form,'Vị trí dọc X (m)',-10000,10000,values['x'])
        self.y=w.number(form,'Vị trí ngang Y trái (m)',-100,100,values['y'])
        self.speed=w.number(form,'Tốc độ ban đầu (km/h)',0,200,values['speed']*3.6)
        self.heading=w.number(form,'Hướng ban đầu (độ)',-180,180,math.degrees(values['heading']))
        self.mode=QComboBox();self.mode.addItems(['Gia tốc cố định','Theo từng giai đoạn']);form.addRow('Chế độ gia tốc',self.mode)
        self.acceleration=w.number(form,'Gia tốc dọc (m/s²)',-10,10,values['acceleration'])
        self.schedule=QGroupBox('Lịch tăng/giảm tốc');layout=QVBoxLayout(self.schedule)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Từ giây','Đến giây','Gia tốc m/s²'])
        self.table.setMinimumHeight(130);layout.addWidget(self.table)
        row=QHBoxLayout();layout.addLayout(row)
        self.add=QPushButton('Thêm giai đoạn');self.remove=QPushButton('Xóa dòng chọn');row.addWidget(self.add);row.addWidget(self.remove)
        self.add.clicked.connect(lambda:self.add_row());self.remove.clicked.connect(lambda:self.table.removeRow(self.table.currentRow()) if self.table.currentRow()>=0 else None)
        for segment in values['acceleration_schedule']:self.add_row(segment)
        form.addRow(self.schedule)
        self.mode.currentIndexChanged.connect(self.show_mode)
        self.mode.setCurrentIndex(int(bool(values['acceleration_schedule'])));self.show_mode()

    def show_mode(self):
        self.schedule.setVisible(self.mode.currentIndex()==1);self.acceleration.setEnabled(self.mode.currentIndex()==0)

    def add_row(self,segment=None):
        i=self.table.rowCount();self.table.insertRow(i)
        if segment is None:
            try:start=float(self.table.item(i-1,1).text()) if i else 0.
            except (ValueError,AttributeError):start=0.
            segment=(start,start+2,0.)
        for col,value in enumerate(segment):self.table.setItem(i,col,QTableWidgetItem(f'{value:g}'))

    def raw(self):
        return ([v.value() for v in (self.x,self.y,self.speed,self.heading,self.acceleration)],self.mode.currentIndex(),
                [[self.table.item(i,j).text() if self.table.item(i,j) else '' for j in range(3)] for i in range(self.table.rowCount())])

    def values(self):
        for widget in (self.x,self.y,self.speed,self.heading,self.acceleration):widget.interpretText()
        schedule=[]
        if self.mode.currentIndex()==1:
            if not self.table.rowCount():raise ValueError(self.title()+': hãy thêm ít nhất một giai đoạn')
            for i in range(self.table.rowCount()):
                try:schedule.append(tuple(float(self.table.item(i,j).text()) for j in range(3)))
                except (ValueError,AttributeError):raise ValueError(f'{self.title()} · dòng {i+1}: cần nhập ba số hợp lệ')
        return validate_setup(dict(x=self.x.value(),y=self.y.value(),speed=self.speed.value()/3.6,heading=math.radians(self.heading.value()),
            acceleration=self.acceleration.value() if self.mode.currentIndex()==0 else 0.,acceleration_schedule=schedule),self.title())


class SetupPanel(QWidget):
    def __init__(self,w):
        super().__init__();self.w=w;self.layout=QVBoxLayout(self);self.layout.setContentsMargins(0,0,0,0)
        note=QLabel('Thiết lập đầu phiên · lưu khi STOPPED. Tọa độ thế giới: X dọc đường, Y trái, không phải khoảng cách đến EGO. +1 m/s² = tăng 3,6 km/h mỗi giây. Khoảng trống/sau lịch: gia tốc 0; không chạy lùi.')
        note.setWordWrap(True);self.layout.addWidget(note)
        self.cards_layout=QVBoxLayout();self.layout.addLayout(self.cards_layout);self.cards={}
        self.save_button=QPushButton('Lưu cấu hình đầu phiên');self.save_button.clicked.connect(self.save);self.layout.addWidget(self.save_button)
        self.rerun_button=QPushButton('Chạy lại với cấu hình này');self.rerun_button.clicked.connect(w.start);self.layout.addWidget(self.rerun_button)
        self.defaults_button=QPushButton('Khôi phục mặc định kịch bản');self.defaults_button.clicked.connect(self.restore);self.layout.addWidget(self.defaults_button)
        self.summary=QLabel();self.summary.setWordWrap(True);self.layout.addWidget(self.summary)
        self.load(0)

    def load(self,index):
        self.index=index
        while self.cards_layout.count():self.cards_layout.takeAt(0).widget().deleteLater()
        self.cards={};world=scenarios.create(index);saved=self.w.session.initial_setups.get(index,{})
        for car in [world.ego,*world.targets]:
            card=VehicleCard(self.w,car,saved.get(car.track_id,defaults(car)));self.cards[car.track_id]=card;self.cards_layout.addWidget(card)
        self.baseline=self.snapshot();self.summary.setText('Đã nạp cấu hình đã lưu.' if saved else 'Mặc định kịch bản; chưa có chỉnh sửa đầu phiên.')
        # Dynamically created spinboxes must have the same wheel protection.
        guard=getattr(self.w,'wheel_guard',None)
        if guard:
            from PySide6.QtWidgets import QDoubleSpinBox
            for widget in self.findChildren(QDoubleSpinBox)+self.findChildren(QComboBox):widget.installEventFilter(guard)

    def snapshot(self):return {key:card.raw() for key,card in self.cards.items()}
    def dirty(self):return self.snapshot()!=self.baseline

    def reason(self):
        s=self.w.session
        if s.state not in ('STOPPED','ERROR') or s.pending:return 'Dừng phiên và chờ STOPPED trước khi thiết lập đầu phiên.'
        if s.dataset or s.replay or s.imu_recording:return 'CSV/dataset chỉ đọc; Về mô phỏng tổng hợp trước khi thiết lập.'
        return ''

    def refresh(self):
        reason=self.reason()
        for card in self.cards.values():card.setEnabled(not reason)
        self.save_button.setEnabled(not reason);self.defaults_button.setEnabled(not reason);self.rerun_button.setEnabled(not reason)
        self.save_button.setToolTip(reason);self.rerun_button.setToolTip(reason)

    def save(self):
        if self.reason():self.summary.setText(self.reason());return
        try:values={key:card.values() for key,card in self.cards.items()}
        except ValueError as error:self.summary.setText(str(error));return
        self.w.session.initial_setups[self.index]=values;self.baseline=self.snapshot()
        self.w.session.world=scenarios.create(self.index);apply_setup(self.w.session.world,values)
        self.summary.setText('Đã lưu: '+' · '.join(f'{card.title()}: {values[key]["speed"]*3.6:g} km/h · {len(values[key]["acceleration_schedule"])} đoạn gia tốc' for key,card in self.cards.items()))
        self.w.road.update();self.w.ai_road.update()

    def restore(self):
        if self.reason():return
        self.w.session.initial_setups.pop(self.index,None);self.load(self.index)
        self.w.session.world=scenarios.create(self.index);self.w.road.update();self.w.ai_road.update()
