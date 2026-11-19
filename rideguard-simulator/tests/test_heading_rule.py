"""The warning must not rise merely because the gyro changed sign.

A lane change turns left first and then steers back to straight.  In the
second half the yaw *rate* points right while the vehicle is still angled
left.  Warning on the right there would flag a vehicle that is merely
straightening up, so the rule also requires the heading offset to agree
with the side being warned about.

These tests drive the Python mirror of the firmware logic.  The real board
remains the authority; ``hardware_scenarios.py`` and ``probe_lane_change.py``
check the same behaviour on hardware.
"""
import math

import pytest

from rideguard_sim.firmware_model import FirmwareModel

RULES = dict(yaw=2.0, near=18.0, confirm=200, hold=350)


def target(track_id, y_m, angle_deg=0.0):
    """A target in the left lane, seen by the LEFT radar."""
    return dict(track_id=track_id, y_m=y_m, angle_deg=angle_deg)


def feed(model, t_ms, yaw_dps):
    model.feed_imu(t_ms, yaw_dps)
    model.feed_radar('LEFT', t_ms, [target(31, 16.0)])
    model.feed_radar('RIGHT', t_ms, [])
    return model.result()


def lane_change_yaw(t_s):
    """Yaw rate of a quintic lane change: positive then negative.

    The heading offset rises and then falls back to zero, while the rate
    changes sign halfway through.  This is the *rate of the heading*, i.e.
    d(atan2(vy, vx))/dt, which is what the gyro reports.
    """
    duration = 4.0
    if t_s < 2.0 or t_s > 2.0 + duration:
        return 0.0

    def heading_at(time_s):
        u = min(max((time_s - 2.0) / duration, 0.0), 1.0)
        vy = 3.5 * (30 * u ** 2 - 60 * u ** 3 + 30 * u ** 4) / duration
        vx = 10.0
        return math.atan2(vy, vx)

    epsilon = 1e-4
    return math.degrees((heading_at(t_s + epsilon) - heading_at(t_s - epsilon)) /
                        (2 * epsilon))


def test_yaw_rate_changes_sign_during_a_lane_change():
    """Sanity: the rate really does flip, so the test is meaningful."""
    early = lane_change_yaw(3.0)
    late = lane_change_yaw(5.0)
    assert early > 0
    assert late < 0


def test_straightening_up_does_not_raise_a_warning():
    """Turning back to straight must not warn on the opposite side."""
    model = FirmwareModel(RULES)
    levels = []
    t = 0
    while t <= 8000:
        levels.append(feed(model, t, lane_change_yaw(t / 1000.0)))
        t += 10
    # No HIGH at all in this scenario: the target is on the left and the
    # vehicle is angled left the whole time, so the right side must stay
    # clear even while the rate points right.
    right_high = [out for out, _, _ in levels if out['right']['level'] == 'HIGH']
    assert right_high == []
    # The left side may warn while actually turning left, which is correct.
    left_high = [out for out, _, _ in levels if out['left']['level'] == 'HIGH']
    assert left_high, 'the real turn towards the near target should warn'


def test_heading_offset_keeps_the_sign_of_the_lane_change():
    """The integrated angle must not flip when the rate does."""
    model = FirmwareModel(RULES)
    headings = []
    t = 0
    while t <= 8000:
        feed(model, t, lane_change_yaw(t / 1000.0))
        headings.append(model.heading_deg)
        t += 10
    peak = max(headings)
    assert peak > 1.0, 'the heading offset should build up during the turn'
    # After the vehicle is straight again the offset returns to zero.
    assert abs(headings[-1]) < 0.5
    # The offset stays positive for the whole second half even though the
    # rate is negative there.
    second_half = headings[len(headings) // 2:]
    assert min(second_half) > 0


def test_real_turn_towards_a_target_still_warns():
    """A genuine turn towards a near target must still raise HIGH."""
    model = FirmwareModel(RULES)
    # Turn left steadily for 1.5 s, with the target on the left.
    levels = []
    for t in range(0, 3000, 10):
        levels.append(feed(model, t, 8.0))
    left_high = [out for out, _, _ in levels if out['left']['level'] == 'HIGH']
    assert left_high, 'turning left towards a near left target must warn'
    assert model.heading_deg > 0


def test_turning_right_towards_a_right_target_warns():
    """The mirrored case must work too."""
    model = FirmwareModel(RULES)
    levels = []
    for t in range(0, 3000, 10):
        model.feed_imu(t, -8.0)
        model.feed_radar('LEFT', t, [])
        model.feed_radar('RIGHT', t, [target(32, 16.0, angle_deg=0.0)])
        levels.append(model.result())
    right_high = [out for out, _, _ in levels if out['right']['level'] == 'HIGH']
    assert right_high
    assert model.heading_deg < 0


def test_far_target_does_not_warn():
    """Distance still gates the warning; the heading rule does not replace it."""
    model = FirmwareModel(RULES)
    levels = []
    for t in range(0, 3000, 10):
        model.feed_imu(t, 8.0)
        model.feed_radar('LEFT', t, [target(31, 40.0)])
        model.feed_radar('RIGHT', t, [])
        levels.append(model.result())
    assert not [out for out, _, _ in levels if out['left']['level'] == 'HIGH']


def test_rate_below_threshold_does_not_warn():
    """The yaw threshold is unchanged."""
    model = FirmwareModel(RULES)
    levels = []
    for t in range(0, 3000, 10):
        levels.append(feed(model, t, 1.0))
    assert not [out for out, _, _ in levels if out['left']['level'] == 'HIGH']


def test_stale_gap_resets_the_heading():
    """A gap in the IMU stream resets the integrated heading."""
    model = FirmwareModel(RULES)
    for t in range(0, 1000, 10):
        model.feed_imu(t, 8.0)
    assert model.heading_deg > 0
    # A gap longer than 200 ms resets the filter and the heading.
    model.feed_imu(1500, 0.0)
    assert model.heading_deg == 0.0
    assert model.filtered_yaw == 0.0
