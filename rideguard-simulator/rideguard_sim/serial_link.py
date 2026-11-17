"""Bounded serial worker. UI never blocks on a read or control ACK."""
import json
import queue
import threading
import time
import serial
from serial.tools import list_ports
from .protocol import encode, MAX_LINE


def ports():
    return [(p.device, p.description) for p in list_ports.comports()]


class SerialLink:
    def __init__(self, port, baud, settle=2.5):
        self.tx = queue.Queue(maxsize=64)
        self.rx = queue.Queue(maxsize=256)
        self.stop = threading.Event()
        self.error = None
        self.bytes_sent = 0
        self.port, self.baud = port, baud
        # The ESP32 boot ROM handshake floods the UART with non-protocol bytes.
        # Sending before that noise drains corrupts the first frame, and the
        # firmware latches INVALID_INPUT on any parse failure.
        self.settle = settle
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def send(self, message):
        if not self.ready.is_set():
            raise RuntimeError('Serial chưa sẵn sàng; đợi kết nối hoàn tất')
        try:
            self.tx.put_nowait(encode(message))
        except queue.Full:
            self.error = 'TX backlog: session stopped; reduce simulation speed/rate'
            self.stop.set()
            raise RuntimeError(self.error)

    def _run(self):
        try:
            with serial.Serial(port=None, baudrate=self.baud, timeout=.005, write_timeout=.2) as connection:
                connection.dtr = connection.rts = False
                connection.port = self.port
                connection.open()
                # Drain boot-ROM/bootloader noise before the first protocol line.
                deadline = time.monotonic() + self.settle
                while time.monotonic() < deadline and not self.stop.is_set():
                    pending = connection.in_waiting
                    if pending:
                        connection.read(min(pending, 8192))
                    time.sleep(.01)
                self.ready.set()
                buffer = bytearray()
                while not self.stop.is_set():
                    for _ in range(12):
                        try:
                            data = self.tx.get_nowait()
                        except queue.Empty:
                            break
                        connection.write(data)
                        self.bytes_sent += len(data)
                    buffer.extend(connection.read(max(1, min(connection.in_waiting, 8192))))
                    while b'\n' in buffer:
                        raw, _, buffer = buffer.partition(b'\n')
                        if len(raw)>MAX_LINE:
                            raise ValueError('RX line too long')
                        try:
                            decoded = json.loads(raw)
                            if not isinstance(decoded, dict):
                                raise ValueError('RX is not an object')
                        except (ValueError, UnicodeDecodeError):
                            decoded = {'type': 'TRANSPORT_NOTICE', 'text': raw[:120].decode(errors='replace')}
                        self.rx.put_nowait((time.monotonic(), decoded))
                    if len(buffer)>MAX_LINE:
                        raise ValueError('Unterminated RX line exceeds limit')
        except Exception as error:
            self.error = str(error)
        finally:
            self.stop.set()
            # Unblock anyone waiting for the link to become ready.
            self.ready.set()

    def close(self):
        self.stop.set()
        self.ready.set()
        self.thread.join(timeout=1)
