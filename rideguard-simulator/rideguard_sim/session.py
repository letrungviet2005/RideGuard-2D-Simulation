"""Fixed-step sensor scheduler, ACK-controlled sessions, append-only evidence."""
import json
from pathlib import Path
import time
from . import imu, imu_import, replay, scenarios
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
        # Recorded IMU (BMI160 log).  When present it replaces the synthetic
        # IMU stream; ``imu_offset_ms`` aligns its clock with the radar
        # recording, because the two logs come from different clocks.
        self.imu_recording = None
        self.imu_index = 0
        self.imu_offset_ms = 0
        # Set once a replay has consumed every frame; the session then stops
        # itself instead of looping or emitting empty frames.
        self.replay_finished = False
        # IMU sample period in simulation time. Raised only when the serial
        # budget demands it (high playback speed); the board timestamps stay
        # in simulation time either way.
        self.imu_period_ms = 10
        self.next_imu = 0
        self.playback_speed = 1.
        self.live_imu_rows = []
        self.live_imu_overflow = False
        self.dataset = None
        self.sensor_observer = None
        self.result_observer = None
        self.sensor_batch = []

    def record(self, direction, message):
        if self.log:
            self.log.write(json.dumps(dict(wall_ns=time.time_ns(), direction=direction, message=message), ensure_ascii=False)+'\n')
        if message.get('type') in CONTROL | {'ACK', 'ERROR', 'TRANSPORT_NOTICE'}:
            self.events.append(f'{direction} {message}')
            self.events = self.events[-100:]

    def send(self, kind, t_ms=None, **fields):
        message = self.envelope.make(kind, round(self.world.t*1000) if t_ms is None else t_ms, **fields)
        self.mark('generated', message)
        self.record('TX' if self.link else 'OFFLINE', message)
        self.last_sent[kind+fields.get('radar_id', '')] = message
        if kind=='IMU' and self.imu_recording is None and self.dataset is None:
            from .imu_csv import quantize,G
            try:
                raw,physical=quantize([message[k]/G for k in ('ax_mps2','ay_mps2','az_mps2')],
                                      [message[k] for k in ('gx_dps','gy_dps','gz_dps')])
            except ValueError:
                self.live_imu_overflow=True
                raw=physical=None
            if raw is not None and len(self.live_imu_rows)<200000:
                self.live_imu_rows.append([message['t_ms']*1000,*raw,*(f'{v:.9f}' for v in physical),'29.750000'])
            else:
                self.live_imu_overflow=True
        if self.link:
            self.link.send(message)
        if kind in ('IMU','RADAR_FRAME'):
            from .ml.stream import Event
            self.sensor_batch.append(Event.from_message(message))
            # Bounded to one scheduler step, consumed by publish_sensors().
            if len(self.sensor_batch)>4096:
                raise RuntimeError('Common sensor batch exceeded limit')
        return message

    def publish_sensors(self):
        events=tuple(sorted(self.sensor_batch,key=lambda e:(e.timestamp_us,e.kind,e.payload)))
        self.sensor_batch.clear()
        if self.sensor_observer:
            self.sensor_observer(events,round(self.world.t*1000000))

    def mark(self, stage, message=None, **fields):
        trace = getattr(self.link, 'trace', None)
        if trace is not None:
            trace.mark(stage, message, **fields)

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
        self.preflight()
        self.live_imu_rows=[];self.live_imu_overflow=False
        if self.log:
            self.log.close()
        self.envelope = Envelope()
        self.sensor_batch=[]
        if self.dataset:self.dataset.reset()
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
        self.replay_finished = False
        self.imu_index = 0
        self.result = None
        self.last_sent = {}
        self.record('META', {'type':'CONFIG', 'seed':17, 'scenario':scenario, 'radar':vars(self.radar.config),
                             'rules':self.rules, 'mode':'recorded' if self.replay else 'synthetic',
                             'replay_imu':self.replay_imu,
                             'replay_source':self.replay.source if self.replay else None})
        if self.replay:
            self.record('META', {'type':'RECORDED_SOURCE','raw':self.replay.raw})
        if self.imu_recording is not None:
            self.record('META', {'type':'RECORDED_IMU_SOURCE',
                                 'source':self.imu_recording.source,
                                 'mount':self.imu_recording.mount,
                                 'offset_ms':self.imu_offset_ms,
                                 'samples':len(self.imu_recording.samples),
                                 'notes':self.imu_recording.notes,
                                 'raw':self.imu_recording.raw})
        self.command('SESSION_START', 'RUNNING', rules=self.rules,
                     timing_legacy=getattr(self, 'timing_legacy', False))

    def load_replay(self, path, radar_id, rebase=True):
        """Load a radar recording as the replay source.

        ``rebase`` must be False for a synchronised hardware capture: the
        radar file and the IMU file then share one origin, so neither may be
        shifted independently.  For a standalone radar log the default (True)
        makes the recording start at 0 ms.
        """
        recording = replay.load(path, radar_id, rebase=rebase)
        self.replay = recording
        self.replay_index = 0
        self.replay_finished = False
        return recording

    def load_imu(self, path, mount='identity', offset_ms=0, max_gap_ms=None, rebase=True,
                 profile='physical_only', rotation_deg=(0,0,0)):
        """Import a BMI160 IMU log and use it as the replay IMU stream.

        ``mount`` states how the sensor is mounted on the vehicle; it is
        never guessed.  ``offset_ms`` aligns the IMU clock with the radar
        recording, because the two logs come from separate clocks.

        ``rebase`` is True for a standalone log: the timestamps are made
        relative to the first sample.  It must be False for a synchronised
        hardware capture, where the radar file and the IMU file share one
        origin and must not be rebased independently.
        """
        recording = imu_import.load(path, mount=mount, max_gap_ms=max_gap_ms,
                                    rebase=rebase,profile=profile,rotation_deg=rotation_deg)
        self.imu_recording = recording
        if not __import__('math').isfinite(offset_ms):
            raise ValueError('Invalid IMU offset')
        self.imu_offset_ms = offset_ms
        self.imu_index = 0
        self.replay_imu = False
        return recording

    def preflight(self):
        from .protocol import budget_bytes_per_second,BAUD
        if not __import__('math').isfinite(self.playback_speed) or self.playback_speed<=0:
            raise ValueError('Playback speed must be finite and positive')
        period=self.imu_period_ms
        if self.dataset:period=5
        if self.imu_recording:
            times=[imu_import.sample_us(s)+round(self.imu_offset_ms*1000) for s in self.imu_recording.samples]
            for index,t in enumerate(times):
                if t<0 or t//1000>0xffffffff:
                    raise ValueError(f'IMU_TIMESTAMP_RANGE: mẫu #{index+1}, timestamp sau offset = {t} µs, outside protocol range. '
                                     f'Offset hiện tại {self.imu_offset_ms} ms. Chỉnh offset/mốc đồng hồ để thời gian không âm và không quá 4294967295 ms; không sửa CSV gốc.')
            for index,(a,b) in enumerate(zip(times,times[1:]),start=1):
                if b//1000<=a//1000:
                    raise ValueError(f'IMU_TIMESTAMP_MS_COLLISION: mẫu #{index} và #{index+1} share protocol ms. '
                                     f'Thời gian sau offset: {a} µs → {a//1000} ms; {b} µs → {b//1000} ms; chênh {b-a} µs. '
                                     'Firmware yêu cầu mỗi mẫu IMU có ms tăng nghiêm ngặt. '
                                     'CSV µs vẫn được giữ nguyên. Dùng nguồn có khoảng mẫu phù hợp (ví dụ 5000 µs ở 200 Hz) '
                                     'hoặc quy trình resample có lọc riêng. Giảm tốc độ phát không sửa được trùng ms trong dữ liệu nguồn.')
            period=min((b-a)/1000 for a,b in zip(times,times[1:])) if len(times)>1 else 1000
        estimate=budget_bytes_per_second(self.radar.config.period_ms,self.radar.config.max_targets,self.playback_speed,period)
        baud=getattr(self.link,'baud',BAUD)
        if self.link and estimate>baud/10*.8:
            raise ValueError(f'SERIAL_BUDGET: Unsupported Serial budget: dự kiến {estimate:.0f} B/s > giới hạn {baud/10*.8:.0f} B/s '
                             f'ở baud {baud}, tốc độ {self.playback_speed}×, chu kỳ IMU {period:.3f} ms. '
                             'Chọn tốc độ phát 0.5× rồi thử lại. Đây là vượt băng thông, không phải lỗi định dạng CSV; không bỏ mẫu hoặc tự đổi baud.')
        return estimate

    def export_imu(self,path):
        from .imu_csv import write_csv
        if not self.live_imu_rows or self.live_imu_overflow:
            raise ValueError('No complete live synthetic IMU capture to export')
        return write_csv(path,self.live_imu_rows,dict(source='SYNTHETIC_LIVE',clock_id=self.envelope.session,
            clock_evidence='TX radar/IMU in this same session share simulation clock; exported standalone IMU does not prove another CSV synchronized',
            sample_count=len(self.live_imu_rows),timestamp_span_us=self.live_imu_rows[-1][0]-self.live_imu_rows[0][0],
            mount_sensor_to_body_deg=[0,0,0],temperature_C=29.75,quantized_export=True))

    def send_recorded_imu(self,due_us):
        samples=self.imu_recording.samples
        while self.imu_index<len(samples):
            sample=samples[self.imu_index]
            stamp=imu_import.sample_us(sample)+round(self.imu_offset_ms*1000)
            if stamp>due_us:
                break
            self.send('IMU',t_ms=stamp//1000,**imu_import.to_protocol(sample,self.imu_offset_ms))
            self.imu_index+=1

    def clear_imu(self):
        """Go back to the synthetic IMU stream."""
        self.imu_recording = None
        self.imu_index = 0
        self.imu_offset_ms = 0

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
            self.mark('dequeue', message)
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
                    if self.result_observer:self.result_observer(message)
        if self.pending and time.monotonic()-self.pending[3]>3:
            self.events.append('ACK timeout: không đổi trạng thái; kết nối bị đóng')
            self.disconnect()

    def visible_result(self):
        if not self.link or self.state == 'ERROR' or time.monotonic()-self.result_wall>1:
            return None
        return self.result

    def step(self,dt=.01):
        if self.state != 'RUNNING' or self.pending:
            return
        self.world.step(dt)
        t = round(self.world.t*1000)
        if self.dataset:
            for event in self.dataset.due(round(self.world.t*1000000)):
                self.send(event.kind,t_ms=event.timestamp_us//1000,**event.wire_fields())
            self.dataset.update_visual_only(self.world)
            self.publish_sensors()
            if self.dataset.index==len(self.dataset.events):
                self.replay_finished=True
                self.command('SESSION_STOP','STOPPED')
            return
        if self.replay:
            if self.imu_recording is not None:
                # Recorded IMU: send every sample whose shifted timestamp has
                # been reached.  ``imu_offset_ms`` moves the IMU clock onto the
                # radar recording's clock; both are simulation time.
                self.send_recorded_imu(round(self.world.t*1000000))
            elif self.replay_imu:
                self.send('IMU', **imu.sample(self.world.ego,t))
            while self.replay_index<len(self.replay.frames) and self.replay.frames[self.replay_index].t_ms<=t:
                frame=self.replay.frames[self.replay_index]
                self.send('RADAR_FRAME', t_ms=frame.t_ms, radar_id=frame.radar_id, targets=frame.targets)
                self.replay_index+=1
            # End of recording.  Stop the session instead of leaving the board
            # running with no radar frames: a replay does not loop and does not
            # invent empty frames, so without this the firmware would keep
            # reporting UNKNOWN/RADAR_STALE once the freshness window lapses.
            if self.replay_index>=len(self.replay.frames) and (not self.imu_recording or self.imu_index>=len(self.imu_recording.samples)):
                if not self.replay_finished:
                    self.replay_finished=True
                    self.events.append('Đã hết bản ghi; dừng phiên (không lặp, không phát khung rỗng).')
                    self.command('SESSION_STOP', 'STOPPED')
            self.publish_sensors()
            return
        scenarios.update(self.world,self.scenario)
        imu_on,left_on,right_on=scenarios.enabled_sensors(self.world,self.scenario)
        if self.imu_recording:
            self.send_recorded_imu(round(self.world.t*1000000))
        elif imu_on and t>=self.next_imu:
            self.next_imu=t+self.imu_period_ms
            self.send('IMU', **imu.sample(self.world.ego,t))
        if t>=self.next_radar:
            self.next_radar+=self.radar.config.period_ms
            for radar_id, enabled in [('LEFT',left_on),('RIGHT',right_on)]:
                targets=self.radar.sample(self.world,radar_id) if enabled else None
                if targets is not None:
                    self.send('RADAR_FRAME', radar_id=radar_id, targets=targets)
        self.publish_sensors()

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
