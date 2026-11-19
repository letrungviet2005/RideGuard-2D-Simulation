"""Run the predeclared test sessions, without fitting or threshold selection."""
import hashlib
import json
from pathlib import Path
import time
from .stream import check_split
from .acceptance import replay


def protected_hashes(repo,config):
    paths=[repo/config['artifacts']/'model.joblib',repo/'ai/configs/sensor-lab.json',
        repo/'firmware/include/sim_rules.hpp',repo/'firmware/include/sim_state.hpp',
        repo/'firmware/include/sim_input.hpp',repo/'firmware/src/sensor_simulation.cpp',
        Path(__file__).parent/'features.py',Path(__file__).parent/'inference.py',Path(__file__).parent/'offline_rules.py']
    return {p.relative_to(repo).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def run_test_suite(repo,config_path,port,output):
    if not port:raise ValueError('ESP32 test suite requires an explicitly discovered Serial port')
    config=json.loads(config_path.read_text());manifest=json.loads((repo/config['dataset']/'sessions.json').read_text())
    check_split(manifest);sessions=[r['session_id'] for r in manifest if r['split']=='test']
    output=Path(output)
    if output.exists():raise ValueError('Output exists; preserve previous evidence')
    output.mkdir(parents=True);before=protected_hashes(repo,config)
    plan=dict(sessions=sessions,port=port,baud=921600,speed=.5,protected_before=before)
    (output/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    progress=dict(completed=[],failed=None,started_wall=time.time())
    try:
        for sid in sessions:
            print(f'BEGIN {sid} ({len(progress["completed"])+1}/{len(sessions)})',flush=True)
            replay(repo,config_path,sid,port,output/sid)
            progress['completed'].append(sid)
            (output/'progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
            if protected_hashes(repo,config)!=before:raise RuntimeError('Protected model/rules changed; stop test suite')
        progress['status']='PASS'
    except Exception as error:
        progress['status']='FAILED';progress['failed']=str(error)
        raise
    finally:
        progress['duration_s']=time.time()-progress['started_wall']
        progress['protected_after']=protected_hashes(repo,config)
        (output/'progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
