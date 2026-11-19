import math
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QPainterPath
from PySide6.QtWidgets import QWidget
from .radar import MOUNTS, radar_to_body
from .replay import coordinates

COLORS = {'UNKNOWN':'#bb9af7','NONE':'#667587','MONITOR':'#ffd166','HIGH':'#ff556c'}


class RoadView(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session=session
        self.scale=13.
        self.selected=0
        self.drag=None
        self.result_provider=None
        self.caption='GÓC NHÌN TỪ TRÊN • CAMERA BÁM XE'
        self.scale_peer=None
        self.setMinimumSize(420,440)

    def point(self,x,y):
        ego=self.session.world.ego
        return QPointF(self.width()/2-(y-ego.y)*self.scale,
                       self.height()*.38-(x-ego.x)*self.scale)

    def world_point(self,pos):
        ego=self.session.world.ego
        return ego.x+(self.height()*.38-pos.y())/self.scale,ego.y+(self.width()/2-pos.x())/self.scale

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#101c2b'))
        ego=self.session.world.ego
        for lane in (-7,-3.5,0,3.5,7):
            pen=QPen(QColor('#384b5b'),1.5,Qt.DashLine)
            p.setPen(pen)
            a=self.point(ego.x+100,lane+1.75)
            b=self.point(ego.x-100,lane+1.75)
            p.drawLine(a,b)
        for x in range(int(ego.x)-80,int(ego.x)+80):
            if x%10==0:
                pos=self.point(x,0)
                p.setPen(QColor('#75899c'))
                p.drawText(QPointF(12,pos.y()),f'{x} m')
        c,s=math.cos(ego.heading),math.sin(ego.heading)
        def mounted(bx,by):
            return self.point(ego.x+c*bx-s*by,ego.y+s*bx+c*by)
        mounts={r:(0,.25 if r=='LEFT' else -.25,math.pi) for r in ('LEFT','RIGHT')} if self.session.dataset else MOUNTS
        for radar_id,(mx,my,yaw) in mounts.items():
            origin=mounted(mx,my)
            path=QPainterPath(origin)
            cfg=self.session.radar.config
            for a in range(-int(cfg.half_fov_deg),int(cfg.half_fov_deg)+1,2):
                angle=yaw+math.radians(a)
                path.lineTo(mounted(mx+cfg.range_m*math.cos(angle),my+cfg.range_m*math.sin(angle)))
            path.closeSubpath()
            tint=QColor('#40c9d6' if radar_id=='LEFT' else '#9e8bff'); tint.setAlpha(22)
            p.fillPath(path,tint)
            p.setPen(QPen(QColor('#395264'),1));p.setBrush(Qt.NoBrush);p.drawPath(path)
            p.setBrush(QColor('#7cdae3'));p.drawEllipse(origin,3,3)
            frame=self.session.last_sent.get('RADAR_FRAME'+radar_id)
            if frame:
                age=round(self.session.world.t*1000)-frame['t_ms']
                for target in frame['targets']:
                    if self.session.dataset:
                        forward,lateral=coordinates(target);bx,by=mx-forward,my-lateral
                    else:bx,by=radar_to_body(radar_id,*coordinates(target))
                    dest=mounted(bx,by)
                    p.setPen(QPen(QColor('#3f7e8b' if age<260 else '#54616b'),1,Qt.DotLine))
                    p.drawLine(origin,dest)
                    if self.session.replay:
                        p.setBrush(QColor('#ffaa67' if age<260 else '#667587'))
                        p.drawEllipse(dest,7,7)
                        p.setPen(QColor('#e8eef6'))
                        p.drawText(dest+QPointF(10,0),f"{radar_id} / ID{target['track_id']}"+(' · stale' if age>=260 else ''))
        if not self.session.replay:
            for vehicle in self.session.world.targets:
                self.draw_vehicle(p,vehicle,False)
        self.draw_vehicle(p,ego,True)
        result=self.result_provider() if self.result_provider else self.session.visible_result()
        for side,dy in [('left',.9),('right',-.9)]:
            level=result[side]['level'] if result else 'UNKNOWN'
            pos=mounted(.6,dy)
            p.setPen(Qt.NoPen);p.setBrush(QColor(COLORS[level]));p.drawEllipse(pos,8,8)
            if level=='UNKNOWN':
                p.setPen(QColor('#101c2b'));p.drawText(pos+QPointF(-4,4),'?')
        p.setPen(QColor('#dce9f5'))
        p.drawText(14,25,self.caption)
        if self.session.dataset:p.drawText(14,47,'Xe: Oracle giả lập (chỉ hiển thị)')
        p.drawText(14,self.height()-38,f't = {self.session.world.t:.2f} s   |   {self.scale:.0f} px/m   |   mô hình phẳng')
        p.drawText(14,self.height()-16,'Xám: không cảnh báo  •  Vàng: mục tiêu  •  Đỏ: cao  •  ?: mất/thiếu dữ liệu')
        p.end()
        if result and result.get('type')=='RESULT' and getattr(self,'last_painted',None)!=(result['session_id'],result['seq']):
            self.last_painted=(result['session_id'],result['seq'])
            self.session.mark('paint',result)

    def draw_vehicle(self,p,car,ego):
        pos=self.point(car.x,car.y)
        p.save();p.translate(pos);p.rotate(-math.degrees(car.heading))
        p.setPen(QPen(QColor('#e8eef6') if car.track_id==self.selected else QColor('#101c2b'),2))
        p.setBrush(QColor('#4cb9fb' if ego else '#ffa66b'))
        width,length=(.75,1.9) if ego else (1.6,3.6)
        p.drawRoundedRect(-width*self.scale/2,-length*self.scale/2,width*self.scale,length*self.scale,5,5)
        p.setBrush(QColor('#153a54'));p.drawEllipse(QPointF(0,-length*self.scale*.18),4,6)
        p.drawLine(QPointF(0,-length*self.scale/2),QPointF(0,-length*self.scale/2-16))
        p.drawPolyline(QPolygonF([QPointF(-4,-length*self.scale/2-10),QPointF(0,-length*self.scale/2-16),QPointF(4,-length*self.scale/2-10)]))
        p.restore();p.setPen(QColor('#ebf0f6'))
        p.drawText(pos+QPointF(16,-6),f'{"EGO" if ego else "ID"+str(car.track_id)}  {car.speed*3.6:.1f} km/h')

    def wheelEvent(self,event):
        self.scale=max(5,min(40,self.scale+(2 if event.angleDelta().y()>0 else -2)))
        if self.scale_peer:self.scale_peer.scale=self.scale;self.scale_peer.update()
        self.update()

    def mousePressEvent(self,event):
        if self.session.state!='PAUSED' or self.session.pending or self.session.replay or self.session.dataset:
            return
        for car in [self.session.world.ego,*self.session.world.targets]:
            if (self.point(car.x,car.y)-event.position()).manhattanLength()<35:
                self.selected=car.track_id;self.drag=car;break

    def mouseMoveEvent(self,event):
        if self.drag:
            self.drag.x,self.drag.y=self.world_point(event.position());self.drag.lane=None;self.update()

    def mouseReleaseEvent(self,event):
        self.drag=None
