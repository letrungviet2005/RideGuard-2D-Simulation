"""World X forward, Y left, Z up. SI units, radians internally."""
from dataclasses import dataclass, field
import math

# Live steering: full command = 15 deg/s yaw (~38 m turn radius at 10 m/s).
# Yaw acceleration limit 90 deg/s^2.
MAX_STEER_YAW_RATE = math.radians(15)
STEER_YAW_ACCEL = math.radians(90)


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
    manual_fields: set = field(default_factory=set)
    # Who drives this vehicle: 'scenario' (scheduled lane/heading events) or
    # 'manual' (live steering).  Data source and driver are separate: a
    # scenario may place the cars while the user steers EGO.
    control: str = 'scenario'
    steer: float = 0.          # live steering command, -1 (right) .. +1 (left)
    steer_sensitivity: float = 1.
    acceleration: float = 0.
    acceleration_schedule: list = field(default_factory=list)
    speed_profile_enabled: bool = True
    commanded_acceleration: float = 0.

    def acceleration_at(self, t):
        if not self.speed_profile_enabled:return 0.
        if not self.acceleration_schedule:return self.acceleration
        return next((a for start,end,a in self.acceleration_schedule if start<=t<end),0.)

    def take_manual(self):
        """Hand this vehicle to live steering without a pose/velocity jump.

        Cancels any pending/active scheduled manoeuvre but keeps position,
        heading, velocity and yaw rate, so the first manual tick continues
        the motion the vehicle already had.
        """
        if self.control == 'manual':
            return
        if self.lane is not None:
            # During a scheduled lane change vx==speed and heading=atan2(vy,vx);
            # re-expressing the same velocity vector as speed*(cos,sin) needs
            # speed=|v|, otherwise vx would jump by ~0.5 % in one tick.
            self.speed = math.hypot(self.vx, self.vy)
        self.lane = None
        self.next_heading = None
        self.control = 'manual'
        self.manual_fields.add('steer')

    def set_steer(self, value: float):
        value = max(-1., min(1., float(value)))
        if not math.isfinite(value):
            raise ValueError('Steering command must be finite')
        self.take_manual()
        self.steer = value

    def release_to_scenario(self):
        """Only on explicit user request: scheduled events may apply again.

        The vehicle keeps its current pose/heading; it is not teleported back
        onto the sample trajectory.  Events whose time already passed do not
        fire again.
        """
        self.control = 'scenario'
        self.steer = 0.
        self.manual_fields.discard('steer')
        # Marker only: scenario 7 would otherwise snap heading back to 0.
        self.manual_fields.add('released')
        self.settle()

    def apply_manual(self, **changes):
        """Paused kinematic edit: change only supplied fields, not elapsed time."""
        changes={k:v for k,v in changes.items() if not math.isclose(getattr(self,k),v,abs_tol=1e-12)}
        if not changes:return
        if {'x','y','heading'} & changes.keys():
            self.lane=None;self.next_heading=None
            if {'x','y'} & changes.keys():self.manual_fields.add('position')
            if 'heading' in changes:self.manual_fields.add('heading')
        if 'speed' in changes:
            self.manual_fields.add('speed')
            self.speed_profile_enabled=False
        for key,value in changes.items():setattr(self,key,value)
        # A paused placement is not a physical impulse measured over dt.
        self.settle()

    def go_straight(self):
        self.manual_fields.add('trajectory');self.lane=None;self.next_heading=None
        self.heading=0;self.settle()

    def change_lane(self, delta: float, duration: float = 4, *, manual=False):
        if manual:self.manual_fields.add('trajectory');self.next_heading=None
        self.lane = (self.y, self.y + delta, max(2, duration), 0.)

    def step(self, dt: float, t: float = 0.):
        # Split exactly at absolute simulation-time profile boundaries and at stop.
        boundaries=sorted({b for start,end,_ in self.acceleration_schedule for b in (start,end) if t+1e-10<b<t+dt-1e-10})
        if boundaries:
            end=t+dt
            for boundary in boundaries+[end]:
                self.step(boundary-t,t);t=boundary
            return
        a=self.acceleration_at(t+dt/2)
        if a<0 and 0<self.speed<-a*dt-1e-10:
            stop=self.speed/-a
            self.step(stop,t);self.step(dt-stop,t+stop)
            return
        previous_speed=self.speed
        final_speed=max(0.,previous_speed+a*dt)
        self.commanded_acceleration=a if previous_speed>0 or a>0 else 0.
        # Existing kinematics integrate using the mean speed; velocity/IMU use
        # the final speed. With a=0 this is exactly the previous path.
        self.speed=(previous_speed+final_speed)/2
        old_vx,old_vy=self.vx,self.vy
        self._step_kinematic(dt)
        self.speed=final_speed
        if a:
            c,s=math.cos(self.heading),math.sin(self.heading)
            self.vx=final_speed*c if self.lane is None else final_speed
            if self.lane is None:self.vy=final_speed*s
            self.ax=(self.vx-old_vx)/dt
            self.ay=(self.vy-old_vy)/dt

    def _step_kinematic(self, dt: float):
        old_vx, old_vy, old_heading = self.vx, self.vy, self.heading
        if self.control == 'manual':
            # Kinematic steering: command -> yaw-rate target, slew-limited so a
            # key press does not create an unphysical IMU impulse.  With the
            # command at 0 the yaw rate decays to 0 and the vehicle KEEPS its
            # current heading (it does not return to the sample trajectory).
            target = self.steer * MAX_STEER_YAW_RATE * self.steer_sensitivity
            limit = STEER_YAW_ACCEL * dt
            w = self.yaw_rate + max(-limit, min(limit, target - self.yaw_rate))
            self.heading = math.atan2(math.sin(self.heading + w * dt), math.cos(self.heading + w * dt))
            self.vx = self.speed * math.cos(self.heading)
            self.vy = self.speed * math.sin(self.heading)
            self.x += self.vx * dt
            self.y += self.vy * dt
            self.ax, self.ay = (self.vx-old_vx)/dt, (self.vy-old_vy)/dt
            self.yaw_rate = w
            return
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
        for vehicle in [self.ego, *self.targets]:
            vehicle.step(dt,self.t)
        self.t += dt


def body_position(ego, x, y):
    dx, dy = x-ego.x, y-ego.y
    c, s = math.cos(ego.heading), math.sin(ego.heading)
    return c*dx+s*dy, -s*dx+c*dy
