"""Bounded subprocess bridge: ML never runs on the Qt event thread."""
from collections import deque
from dataclasses import asdict
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class Bridge:
    def __init__(self,repo,session_id):
        self.session_id=session_id;self.error=None;self.ready=False;self.closed=False
        self.tx=queue.Queue(maxsize=256);self.rx=queue.Queue(maxsize=512)
        self.latest=None;self.latest_wall=0;self.history=deque(maxlen=1000);self.peak_queue=0
        self.checkpoint=None;self.recorded_checkpoint=None
        repo=Path(repo);python=repo/'ai/.venv/Scripts/python.exe'
        if not python.exists():python=repo/'ai/.venv/bin/python'
        self.process=subprocess.Popen([str(python),'-X','utf8',str(repo/'ai/sensor_lab.py'),'worker'],
             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1,
             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.threads=[threading.Thread(target=f,daemon=True) for f in (self._write,self._read,self._errors)]
        for thread in self.threads:thread.start()
        self._put(dict(type='RESET',session_id=session_id))

    def _put(self,message):
        if self.error:raise RuntimeError(self.error)
        try:self.tx.put_nowait(message);self.peak_queue=max(self.peak_queue,self.tx.qsize())
        except queue.Full:
            self.error='AI_BACKLOG: queue limit reached; stop/restart at lower playback speed (no silent drops)'
            raise RuntimeError(self.error)

    def submit(self,events,t_us):
        self._put(dict(type='BATCH',session_id=self.session_id,events=[asdict(e) for e in events],t_us=t_us))

    def _write(self):
        try:
            while not self.closed:
                try:item=self.tx.get(timeout=.1)
                except queue.Empty:continue
                self.process.stdin.write(json.dumps(item,separators=(',',':'))+'\n');self.process.stdin.flush()
        except (OSError,ValueError) as error:
            if not self.closed:self.error=f'AI_WORKER_WRITE: {error}'

    def _read(self):
        try:
            for line in self.process.stdout:
                data=json.loads(line)
                if data['type']=='READY':self.ready=True;continue
                if data.get('session_id')!=self.session_id:continue
                if data['type']=='AI_STREAM':self.checkpoint=data;continue
                try:self.rx.put_nowait((time.monotonic(),data))
                except queue.Full:self.error='AI_RESULT_BACKLOG: consumer must stop';break
        except (OSError,ValueError) as error:
            if not self.closed:self.error=f'AI_WORKER_READ: {error}'
        finally:
            if not self.closed and not self.error:self.error='AI_WORKER_EXIT: model unavailable; no NONE fallback'

    def _errors(self):
        tail=deque(maxlen=8)
        for line in self.process.stderr:tail.append(line.rstrip())
        if tail and not self.closed:self.error='AI_MODEL_ERROR: '+' | '.join(tail)

    def poll(self,record=None):
        if record and self.checkpoint and self.checkpoint!=self.recorded_checkpoint:
            record('AI_STREAM',self.checkpoint);self.recorded_checkpoint=self.checkpoint
        while True:
            try:wall,data=self.rx.get_nowait()
            except queue.Empty:break
            if data.get('session_id')!=self.session_id:continue
            self.latest,self.latest_wall=data,wall;self.history.append(data)
            if record:record('AI',data)

    def visible(self,t_ms,state):
        if self.error or state!='RUNNING' or not self.latest:return None
        if time.monotonic()-self.latest_wall>1 or not 0<=t_ms-self.latest['t_ms']<=150:return None
        return self.latest

    def close(self):
        self.closed=True
        if self.process.poll() is None:self.process.terminate()
        try:self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        for thread in self.threads:thread.join(timeout=.3)
        for pipe in (self.process.stdin,self.process.stdout,self.process.stderr):
            if pipe:pipe.close()


def match_result(rule,history,session_id,tolerance_ms=25):
    if not rule or rule.get('session_id')!=session_id or rule.get('paused'):return None
    candidates=[a for a in history if a['session_id']==session_id and abs(a['t_ms']-rule['t_ms'])<=tolerance_ms]
    if not candidates:return None
    ai=min(candidates,key=lambda a:abs(a['t_ms']-rule['t_ms']))
    if any(rule[s]['level']=='UNKNOWN' or ai[s]['level']=='UNKNOWN' for s in ('left','right')):return None
    return dict(t_ms=rule['t_ms'],ai_t_ms=ai['t_ms'],delta_ms=ai['t_ms']-rule['t_ms'],
                left_agree=rule['left']['level']==ai['left']['level'],right_agree=rule['right']['level']==ai['right']['level'])
