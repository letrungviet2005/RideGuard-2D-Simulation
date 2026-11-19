"""Generate synthetic radar recordings in the same format as the real Hi-Link logger.

The output is accepted by ``rideguard_sim.replay.load`` unchanged.  Every file
is derived from a straight-line kinematic model, so ``v_kmh`` (closing speed),
``y_m`` (forward range) and ``goc`` (bearing) stay mutually consistent instead
of being arbitrary numbers.

The generated recording is *simulated*, not measured: it carries no real radar
characteristics and must not be presented as field data.

Two-radar files
---------------
The firmware reports ``UNKNOWN``/``RADAR_STALE`` as soon as *either* radar
stops producing frames, which drives **both** warning lights to UNKNOWN.  A
recording that contains only one radar therefore cannot show MONITOR or HIGH.
The default output therefore contains both radars, separated by the optional
``radar_id`` column.  A target is emitted only by the radar that geometrically
sees it, exactly like ``rideguard_sim.radar.Radar`` would place it.

Usage:
    python -m rideguard_sim.make_samples
    python -m rideguard_sim.make_samples --preset left_approach --duration 45
"""
import argparse
import csv
import math
from pathlib import Path

# Logger column order of the captured excerpt.  ``x_m``/``fwd_m`` are kept for
# provenance: the raw file stores x=y*sin(angle), fwd=y*cos(angle).
HEADER = ['pc_time', 'ms', 'frame', 'id', 'huong', 'y_m', 'v_kmh', 'goc',
          'cao_toc', 'lien_ket', 'ton_tai', 'x_m', 'fwd_m']
HEADER_DUAL = ['radar_id'] + HEADER

HIGH_SPEED_KMH = 30.0
RADAR_MOUNT_X = 0.65      # forward offset of the radar from the ego origin (m)
RADAR_MOUNT_Y = 0.40      # LEFT radar lateral offset (m)


class Target:
    """One tracked object moving at constant velocity in the ego frame.

    ``closing_kmh`` is the *initial* closing speed.  The target stops closing
    once it reaches ``min_range`` and then holds that range, which keeps a
    long recording physically plausible instead of driving the target through
    the ego and behind it.

    The radar geometry is copied from ``rideguard_sim.radar.Radar``: both
    radars are mounted at ``(0.65, +/-0.40)`` and point **rearwards**
    (``yaw = pi``), so they detect targets *behind* the ego.  ``range_m`` is
    therefore the distance behind the ego, and ``lateral_m`` is positive to
    the left.
    """

    def __init__(self, track_id, side='trai', start_range=40.0, lateral=3.5,
                 closing_kmh=8.0, high_speed=False,
                 associated=True, min_range=12.0):
        self.track_id = track_id
        self.side = side
        self.start_range = start_range
        self.min_range = min_range
        self.lateral = lateral
        self.closing_kmh = closing_kmh
        self.high_speed = high_speed
        self.associated = associated
        self.duration = 0

    def range_at(self, t_s):
        """Distance behind the ego at ``t_s``."""
        travelled = self.closing_kmh / 3.6 * t_s
        return max(self.min_range, self.start_range - travelled)

    def lateral_at(self, t_s):
        """Lateral offset at ``t_s`` (positive = left of the ego)."""
        return self.lateral * (1.0 - 0.12 * min(1.0, t_s / 30.0))

    def body_position(self, t_s):
        """Target position in the ego body frame (X forward, Y left)."""
        # The target trails the ego, so its body X is negative.
        return (-self.range_at(t_s), self.lateral_at(t_s))

    def visible_to(self, radar_id, t_s, range_m=40.0, half_fov_deg=60.0):
        """Would the given radar geometrically see this target?

        Mirrors ``radar.py``: mount ``(mx, my, pi)``, so
        ``forward = -dx`` and ``lateral = -dy`` in the radar frame.
        """
        if radar_id == 'LEFT':
            mx, my = 0.65, RADAR_MOUNT_Y
        else:
            mx, my = 0.65, -RADAR_MOUNT_Y
        bx, by = self.body_position(t_s)
        dx, dy = bx - mx, by - my
        forward = -dx                       # cos(pi)=-1, sin(pi)=0
        lateral = -dy                       # -sin(pi)=0,  cos(pi)=-1
        angle = math.degrees(math.atan2(lateral, forward))
        if forward <= 0:
            return False
        if math.hypot(forward, lateral) > range_m:
            return False
        if abs(angle) > half_fov_deg:
            return False
        # The firmware decides the warning side from the target's body Y, not
        # from which radar reported it.
        body_y = my - forward * math.tan(math.radians(angle))
        return (body_y >= 0) if radar_id == 'LEFT' else (body_y < 0)

    def radar_angle(self, radar_id, t_s):
        """Bearing as the given radar measures it, in degrees."""
        if radar_id == 'LEFT':
            mx, my = 0.65, RADAR_MOUNT_Y
        else:
            mx, my = 0.65, -RADAR_MOUNT_Y
        bx, by = self.body_position(t_s)
        dx, dy = bx - mx, by - my
        forward, lateral = -dx, -dy
        return math.degrees(math.atan2(lateral, forward))

    def sample(self, t_s, radar_id='LEFT'):
        """Return the protocol fields for this target at time ``t_s``."""
        range_m = max(0.05, self.range_at(t_s))
        self.duration = min(255, self.duration + 1)
        return dict(track_id=self.track_id,
                    dir=1 if self.side == 'trai' else 2,
                    y_m=round(range_m, 3),
                    v_kmh=round(abs(self.closing_kmh), 3),
                    angle_deg=round(self.radar_angle(radar_id, t_s), 3),
                    high_speed=1 if self.high_speed else 0,
                    associated=1 if self.associated else 0,
                    duration_frames=self.duration)


