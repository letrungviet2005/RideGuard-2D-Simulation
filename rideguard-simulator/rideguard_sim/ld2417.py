"""Decode the Hi-Link LD2417 UART telemetry stream.

The frame layout below is taken verbatim from the official
``HLK-LD2417串口协议说明V1.0.4.pdf`` (Hi-Link, "LD2417 serial communication
protocol", revision V1.0.4), section 1.  All multi-byte integers are
**little endian**.

    +--------+----------------+-----------------------------+--------+
    | AA AA  | N               | target 1 .. target N         | 55 55  |
    +--------+----------------+-----------------------------+--------+

``N`` is the number of targets in the frame (1 byte).  Each target is
exactly 10 bytes:

    +------+------+----------+----------+--------+---------+
    | id   | dir  | y_m      | v_kmh    | state  | angle   |
    | uint8| uint8| uint16   | uint16   | uint16 | int16   |
    +------+------+----------+----------+--------+---------+

* ``id``            target track number, reported by the radar.
* ``dir``           1 = left lane, 2 = right lane.
* ``y_m``           target Y coordinate, value / 100 in metres.
* ``v_kmh``         target speed, value / 100 in km/h.
* ``state``         bit 0     = high-speed flag
                    bit 1     = "associated with a detected target" flag
                    bits 8-15 = track duration in frames
* ``angle``         signed angle in whole degrees.

This is **not** the LD2412/LD2410B framing (``F4 F3 F2 F1`` + length +
``F8 F7 F6 F5``).  The LD2417 telemetry frame has no length field and no
engineering-mode payload, so the two protocols must not share a decoder.

The parser is deliberately strict.  A frame with the wrong magic, a
truncated target, an impossible target count or an out-of-range field is
reported as an error rather than being turned into a partial or invented
target, because the simulator must never fabricate sensor data.
"""
from dataclasses import dataclass, field

HEADER = b'\xAA\xAA'
TAIL = b'\x55\x55'
TARGET_BYTES = 10
MAX_TARGETS = 4

# Protocol bounds, matching ``rideguard_sim.protocol`` / the firmware.
Y_MIN_M = 0.001
Y_MAX_M = 150.0
V_MIN_KMH = 0.0
V_MAX_KMH = 655.35
ANGLE_MIN_DEG = -80.0
ANGLE_MAX_DEG = 80.0

DIR_LEFT = 1
DIR_RIGHT = 2


class Ld2417Error(ValueError):
    """Raised when the byte stream does not match the LD2417 protocol."""


@dataclass
class Target:
    track_id: int
    dir: int
    y_m: float
    v_kmh: float
    high_speed: int
    associated: int
    duration_frames: int
    angle_deg: int


@dataclass
class Frame:
    """One decoded LD2417 telemetry frame."""

    targets: list = field(default_factory=list)

    @property
    def left(self):
        return [t for t in self.targets if t.dir == DIR_LEFT]

    @property
    def right(self):
        return [t for t in self.targets if t.dir == DIR_RIGHT]


def decode_target(payload):
    """Decode the 10 bytes of one target.

    ``payload`` must be exactly 10 bytes.  Raises :class:`Ld2417Error` when
    the field values cannot be represented by the protocol.
    """
    if len(payload) != TARGET_BYTES:
        raise Ld2417Error(f'Target phải đúng {TARGET_BYTES} byte, nhận {len(payload)}')
    track_id = payload[0]
    direction = payload[1]
    if direction not in (DIR_LEFT, DIR_RIGHT):
        raise Ld2417Error(f'Chiều target không hợp lệ: {direction} (phải là 1 hoặc 2)')
    y_raw = int.from_bytes(payload[2:4], 'little')
    v_raw = int.from_bytes(payload[4:6], 'little')
    state = int.from_bytes(payload[6:8], 'little')
    angle = int.from_bytes(payload[8:10], 'little', signed=True)

    y_m = y_raw / 100.0
    v_kmh = v_raw / 100.0
    if not (Y_MIN_M <= y_m <= Y_MAX_M):
        raise Ld2417Error(f'Khoảng cách ngoài miền: {y_m} m')
    if not (V_MIN_KMH <= v_kmh <= V_MAX_KMH):
        raise Ld2417Error(f'Tốc độ ngoài miền: {v_kmh} km/h')
    if not (ANGLE_MIN_DEG <= angle <= ANGLE_MAX_DEG):
        raise Ld2417Error(f'Góc ngoài miền: {angle}°')

    return Target(
        track_id=track_id,
        dir=direction,
        y_m=y_m,
        v_kmh=v_kmh,
        high_speed=state & 0x0001,
        associated=(state >> 1) & 0x0001,
        duration_frames=(state >> 8) & 0xFF,
        angle_deg=angle,
    )


