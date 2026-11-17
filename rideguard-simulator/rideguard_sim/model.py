"""World X forward, Y left, Z up. SI units, radians internally."""
from dataclasses import dataclass, field
import math


@dataclass
class Vehicle:
    track_id: int
    x: float = 0
    y: float = 0
    speed: float = 10
    heading: float = 0
    vx: float = 10
    vy: float = 0
    ax: float = 0
    ay: float = 0
    yaw_rate: float = 0
    lane: tuple | None = None
    next_heading: float | None = None

    def change_lane(self, delta: float, duration: float = 4):
        self.lane = (self.y, self.y + delta, max(2, duration), 0.)

    def step(self, dt: float):
        old_vx, old_vy, old_heading = self.vx, self.vy, self.heading
        if self.lane:
            start, end, duration, elapsed = self.lane
            elapsed = min(duration, elapsed + dt)
            u = elapsed / duration
            self.y = start + (end - start) * (10*u**3 - 15*u**4 + 6*u**5)
            self.vy = (end-start) * (30*u**2 - 60*u**3 + 30*u**4) / duration
            self.vx = self.speed
            self.heading = math.atan2(self.vy, max(.01, self.vx))
            self.lane = None if elapsed == duration else (start, end, duration, elapsed)
        else:
            if self.next_heading is not None:
                self.heading=self.next_heading
                self.next_heading=None
            self.vx = self.speed * math.cos(self.heading)
            self.vy = self.speed * math.sin(self.heading)
            self.y += self.vy * dt
        self.x += self.vx * dt
        self.ax, self.ay = (self.vx-old_vx)/dt, (self.vy-old_vy)/dt
        self.yaw_rate = math.atan2(math.sin(self.heading-old_heading), math.cos(self.heading-old_heading))/dt

    def settle(self):
        self.vx, self.vy = self.speed*math.cos(self.heading), self.speed*math.sin(self.heading)
        self.ax = self.ay = self.yaw_rate = 0


@dataclass
class World:
    ego: Vehicle = field(default_factory=lambda: Vehicle(0))
    targets: list[Vehicle] = field(default_factory=lambda: [Vehicle(31, -24, 3.5, 12, vx=12)])
    t: float = 0

    def step(self, dt=.01):
        self.t += dt
        for vehicle in [self.ego, *self.targets]:
            vehicle.step(dt)


def body_position(ego, x, y):
    dx, dy = x-ego.x, y-ego.y
    c, s = math.cos(ego.heading), math.sin(ego.heading)
    return c*dx+s*dy, -s*dx+c*dy
