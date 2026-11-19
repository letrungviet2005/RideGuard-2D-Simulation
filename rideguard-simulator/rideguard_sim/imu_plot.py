"""Read-only IMU preview; display sampling never changes replay samples."""
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter,QColor,QPen
from PySide6.QtCore import QPointF


class ImuPlot(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.points=[];self.setMinimumHeight(200)

    def set_points(self,points):
        self.points=points;self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#0c1622'))
        points=self.points
        for panel,(label,indices) in enumerate((('Gia tốc m/s² — X/Y/Z',(1,2,3)),('Gyro °/s — X/Y/Z',(4,5,6)))):
            top=panel*self.height()/2;h=self.height()/2
            p.setPen(QColor('#e2eaf2'));p.drawText(8,int(top+16),label)
            if not points:
                continue
            t0,t1=points[0][0],points[-1][0]
            values=[r[i] for r in points for i in indices];lo,hi=min(values),max(values)
            if hi-lo<.01:lo-=.5;hi+=.5
            # Screen-only decimation; all input points remain in Session.
            step=max(1,len(points)//max(1,self.width()))
            shown=points[::step]
            for index,color in zip(indices,('#f39772','#7bdfa9','#8eb9ff')):
                p.setPen(QPen(QColor(color),1))
                previous=None
                for row in shown:
                    point=QPointF(8+(self.width()-16)*(row[0]-t0)/max(.000001,t1-t0),top+24+(h-44)*(1-(row[index]-lo)/(hi-lo)))
                    if previous is not None:p.drawLine(previous,point)
                    previous=point
            p.setPen(QColor('#b9c9d9'));p.drawText(8,int(top+h-3),f'{t0:.3f}..{t1:.3f} s; {lo:.2f}..{hi:.2f}')
        p.end()
