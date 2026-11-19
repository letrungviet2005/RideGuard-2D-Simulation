"""Trusted local artifact loader and causal incremental prediction engine."""
import hashlib
import json
from pathlib import Path
import time
from .features import Features,FEATURES
from .stream import Event


def load_trusted(repo,config_path):
    import joblib
    config=json.loads(Path(config_path).read_text());folder=repo/config['artifacts']
    if not folder.resolve().is_relative_to((repo/'.local/ai').resolve()):
        raise ValueError('Model must be a project-generated artifact under .local/ai; arbitrary pickle loading is disabled')
    manifest=json.loads((folder/'trusted-model.json').read_text())
    path=folder/'model.joblib'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:
        raise ValueError('MODEL_HASH: artifact damaged or not the trusted build')
    bundle=joblib.load(path)  # only our locally generated artifact; not a user upload
    if bundle['schema']!=config['schema'] or bundle['features']!=list(FEATURES) or bundle['config']!=config:
        raise ValueError('MODEL_SCHEMA: schema/config/feature order mismatch; train a new artifact')
    if set(bundle['model'].classes_)!={'NONE','MONITOR','HIGH'}:
        raise ValueError('MODEL_CLASSES: unsupported class mapping')
    if hasattr(bundle['model'],'n_jobs'):bundle['model'].n_jobs=1
    return bundle


class Engine:
    def __init__(self,bundle,session_id):
        self.bundle=bundle;self.session_id=session_id;self.features=Features(bundle['config'])
        self.next_us=0;self.count=0;self.digest=hashlib.sha256();self.last_us=-1

    def _predict(self,stamp):
        start=time.perf_counter_ns();pairs=[self.features.at(stamp,s) for s in ('LEFT','RIGHT')]
        levels={};ready=[i for i,(_,reason) in enumerate(pairs) if reason=='READY']
        predictions={};probabilities={}
        if ready:
            model=self.bundle['model'];x=[pairs[i][0] for i in ready]
            for i,p,proba in zip(ready,model.predict(x),model.predict_proba(x)):
                predictions[i]=str(p);probabilities[i]={str(k):float(v) for k,v in zip(model.classes_,proba)}
        for i,side in enumerate(('left','right')):
            levels[side]=dict(level=predictions.get(i,'UNKNOWN'),reason_code=pairs[i][1],scores=probabilities.get(i,{}))
        return dict(type='AI',session_id=self.session_id,t_ms=stamp//1000,**levels,
                    sensor_count=self.count,stream_sha256=self.digest.hexdigest(),inference_ms=(time.perf_counter_ns()-start)/1e6)

    def advance(self,events,t_us):
        out=[]
        for event in events:
            if event.session_id!=self.session_id:raise ValueError('Wrong inference session')
            if event.timestamp_us<self.last_us:raise ValueError('Non-monotonic common event stream')
            while self.next_us<event.timestamp_us:
                out.append(self._predict(self.next_us));self.next_us+=self.bundle['config']['step_us']
            self.features.ingest(event);self.digest.update(event.canonical());self.count+=1;self.last_us=event.timestamp_us
        while self.next_us<=t_us:
            out.append(self._predict(self.next_us));self.next_us+=self.bundle['config']['step_us']
        return out


def worker(repo,config_path):
    bundle=load_trusted(repo,config_path);engine=None
    print(json.dumps(dict(type='READY')),flush=True)
    import sys
    for line in sys.stdin:
        request=json.loads(line)
        if request['type']=='RESET':engine=Engine(bundle,request['session_id'])
        elif request['type']=='BATCH':
            if engine is None or request['session_id']!=engine.session_id:continue
            events=[Event(**row) for row in request['events']]
            for result in engine.advance(events,request['t_us']):print(json.dumps(result),flush=True)
            print(json.dumps(dict(type='AI_STREAM',session_id=engine.session_id,t_ms=request['t_us']//1000,
                sensor_count=engine.count,stream_sha256=engine.digest.hexdigest())),flush=True)