def build_targets(preset):
    """Return the target list for a preset.

    Every preset keeps at least one target on each side.  The firmware reports
    ``UNKNOWN``/``RADAR_STALE`` as soon as one radar stops producing frames,
    and the CSV format cannot express an empty frame, so a one-sided recording
    would keep *both* warning lights UNKNOWN for its whole length.
    """
    targets = []
    if preset == 'left_approach':
        # Primary target closing on the left, plus a distant right-hand
        # vehicle so the right radar stays fresh.
        targets.append(Target(31, 'trai', start_range=38.0, lateral=3.2,
                              closing_kmh=9.0))
        targets.append(Target(32, 'phai', start_range=33.0, lateral=-3.6,
                              closing_kmh=2.0))
    elif preset == 'left_approach_multi':
        # Primary left target plus two companions, one on the right and one
        # further behind on the left, to exercise the 4-target-per-frame limit.
        targets.append(Target(31, 'trai', start_range=36.0, lateral=3.4,
                              closing_kmh=11.0, high_speed=True))
        targets.append(Target(32, 'phai', start_range=28.0, lateral=-3.5,
                              closing_kmh=3.0))
        targets.append(Target(33, 'trai', start_range=52.0, lateral=4.0,
                              closing_kmh=6.0))
    elif preset == 'multi_target':
        targets.append(Target(31, 'trai', start_range=34.0, lateral=3.5,
                              closing_kmh=7.0))
        targets.append(Target(32, 'phai', start_range=30.0, lateral=-3.5,
                              closing_kmh=5.0))
        targets.append(Target(33, 'phai', start_range=45.0, lateral=-4.0,
                              closing_kmh=2.0))
        targets.append(Target(34, 'trai', start_range=60.0, lateral=4.2,
                              closing_kmh=4.0))
    elif preset == 'empty_frames':
        # Nothing in view for the first half, then the targets appear.
        targets.append(Target(31, 'trai', start_range=25.0, lateral=3.5,
                              closing_kmh=6.0))
        targets.append(Target(32, 'phai', start_range=30.0, lateral=-3.5,
                              closing_kmh=3.0))
    else:
        raise ValueError(f'Unknown preset: {preset}')
    return targets


def build_rows(preset, duration_s, period_ms, dual=True):
    """Return (header, rows) for the requested preset."""
    targets = build_targets(preset)
    rows = []
    frame_no = 0
    base_ms = 0
    total_ms = int(duration_s * 1000)
    step = max(1, int(period_ms))

    for ms in range(0, total_ms + 1, step):
        frame_no += 1
        t_s = ms / 1000.0
        for radar_id in (('LEFT', 'RIGHT') if dual else ('LEFT',)):
            for target in targets:
                # ``empty_frames`` keeps the target hidden until the midpoint.
                if preset == 'empty_frames' and t_s < duration_s / 2:
                    continue
                if target.range_at(t_s) <= 0.05:
                    continue
                if not target.visible_to(radar_id, t_s):
                    continue
                field = target.sample(t_s, radar_id)
                # Write the rounded values, then derive the provenance columns
                # from exactly those rounded values so the file is internally
                # consistent (x_m = y_m*sin(goc), fwd_m = y_m*cos(goc)).
                y_text = f"{field['y_m']:.2f}"
                angle_text = f"{field['angle_deg']:.0f}"
                y_m = float(y_text)
                angle = float(angle_text)
                x_m = y_m * math.sin(math.radians(angle))
                fwd_m = y_m * math.cos(math.radians(angle))
                row = [
                    '00:00.0',
                    base_ms + ms,
                    frame_no,
                    field['track_id'],
                    target.side,
                    y_text,
                    f"{field['v_kmh']:.2f}",
                    angle_text,
                    field['high_speed'],
                    field['associated'],
                    field['duration_frames'],
                    f'{x_m:.2f}',
                    f'{fwd_m:.2f}',
                ]
                if dual:
                    row = [radar_id] + row
                rows.append(row)
    header = HEADER_DUAL if dual else HEADER
    return header, rows


PRESETS = ('left_approach', 'left_approach_multi', 'multi_target', 'empty_frames')

# Repository root of this tool: <repo>/tools/rideguard-simulator.  Samples are
# written next to the tool, not relative to the caller's working directory,
# so the output never lands in an unrelated ``tools/samples`` folder.
TOOL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = TOOL_ROOT.parent / 'samples' / 'generated'


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', default=str(DEFAULT_OUT_DIR),
                        help='Output directory (default: ../samples/generated).')
    parser.add_argument('--preset', action='append', choices=PRESETS,
                        help='May be repeated; defaults to all presets.')
    parser.add_argument('--duration', type=float, default=45.0,
                        help='Length of each recording in seconds.')
    parser.add_argument('--period-ms', type=int, default=65,
                        help='Radar frame period in milliseconds.')
    parser.add_argument('--single-radar', action='store_true',
                        help='Emit only LEFT rows, matching the captured excerpt.')
    args = parser.parse_args()

    presets = args.preset or list(PRESETS)
    out_dir = Path(args.out_dir).expanduser()
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()
    for preset in presets:
        header, rows = build_rows(preset, args.duration, args.period_ms,
                                  dual=not args.single_radar)
        suffix = 'left' if args.single_radar else 'dual'
        path = out_dir / f'{preset}_{int(args.duration)}s_{suffix}.csv'
        write_csv(path, header, rows)
        print(f'{path}  ({len(rows)} target rows)')


if __name__ == '__main__':
    main()
