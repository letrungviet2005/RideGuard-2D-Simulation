"""Geometric functional approximation, not the vendor tracking algorithm."""
from dataclasses import dataclass
import math
import random
from .model import body_position


@dataclass
class RadarConfig:
    range_m: float = 40
    half_fov_deg: float = 60
    period_ms: int = 65
    max_targets: int = 4
    noise_m: float = 0
    dropout: float = 0
    unassociated: float = 0


# Local forward (radar Y) points rearward. Local lateral (radar X)
# points vehicle right for the default mounting, matching negative-left angles.
MOUNTS = {"LEFT": (.65, .4, math.pi), "RIGHT": (.65, -.4, math.pi)}


def radar_to_body(radar_id, forward, lateral):
    x, y, yaw = MOUNTS[radar_id]
    return x+math.cos(yaw)*forward-math.sin(yaw)*lateral, y+math.sin(yaw)*forward+math.cos(yaw)*lateral


class Radar:
    def __init__(self, config=None, seed=17):
        self.config = config or RadarConfig()
        self.random = random.Random(seed)
        self.durations = {}

    def sample(self, world, radar_id):
        cfg = self.config
        # Sensor dropout is no frame, not an empty successful frame.
        if self.random.random() < cfg.dropout:
            return None
        mx, my, yaw = MOUNTS[radar_id]
        cy, sy = math.cos(yaw), math.sin(yaw)
        targets, active = [], set()
        for car in world.targets:
            bx, by = body_position(world.ego, car.x, car.y)
            forward = cy*(bx-mx)+sy*(by-my)
            lateral = -sy*(bx-mx)+cy*(by-my)
            angle = math.degrees(math.atan2(lateral, forward))
            if forward <= 0 or math.hypot(forward, lateral) > cfg.range_m or abs(angle) > cfg.half_fov_deg:
                continue
            # Relative derivative in a rotating body frame, including mount rotation.
            c, s = math.cos(world.ego.heading), math.sin(world.ego.heading)
            dx, dy = car.vx-world.ego.vx, car.vy-world.ego.vy
            rvx = c*dx+s*dy + world.ego.yaw_rate*by
            rvy = -s*dx+c*dy - world.ego.yaw_rate*bx
            closing = -(cy*rvx+sy*rvy) * 3.6
            key = radar_id, car.track_id
            active.add(key)
            self.durations[key] = min(255, self.durations.get(key, 0)+1)
            targets.append(dict(track_id=car.track_id, dir=1 if lateral < 0 else 2,
                                y_m=round(max(.01, forward+self.random.gauss(0, cfg.noise_m)), 3),
                                v_kmh=round(abs(closing), 3), angle_deg=round(angle, 3),
                                high_speed=int(abs(closing)>30),
                                associated=int(self.random.random() >= cfg.unassociated),
                                duration_frames=self.durations[key]))
        for key in list(self.durations):
            if key[0] == radar_id and key not in active:
                del self.durations[key]
        return sorted(targets, key=lambda t: t['y_m'])[:cfg.max_targets]
