"""Finite validation selection. Test is opened only after winner is frozen."""
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time
import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from .stream import read_rows,sensor_events,check_split
from .features import FEATURES,snapshots
from .evaluation import metrics,breakdown
from .offline_rules import OfflineRules


def write_json(path,data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def prepare(root,manifest,split,config):
    x=[];rows=[];rule_predictions=[]
    for meta in manifest:
        if meta['split']!=split:continue
        folder=root/'sessions'/meta['session_id']
        events=sensor_events(folder);labels=read_rows(folder/'labels.csv')
        by_time={}
        for row in labels:by_time.setdefault(int(row['timestamp_ms'])*1000,[]).append(row)
        rules=OfflineRules();index=0
        for stamp,values in snapshots(events,sorted(by_time),config):
            while index<len(events) and events[index].timestamp_us<=stamp:
                rules.ingest(events[index]);index+=1
            rule=rules.at(stamp)
            for row in by_time[stamp]:
                feature,reason=values[row['side']]
                rows.append(dict(row,timestamp_ms=int(row['timestamp_ms']),session_id=meta['session_id'],scenario=meta['scenario'],quality=reason,
                    phase='no_maneuver' if meta['lateral_displacement_m']==0 else ('before_maneuver' if stamp/1e6<meta['maneuver_start_s'] else 'after_maneuver'),
                    prediction='UNKNOWN'))
                x.append(feature);rule_predictions.append(rule[row['side']] if reason=='READY' else 'UNKNOWN')
    return np.asarray(x),rows,rule_predictions


def predict_rows(model,x,rows):
    result=[dict(r) for r in rows]
    keep=[i for i,r in enumerate(rows) if r['quality']=='READY']
    if keep:
        predictions=model.predict(x[keep])
        for i,p in zip(keep,predictions):result[i]['prediction']=str(p)
    return result


def train(repo,config_path):
    config=json.loads(config_path.read_text());dataset=repo/config['dataset'];out=repo/config['artifacts']
    if out.exists():raise ValueError('Artifact directory already exists; use a new relative output directory to preserve frozen results')
    started=time.monotonic()
    # Execute the supplied validator BEFORE feature extraction or model fitting.
    validation=subprocess.run([sys.executable,str(dataset/'validate.py')],capture_output=True,text=True,encoding='utf-8')
    if validation.returncode:raise ValueError('Dataset validate.py failed: '+validation.stdout+validation.stderr)
    manifest=json.loads((dataset/'sessions.json').read_text());counts=check_split(manifest)
    # Full raw fingerprint includes held-out files as opaque bytes, not model inputs.
    digest=hashlib.sha256();file_hashes={}
    for path in sorted(dataset.rglob('*')):
        if not path.is_file() or '__pycache__' in str(path) or path.name=='SHA256SUMS.txt':continue
        rel=path.relative_to(dataset).as_posix();h=hashlib.sha256(path.read_bytes()).hexdigest()
        file_hashes[rel]=h;digest.update((rel+' '+h+'\n').encode())
    out.mkdir(parents=True)
    (out/'dataset-validation.txt').write_text(validation.stdout,encoding='utf-8')
    write_json(out/'config.json',config);write_json(out/'split.json',manifest)
    write_json(out/'fingerprint.json',dict(sha256=digest.hexdigest(),files=file_hashes))
    train_x,train_rows,_=prepare(dataset,manifest,'train',config)
    val_x,val_rows,val_rules=prepare(dataset,manifest,'validation',config)
    keep=[i for i,r in enumerate(train_rows) if r['quality']=='READY']
    y=np.asarray([train_rows[i]['label'] for i in keep]);seed=config['seed']
    candidates=[('dummy',DummyClassifier(strategy='prior'))]
    for depth in (4,8):
        for weight in (None,'balanced'):
            candidates.append((f'tree-d{depth}-{weight}',DecisionTreeClassifier(max_depth=depth,min_samples_leaf=20,class_weight=weight,random_state=seed)))
    for weight in (None,'balanced_subsample'):
        candidates.append((f'forest-48-d10-{weight}',RandomForestClassifier(n_estimators=48,max_depth=10,min_samples_leaf=10,class_weight=weight,random_state=seed,n_jobs=2)))
    leaderboard=[];winner=None;best=-float('inf')
    for name,model in candidates:
        model.fit(train_x[keep],y)
        predicted=predict_rows(model,val_x,val_rows);m=metrics(predicted)
        # Fixed before viewing test: not accuracy-only; penalize false alert episodes.
        score=m['classification']['macro avg']['f1-score']+.25*m['classification']['HIGH']['recall']-.02*m['events']['false_high_per_side_minute']
        leaderboard.append(dict(name=name,selection_score=score,metrics=m,parameters=model.get_params()))
        print(f'{name}: validation macro-F1={m["classification"]["macro avg"]["f1-score"]:.4f}, HIGH recall={m["classification"]["HIGH"]["recall"]:.4f}',flush=True)
        if score>best:best=score;winner=(name,model)
    name,model=winner
    write_json(out/'selection-frozen.json',dict(selected=name,score=best,criterion='macro_F1 + 0.25*HIGH_recall - 0.02*false_HIGH_episodes_per_side_minute',candidates=leaderboard))
    # Freeze model/schema BEFORE opening test labels/features. No post-test fitting.
    artifact=dict(model=model,schema=config['schema'],features=list(FEATURES),classes=['NONE','MONITOR','HIGH'],config=config)
    joblib.dump(artifact,out/'model.joblib')
    versions={p:importlib.metadata.version(p) for p in ('numpy','scipy','scikit-learn','joblib','threadpoolctl')}
    write_json(out/'schema.json',dict(version=config['schema'],features=list(FEATURES),classes=artifact['classes'],units='SI acceleration m/s2; gyro deg/s; explicitly suffixed feature units',config=config,dependencies=versions))
    write_json(out/'trusted-model.json',dict(file='model.joblib',sha256=hashlib.sha256((out/'model.joblib').read_bytes()).hexdigest(),schema=config['schema']))
    test_x,test_rows,test_rules=prepare(dataset,manifest,'test',config)
    report=dict(selected=name,split_counts=counts,dependencies=versions,dataset_fingerprint=digest.hexdigest(),
                train_rows=len(train_rows),train_ready_rows=len(keep),duration_s=0,splits={})
    for split,x,rows,rule in [('validation',val_x,val_rows,val_rules),('test',test_x,test_rows,test_rules)]:
        predicted=predict_rows(model,x,rows)
        for r,p in zip(rows,rule):r['prediction']=p
        report['splits'][split]=dict(ai=breakdown(predicted),offline_rules=breakdown(rows))
        with (out/f'{split}-predictions.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(predicted[0])+['offline_rule']);writer.writeheader()
            writer.writerows(dict(r,offline_rule=p) for r,p in zip(predicted,rule))
    report['duration_s']=time.monotonic()-started
    write_json(out/'report.json',report)
    print(json.dumps(dict(selected=name,artifact=str(out.relative_to(repo)),duration_s=report['duration_s'])),flush=True)
