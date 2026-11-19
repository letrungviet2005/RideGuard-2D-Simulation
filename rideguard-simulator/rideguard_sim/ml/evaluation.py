"""Label/oracle-derived fields belong only in evaluation, never inference."""
import numpy as np
from sklearn.metrics import classification_report,confusion_matrix

CLASSES=['NONE','MONITOR','HIGH']


def _runs(rows,field):
    result=[];active=[]
    for row in rows:
        if row[field]=='HIGH' and (not active or row['timestamp_ms']-active[-1]['timestamp_ms']<=50):
            active.append(row)
        else:
            if active:result.append(active)
            active=[row] if row[field]=='HIGH' else []
    if active:result.append(active)
    return result


def metrics(rows):
    accepted=[r for r in rows if r['prediction'] in CLASSES]
    y=[r['label'] for r in accepted];p=[r['prediction'] for r in accepted]
    report=classification_report(y,p,labels=CLASSES,output_dict=True,zero_division=0)
    groups={}
    for row in rows:groups.setdefault((row['session_id'],row['side']),[]).append(row)
    total=hits=false=alerts=0;lead_high=[];lead_overlap=[]
    for group in groups.values():
        group=sorted(group,key=lambda r:r['timestamp_ms'])
        truth=_runs(group,'label');pred=_runs(group,'prediction')
        total+=len(truth);alerts+=len(pred)
        for run in pred:
            if not any(r['label']=='HIGH' for r in run):false+=1
        for run in truth:
            # Match only overlapping alert episodes, no future hindsight credit.
            matched=[episode for episode in pred if episode[0]['timestamp_ms']<=run[-1]['timestamp_ms'] and episode[-1]['timestamp_ms']>=run[0]['timestamp_ms']]
            if matched:
                hits+=1;first=min(episode[0]['timestamp_ms'] for episode in matched)
                lead_high.append((first-run[0]['timestamp_ms'])/1000)
                overlap=min(r['timestamp_ms']/1000+float(r['oracle_time_to_overlap_s']) for r in run if r['oracle_time_to_overlap_s']!='')
                lead_overlap.append(first/1000-overlap)
    minutes=len(rows)*.05/60  # side-minutes; explicitly two opportunities per time tick
    return dict(classification=report,confusion_matrix=confusion_matrix(y,p,labels=CLASSES).tolist(),
        class_order=CLASSES,coverage=len(accepted)/max(1,len(rows)),unknown_rows=len(rows)-len(accepted),
        events=dict(high_episodes=total,hit_episodes=hits,recall=hits/max(1,total),
                    predicted_high_episodes=alerts,false_high_episodes=false,false_high_per_side_minute=false/max(minutes,1e-9),
                    alert_minus_oracle_high_s=lead_high,alert_minus_overlap_s=lead_overlap))


def breakdown(rows):
    result={'all':metrics(rows),'scenario':{},'session':{},'maneuver_phase':{}}
    for field,key in [('scenario','scenario'),('session_id','session'),('phase','maneuver_phase')]:
        for value in sorted({r[field] for r in rows}):result[key][value]=metrics([r for r in rows if r[field]==value])
    return result
