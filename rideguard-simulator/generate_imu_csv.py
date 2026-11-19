"""Generate 14-column IMU CSV plus provenance JSON. No serial operations."""
import argparse
from rideguard_sim.imu_csv import PROFILE,SCENARIOS,generate

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--scenario',choices=SCENARIOS,default='lane_left')
    p.add_argument('--duration',type=float,default=45)
    p.add_argument('--rate',type=float,default=200)
    p.add_argument('--seed',type=int,default=17)
    p.add_argument('--profile',choices=[PROFILE],default=PROFILE)
    p.add_argument('--mount-deg',type=float,nargs=3,default=(0,0,0),metavar=('ROLL','PITCH','YAW'))
    p.add_argument('--temperature',type=float,default=29.75)
    p.add_argument('--accel-noise-g',type=float,default=0)
    p.add_argument('--gyro-noise-dps',type=float,default=0)
    p.add_argument('--accel-bias-g',type=float,nargs=3,default=(0,0,0))
    p.add_argument('--gyro-bias-dps',type=float,nargs=3,default=(0,0,0))
    a=p.parse_args()
    print(generate(a.output,a.scenario,a.duration,a.rate,a.seed,a.profile,a.mount_deg,a.temperature,
                   a.accel_noise_g,a.gyro_noise_dps,a.accel_bias_g,a.gyro_bias_dps))
