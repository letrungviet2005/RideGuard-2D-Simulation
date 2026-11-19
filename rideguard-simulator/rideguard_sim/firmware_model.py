"""Mirror of the firmware decision logic, used to predict what the ESP32 will
report for a recording while no board is attached.

This is a *test aid*.  It intentionally duplicates ``firmware/include/
sim_state.hpp`` and ``sim_rules.hpp`` so a recording can be validated offline.
It is not a substitute for running the real board, which remains the only
authority for the final warning levels.
"""
import math

STALE_MS = 260
HIGH_SPEED_KMH = 30.0


class FirmwareModel:
    """State machine equivalent to the ESP32 firmware."""

    def __init__(self, rules=None):
        rules = rules or dict(yaw=2.0, near=18.0, confirm=200, hold=350)
        self.rules = rules
        self.now = 0
        self.imu = None          # (yaw_dps, t_ms)
        self.radar = {}          # radar_id -> (t_ms, [targets])
        self.filtered_yaw = 0.0
        # Integrated yaw: heading offset from straight ahead, in degrees.
        # The rate changes sign halfway through a lane change; this does not.
        self.heading_deg = 0.0
        self.candidate = 0
        self.candidate_since = 0
        self.motion = 0
        self.high_until = {'left': 0, 'right': 0}

    # -- IMU -------------------------------------------------------------
    def feed_imu(self, t_ms, yaw_dps):
        self.now = max(self.now, t_ms)
        if self.imu is not None:
            dt = (t_ms - self.imu[0]) / 1000.0
            if dt > .2:
                self.filtered_yaw = 0.0
                self.heading_deg = 0.0
                self.candidate = 0
                self.motion = 0
            dt = max(dt, .001)
            self.filtered_yaw += (dt / (.08 + dt)) * (yaw_dps - self.filtered_yaw)
        else:
            dt = .01
            self.filtered_yaw += (dt / (.08 + dt)) * (yaw_dps - self.filtered_yaw)
        self.heading_deg += self.filtered_yaw * dt
        # Stored as (t_ms, yaw_dps) so the freshness check below reads the
        # timestamp from the same slot the radar entries use.
        self.imu = (t_ms, yaw_dps)
        candidate = 1 if self.filtered_yaw > self.rules['yaw'] else \
            -1 if self.filtered_yaw < -self.rules['yaw'] else 0
        if candidate != self.candidate:
            self.candidate = candidate
            self.candidate_since = t_ms
        self.motion = candidate if t_ms - self.candidate_since >= self.rules['confirm'] else 0

    # -- Radar -----------------------------------------------------------
    def feed_radar(self, radar_id, t_ms, targets):
        self.now = max(self.now, t_ms)
        self.radar[radar_id] = (t_ms, targets)

    # -- Result ----------------------------------------------------------
    def _fresh(self, entry):
        if entry is None:
            return False
        t = entry[0]
        return self.now >= t and self.now - t <= STALE_MS

    def result(self):
        unknown = None
        if self.imu is None or not self._fresh(self.imu):
            unknown = 'IMU_STALE'
        for radar_id in ('LEFT', 'RIGHT'):
            if not self._fresh(self.radar.get(radar_id)):
                unknown = 'RADAR_STALE'
                break
        if unknown is None:
            motion = 'LEFT' if self.motion == 1 else 'RIGHT' if self.motion == -1 else 'STRAIGHT'
        else:
            motion = 'UNKNOWN'

        seen = {'left': False, 'right': False}
        high = {'left': False, 'right': False}
        cause = {'left': None, 'right': None}
        for radar_id, entry in self.radar.items():
            if not self._fresh(entry):
                continue
            for target in entry[1]:
                # Mount: LEFT y=+.4, RIGHT y=-.4. bodyY = mountY - y*tan(angle).
                mount_y = .4 if radar_id == 'LEFT' else -.4
                body_y = mount_y - target['y_m'] * math.tan(math.radians(target['angle_deg']))
                side = 'left' if body_y >= 0 else 'right'
                seen[side] = True
                if cause[side] is None:
                    cause[side] = target['track_id']
                # The rate alone is not enough: in the second half of a lane
                # change the rate points back while the vehicle is still
                # angled towards the first side.  The heading offset must
                # agree with the side being warned about.
                turning_towards = self.motion == (1 if side == 'left' else -1)
                angled_towards = self.heading_deg > 0 if side == 'left' else self.heading_deg < 0
                danger = target['y_m'] <= self.rules['near'] and \
                    turning_towards and angled_towards
                if danger:
                    high[side] = True
                    cause[side] = target['track_id']

        out = {}
        for side in ('left', 'right'):
            if unknown:
                out[side] = dict(level='UNKNOWN', reason_code=unknown, track_id=cause[side])
                self.high_until[side] = 0
            else:
                if high[side]:
                    self.high_until[side] = self.now + self.rules['hold']
                held = seen[side] and self.now < self.high_until[side]
                out[side] = dict(
                    level='HIGH' if held else 'MONITOR' if seen[side] else 'NONE',
                    reason_code='TURN_TOWARD_NEAR_TARGET' if held else
                                'TARGET_IN_ZONE' if seen[side] else 'EMPTY_VALID_FRAMES',
                    track_id=cause[side])
        return out, motion, self.filtered_yaw


def replay_recording(recording, yaw_dps=0.0, rules=None):
    """Run the firmware model over parsed replay frames.

    ``yaw_dps`` is a constant yaw fed with every frame, standing in for an IMU
    stream.  Replay mode without IMU has no yaw, so motion stays STRAIGHT.
    """
    model = FirmwareModel(rules)
    results = []
    for frame in recording.frames:
        # Simulate the stale window by advancing the clock to the frame time.
        model.feed_imu(frame.t_ms, yaw_dps)
        model.feed_radar(frame.radar_id, frame.t_ms, frame.targets)
        out, motion, yaw = model.result()
        results.append((frame.t_ms, out, motion, yaw))
    return results
