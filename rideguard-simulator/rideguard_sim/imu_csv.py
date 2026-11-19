"""14-column synthetic/reference IMU support; no inferred sensor model or labels."""
import csv
import hashlib
import json
import math
from pathlib import Path
import random

HEADER='timestamp_us,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C'.split(',')
PROFILE='user_table_v1'
ACCEL_G_PER_LSB=.000122
GYRO_DPS_PER_LSB=.0175
G=9.80665
SCENARIOS=('stationary','straight','lane_left','lane_right','steady_left','bumpy','tilted')


def rotation(roll=0,pitch=0,yaw=0):
    """Rz(yaw) Ry(pitch) Rx(roll), radians in; vector local -> parent."""
    cr,sr,cp,sp,cy,sy=math.cos(roll),math.sin(roll),math.cos(pitch),math.sin(pitch),math.cos(yaw),math.sin(yaw)
    return ((cy*cp,cy*sp*sr-sy*cr,cy*sp*cr+sy*sr),
            (sy*cp,sy*sp*sr+cy*cr,sy*sp*cr-cy*sr),(-sp,cp*sr,cp*cr))


def transform(matrix,vector,transpose=False):
    return tuple(sum((matrix[j][i] if transpose else matrix[i][j])*vector[j] for j in range(3)) for i in range(3))


def quantize(accel_g,gyro_dps):
    values=(*accel_g,*gyro_dps)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Non-finite synthetic IMU')
    raw=[round(v/(ACCEL_G_PER_LSB if i<3 else GYRO_DPS_PER_LSB)) for i,v in enumerate(values)]
    if any(v< -32768 or v>32767 for v in raw):
        raise ValueError('RAW int16 saturation; export refused (no wrap/clipping)')
    physical=[v*(ACCEL_G_PER_LSB if i<3 else GYRO_DPS_PER_LSB) for i,v in enumerate(raw)]
    return raw,physical


def model(t,scenario):
    """Analytic flat trajectory: constant forward v=10; quintic lane y(t).

    Lane event 5..9 s returns yaw to zero, with signed counter-steer.
    A short vertical road displacement supplies bumpy specific force.
    """
    speed=0 if scenario in ('stationary','tilted') else 10
    heading=rate=ay=az=0.
    roll=math.radians(25) if scenario=='tilted' else 0.
    pitch=math.radians(-20) if scenario=='tilted' else 0.
    if scenario in ('lane_left','lane_right'):
        u=(t-5)/4
        if 0<u<1:
            delta=3.5 if scenario=='lane_left' else -3.5
            vy=delta*(30*u*u-60*u**3+30*u**4)/4
            ay=delta*(60*u-180*u*u+120*u**3)/16
            heading=math.atan2(vy,speed)
            rate=speed*ay/(speed*speed+vy*vy)
    elif scenario=='steady_left':
        rate=math.radians(8);heading=rate*t
        # Constant speed circular motion: a_world = v*w*(-sin,cos).
        ax=-speed*rate*math.sin(heading);ay=speed*rate*math.cos(heading)
        return transform(rotation(yaw=heading),(ax,ay,G),True),(0.,0.,8.)
    elif scenario=='bumpy':
        u=(t-5)/.6
        if 0<u<1:
            # z = A*sin^4(pi*u), C2 at both endpoints, no yaw label injection.
            q=math.pi*u
            az=.015*(math.pi/.6)**2*(12*math.sin(q)**2*math.cos(q)**2-4*math.sin(q)**4)
    force=transform(rotation(roll,pitch,heading),(0.,ay,az+G),True)
    return force,(0.,0.,math.degrees(rate))


def write_csv(path,rows,metadata):
    path=Path(path);meta_path=path.with_suffix(path.suffix+'.json')
    if path.exists() or meta_path.exists():
        raise ValueError('Output exists; choose a new name (old samples are preserved)')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle,lineterminator='\n');writer.writerow(HEADER);writer.writerows(rows)
    metadata=dict(metadata,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),columns=HEADER,
                  profile=PROFILE,accel_g_per_lsb=ACCEL_G_PER_LSB,gyro_dps_per_lsb=GYRO_DPS_PER_LSB,
                  profile_hardware_verified=False,sensor_model='UNKNOWN',saturation='reject',temperature_kind='SIMULATED_SENSOR_DIE')
    meta_path.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    return path


def generate(path,scenario='lane_left',duration=45,rate=200,seed=17,profile=PROFILE,
             mount_deg=(0,0,0),temperature=29.75,accel_noise_g=0,gyro_noise_dps=0,
             accel_bias_g=(0,0,0),gyro_bias_dps=(0,0,0)):
    if profile!=PROFILE or scenario not in SCENARIOS:
        raise ValueError('Unknown profile/scenario')
    if not all(math.isfinite(v) for v in (duration,rate,temperature,accel_noise_g,gyro_noise_dps,*mount_deg,*accel_bias_g,*gyro_bias_dps)):
        raise ValueError('Non-finite generator parameter')
    if duration<=0 or rate<=0 or rate>1000000 or duration*rate>2000000 or min(accel_noise_g,gyro_noise_dps)<0:
        raise ValueError('Invalid duration/rate/noise; at most 2 million samples')
    n=duration*rate
    if abs(n-round(n))>1e-7:
        raise ValueError('duration * sample rate must be an integer')
    count=round(n);rng=random.Random(seed)
    if count<1:
        raise ValueError('At least one sample is required')
    mount=rotation(*(math.radians(x) for x in mount_deg));rows=[]
    for index in range(count):
        stamp=round(index*1000000/rate)
        force,gyro=model(stamp/1000000,scenario)
        accel_sensor=transform(mount,tuple(v/G for v in force),True)
        gyro_sensor=transform(mount,gyro,True)
        accel_sensor=[v+accel_bias_g[i]+rng.gauss(0,accel_noise_g) for i,v in enumerate(accel_sensor)]
        gyro_sensor=[v+gyro_bias_dps[i]+rng.gauss(0,gyro_noise_dps) for i,v in enumerate(gyro_sensor)]
        raw,physical=quantize(accel_sensor,gyro_sensor)
        rows.append([stamp,*raw,*(f'{v:.9f}' for v in physical),f'{temperature:.6f}'])
    return write_csv(path,rows,dict(source='SYNTHETIC',scenario=scenario,seed=seed,nominal_duration_s=duration,
        sample_count=count,sample_rate_hz=rate,timestamp_span_us=rows[-1][0]-rows[0][0],
        sample_interval_convention='half-open [0,duration); N=duration*rate',clock_id=f'synthetic-{seed}-{scenario}',
        clock_evidence='IMU_ONLY; radar synchronization requires same simulation run identity',
        mount_sensor_to_body_deg=list(mount_deg),vehicle_axes='X forward, Y left, Z up',
        specific_force='R_world_body^T * (a_world - [0,0,-9.80665])',
        accel_noise_g=accel_noise_g,gyro_noise_dps=gyro_noise_dps,accel_bias_g=list(accel_bias_g),gyro_bias_dps=list(gyro_bias_dps)))
