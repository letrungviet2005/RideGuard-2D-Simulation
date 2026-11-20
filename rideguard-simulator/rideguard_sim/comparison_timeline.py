"""Aligned warning lanes; this widget never runs either decision engine."""
from PySide6.QtCore import Qt,Signal,QRectF
from PySide6.QtGui import QPainter,QColor,QPen,QBrush
from PySide6.QtWidgets import QWidget,QToolTip

COLORS={None:'#554969','NONE':'#556775','MONITOR':'#ffd166','HIGH':'#ff556c'}


class ComparisonTimeline(QWidget):
    selected=Signal(int)

    def __init__(self):
        super().__init__();self.rows=[];self.end_ms=50;self.cursor_ms=None
        self.setFixedHeight(140);self.setMouseTracking(True);self.bands=[]
        self.enabled_review=False;self.rule_caption='ESP32 thật'

    def set_rows(self,rows):
        self.rows=rows;self.end_ms=max([50,*[r['timestamp_ms']+50 for r in rows]])
        self.bands=[]
        for side in ('left','right'):
            selected=[r for r in rows if r['side']==side]
            for source in ('esp32','ai','label','disagreement'):
                runs=[]
                for row in selected:
                    value=row[source]
                    if source=='disagreement':value=True if row['disagreement'] else False if row['comparable'] else None
                    if runs and runs[-1][2]==value and runs[-1][1]==row['timestamp_ms']:
                        runs[-1]=(runs[-1][0],row['timestamp_ms']+50,value)
                    else:runs.append((row['timestamp_ms'],row['timestamp_ms']+50,value))
                self.bands.append((side,source,runs))
        self.update()

    def stamp_at(self,x):
        fraction=(x-160)/max(1,self.width()-180)
        return min(self.end_ms-50,max(0,round(fraction*self.end_ms/50)*50))

    def mousePressEvent(self,event):
        if self.enabled_review:self.selected.emit(self.stamp_at(event.position().x()))

    def mouseMoveEvent(self,event):
        stamp=self.stamp_at(event.position().x());rows=[r for r in self.rows if r['timestamp_ms']==stamp]
        detail='\n'.join(f"{r['side']}: {self.rule_caption}={r['esp32'] or '?'} · AI={r['ai'] or '?'} · Nhãn={r['label'] or '?'}" for r in rows)
        QToolTip.showText(event.globalPosition().toPoint(),f'{stamp/1000:.2f} s\n'+detail,self)

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#0c1622'));p.setRenderHint(QPainter.Antialiasing)
        left=160;width=max(1,self.width()-180);top=22;row_h=14
        labels={'esp32':self.rule_caption,'ai':'AI','label':'Nhãn tham chiếu','disagreement':'Khác nhau'}
        for tick in range(0,self.end_ms+1,5000):
            x=left+tick/self.end_ms*width;p.setPen(QColor('#35475b'));p.drawLine(int(x),top,int(x),top+8*row_h)
            p.setPen(QColor('#b9c9d8'));p.drawText(int(x)-10,19,f'{tick/1000:g}s')
        for i,(side,source,runs) in enumerate(self.bands):
            y=top+i*row_h;p.setPen(QColor('#d7e6ef'))
            p.drawText(5,y+11,('Trái ' if side=='left' else 'Phải ')+labels[source])
            for start,end,value in runs:
                color=('#f96f91' if value is True else '#243c4c' if value is False else '#554969') if source=='disagreement' else COLORS[value]
                rect=QRectF(left+start/self.end_ms*width,y,(end-start)/self.end_ms*width,11)
                p.fillRect(rect,QColor(color))
                if value is None:
                    p.fillRect(rect,QBrush(QColor('#b1a6c9'),Qt.Dense6Pattern))
        if self.cursor_ms is not None:
            x=left+self.cursor_ms/self.end_ms*width;p.setPen(QPen(QColor('#ffffff'),2));p.drawLine(int(x),top-5,int(x),top+8*row_h)
        p.end()
