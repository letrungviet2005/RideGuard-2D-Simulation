"""Fixed-step sensor scheduler, ACK-controlled sessions, append-only evidence."""
import json
from pathlib import Path
import time
from . import imu, scenarios
from .protocol import Envelope, DEFAULT_RULES, CONTROL
from .radar import Radar


class Session:
    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.link = None
        self.state = 'STOPPED'
        self.pending = None
        self.result = None
        self.result_wall = 0
        self.log = None
        self.events = []
        self.rules = DEFAULT_RULES.copy()
        self.radar = Radar()
        self.envelope = Envelope()
        self.world = scenarios.create()
        self.scenario = 0
        self.last_sent = {}
        self.replay = None
        self.replay_index = 0
        self.replay_imu = False
        # IMU sample period in simulation time. Raised only when the serial
        # budget demands it (high playback speed); the board timestamps stay
        # in simulation time either way.
        self.imu_period_ms = 10
        self.next_imu = 0

    def record(self, direction, message):
        if self.log:
            self.log.write(json.dumps(dict(wall_ns=time.time_ns(), direction=direction, message=message), ensure_ascii=False)+'\n')
        if message.get('type') in CONTROL | {'ACK', 'ERROR', 'TRANSPORT_NOTICE'}:
            self.events.append(f'{direction} {message}')
            self.events = self.events[-100:]

    def send(self, kind, t_ms=None, **fields):
        message = self.envelope.make(kind, round(self.world.t*1000) if t_ms is None else t_ms, **fields)
        self.record('TX' if self.link else 'OFFLINE', message)
        self.last_sent[kind+fields.get('radar_id', '')] = message
        if self.link:
            self.link.send(message)
        return message

    def wait_link_ready(self, timeout=6):
        """Block until the Serial transport finished draining boot-ROM noise.

        Returns False when the link is offline or never became ready; the
        caller decides whether to continue offline or report the failure.
        """
        if not self.link:
            return False
        ready = getattr(self.link, 'ready', None)
        if ready is None:
            return True
        return ready.wait(timeout)

    def start(self, scenario=0):
        if self.pending:
            raise ValueError('Đang chờ ACK')
        if self.log:
            self.log.close()
        self.envelope = Envelope()
        self.log_path = self.log_dir / f'{time.strftime("%Y%m%d-%H%M%S")}-{self.envelope.session[:8]}.jsonl'
        self.log = self.log_path.open('w', encoding='utf-8', buffering=1)
        self.world = scenarios.create(scenario)
        if self.replay:
            self.world.targets=[]
            if not self.replay_imu:
                self.world.ego.speed=0
                self.world.ego.settle()
        self.scenario = scenario
        self.radar = Radar(self.radar.config)
        self.next_radar = 0
        self.next_imu = 0
        self.replay_index = 0
        self.result = None
        self.last_sent = {}
        self.record('META', {'type':'CONFIG', 'seed':17, 'scenario':scenario, 'radar':vars(self.radar.config),
                             'rules':self.rules, 'mode':'recorded' if self.replay else 'synthetic',
                             'replay_imu':self.replay_imu,
                             'replay_source':self.replay.source if self.replay else None})
        if self.replay:
            self.record('META', {'type':'RECORDED_SOURCE','raw':self.replay.raw})
        self.command('SESSION_START', 'RUNNING', rules=self.rules)

    def command(self, command, next_state, **fields):
        if self.pending:
            raise ValueError('Đang chờ ACK')
        message = self.send(command, **fields)
        if self.link:
            self.pending = (command, message['seq'], next_state, time.monotonic())
        else:
            self.state = next_state

    def poll(self):
        if not self.link:
            return
        if self.link.error:
            self.events.append(self.link.error)
            self.disconnect()
            return
        while not self.link.rx.empty():
            wall, message = self.link.rx.get_nowait()
            self.record('RX', message)
            if message.get('session_id') != self.envelope.session or message.get('protocol_version') != 1:
                continue
            kind = message.get('type')
            if kind == 'ACK' and self.pending and (message.get('command'),message.get('seq')) == self.pending[:2]:
                self.state = self.pending[2]
                self.pending = None
            elif kind == 'ERROR':
                reason = message.get('reason_code', 'UNKNOWN')
                field = message.get('field')
                detail = f"{reason}" + (f" (field={field})" if field else "")
                self.events.append(f"ESP32 ERROR {detail}; phiên bị dừng, cần phiên mới")
                self.state = 'ERROR'
                self.pending = None
                self.result = None
            elif kind == 'RESULT':
                valid = all(isinstance(message.get(side), dict) and message[side].get('level') in
                            {'UNKNOWN','NONE','MONITOR','HIGH'} for side in ('left','right'))
                if valid and (self.result is None or message.get('seq',0)>self.result.get('seq',0)):
                    self.result, self.result_wall = message, wall
        if self.pending and time.monotonic()-self.pending[3]>3:
            self.events.append('ACK timeout: không đổi trạng thái; kết nối bị đóng')
            self.disconnect()

    def visible_result(self):
        if not self.link or self.state == 'ERROR' or time.monotonic()-self.result_wall>1:
            return None
        return self.result

    def step(self):
        if self.state != 'RUNNING' or self.pending:
            return
        self.world.step()
        t = round(self.world.t*1000)
        if self.replay:
            if self.replay_imu:
                self.send('IMU', **imu.sample(self.world.ego,t))
            while self.replay_index<len(self.replay.frames) and self.replay.frames[self.replay_index].t_ms<=t:
                frame=self.replay.frames[self.replay_index]
                self.send('RADAR_FRAME', t_ms=frame.t_ms, radar_id=frame.radar_id, targets=frame.targets)
                self.replay_index+=1
            return
        scenarios.update(self.world,self.scenario)
        imu_on,left_on,right_on=scenarios.enabled_sensors(self.world,self.scenario)
        if imu_on and t>=self.next_imu:
            self.next_imu=t+self.imu_period_ms
            self.send('IMU', **imu.sample(self.world.ego,t))
        if t>=self.next_radar:
            self.next_radar+=self.radar.config.period_ms
            for radar_id, enabled in [('LEFT',left_on),('RIGHT',right_on)]:
                targets=self.radar.sample(self.world,radar_id) if enabled else None
                if targets is not None:
                    self.send('RADAR_FRAME', radar_id=radar_id, targets=targets)

    def disconnect(self):
        if self.link:
            self.link.close()
        self.link = None
        self.result = None
        self.pending = None
        self.state = 'STOPPED'

    def close(self):
        self.disconnect()
        if self.log:
            self.log.close()