def decode_frame(payload):
    """Decode the frame body between ``AA AA`` and ``55 55``.

    ``payload`` is ``N`` followed by ``N`` targets.  The caller is responsible
    for having removed the header and the tail.
    """
    if not payload:
        raise Ld2417Error('Khung rỗng')
    count = payload[0]
    if count == 0:
        raise Ld2417Error('Khung báo 0 target')
    if count > MAX_TARGETS:
        raise Ld2417Error(f'Khung báo {count} target, vượt giới hạn {MAX_TARGETS}')
    expected = 1 + count * TARGET_BYTES
    if len(payload) != expected:
        raise Ld2417Error(f'Khung không trọn: cần {expected} byte, nhận {len(payload)}')
    targets = []
    for index in range(count):
        start = 1 + index * TARGET_BYTES
        targets.append(decode_target(payload[start:start + TARGET_BYTES]))
    return Frame(targets)


class StreamDecoder:
    """Incremental decoder for a byte stream.

    Feed it whatever arrives from the UART; it yields complete frames and
    keeps any partial frame for the next call.  Bytes that cannot begin a
    frame are discarded so a noisy line does not wedge the decoder.
    """

    def __init__(self):
        self._buffer = bytearray()

    def reset(self):
        self._buffer.clear()

    def feed(self, data):
        """Feed bytes and return the list of frames decoded so far."""
        self._buffer.extend(data)
        frames = []
        while True:
            start = self._buffer.find(HEADER)
            if start < 0:
                # No header yet.  Keep at most one byte, because the tail of
                # the header may still be split across two reads.
                keep = 1 if self._buffer[-1:] == b'\xAA' else 0
                if len(self._buffer) > keep:
                    del self._buffer[:len(self._buffer) - keep]
                break
            if start:
                del self._buffer[:start]
            # The frame body starts after the two header bytes, so at least
            # three bytes are needed before the target count is known.
            if len(self._buffer) < 3:
                break
            count = self._buffer[2]
            if count == 0 or count > MAX_TARGETS:
                # Not a telemetry frame.  Skip the header and resynchronise.
                del self._buffer[:2]
                continue
            total = 2 + 1 + count * TARGET_BYTES + 2
            if len(self._buffer) < total:
                break
            if self._buffer[total - 2:total] != TAIL:
                # Corrupt frame: drop the header and try to resynchronise.
                del self._buffer[:2]
                continue
            payload = bytes(self._buffer[2:total - 2])
            del self._buffer[:total]
            frames.append(decode_frame(payload))
        return frames


def to_protocol(target, radar_id=None):
    """Convert a decoded target into the simulator's internal target dict.

    ``radar_id`` is forced to ``'LEFT'``/``'RIGHT'`` from the LD2417
    direction field when the radar reports a single lane.  The simulator
    needs both radar streams to stay fresh, so a single-radar recording is
    deliberately *not* padded with fake frames here.
    """
    direction = 'LEFT' if target.dir == DIR_LEFT else 'RIGHT'
    return dict(
        track_id=target.track_id,
        dir=target.dir,
        y_m=target.y_m,
        v_kmh=target.v_kmh,
        angle_deg=float(target.angle_deg),
        high_speed=target.high_speed,
        associated=target.associated,
        duration_frames=target.duration_frames,
        radar_id=radar_id or direction,
    )
