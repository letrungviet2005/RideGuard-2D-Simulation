"""Bounded, in-memory timing trace; never writes to the serial transport."""
from collections import deque
import time


class TimingTrace:
    def __init__(self, capacity=32768):
        self.events = deque(maxlen=capacity)
        self.dropped = 0

    def mark(self, stage, message=None, **fields):
        if len(self.events) == self.events.maxlen:
            self.dropped += 1
        message = message or {}
        self.events.append(dict(pc_ns=time.perf_counter_ns(), stage=stage,
                                session=message.get('session_id'), type=message.get('type'),
                                seq=message.get('seq'), radar=message.get('radar_id'),
                                t_ms=message.get('t_ms'), **fields))
