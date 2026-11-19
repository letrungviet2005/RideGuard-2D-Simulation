"""Read-only reporting. Labels are never passed into either processor.

50 ms cells, nearest observation within 25 ms of the cell start. No hold or
interpolation across gaps. Durations describe covered grid cells, not wall time.
"""
from bisect import bisect_left
import csv
import json
from pathlib import Path

LEVELS=('NONE','MONITOR','HIGH')
STEP_MS=50
TOLERANCE_MS=25


def index_records(records,session_id=None):
    indexed={}
    for record in records:
        if session_id is not None and record.get('session_id')!=session_id:continue
        if record.get('paused'):continue
        stamp=record.get('t_ms')
        if not isinstance(stamp,(int,float)) or stamp<0:continue
        # Prefer a valid observation at that sensor timestamp over a later
        # heartbeat saying the same old input has now become stale/stopped.
        valid=sum(level(record,side) is not None for side in ('left','right'))
        previous=indexed.get(stamp)
        prior_valid=sum(level(previous,side) is not None for side in ('left','right')) if previous else -1
        if valid>=prior_valid:indexed[stamp]=record
    stamps=sorted(indexed)
    return stamps,indexed


def nearest(index,stamp):
    stamps,records=index
    i=bisect_left(stamps,stamp)
    candidates=[t for t in stamps[max(0,i-1):i+1] if -TOLERANCE_MS<=t-stamp<TOLERANCE_MS]
    # Tie -> earlier record; never duplicate rows in scoring.
    return records[min(candidates,key=lambda t:(abs(t-stamp),t))] if candidates else None


def level(record,side):
    if not record:return None
    value=record.get(side,{}).get('level')
    if value not in LEVELS:return None
    sensors=record.get('sensors',{})
    if any(not sensor.get('valid',False) for sensor in sensors.values()):return None
    return value


def label_map(folder):
    """Evaluation-only reader. Do not import this in feature/inference code."""
    path=Path(folder)/'labels.csv'
    if not path.exists():return {}
    with path.open(encoding='utf-8-sig',newline='') as handle:
        return {(int(r['timestamp_ms']),r['side'].lower()):r['label'] for r in csv.DictReader(handle)}


def align(rules,ai,labels=None,session_id=None,end_ms=None):
    labels=labels or {};ri=index_records(rules,session_id);mi=index_records(ai,session_id)
    if end_ms is None:
        end_ms=max([0,*ri[0],*mi[0],*(t for t,_ in labels)])+STEP_MS
    rows=[]
    # Last partial cell is omitted rather than inventing 50 ms of data.
    for t in range(0,int(end_ms)//STEP_MS*STEP_MS,STEP_MS):
        rule=nearest(ri,t);model=nearest(mi,t)
        for side in ('left','right'):
            r=level(rule,side);a=level(model,side);truth=labels.get((t,side))
            rows.append(dict(timestamp_ms=t,side=side,esp32=r,ai=a,label=truth if truth in LEVELS else None,
                esp32_timestamp_ms=rule['t_ms'] if rule else None,ai_timestamp_ms=model['t_ms'] if model else None,
                comparable=r is not None and a is not None,
                disagreement=r is not None and a is not None and r!=a,
                # Fair accuracy comparison uses the same labelled READY grid;
                # unknown/missing is counted as uncovered, never as NONE.
                score_valid=truth in LEVELS and t>=1000 and r is not None and a is not None))
    return rows


def segments(rows):
    result=[]
    for side in ('left','right'):
        active=None
        for row in (r for r in rows if r['side']==side):
            if row['disagreement']:
                if active and row['timestamp_ms']==active['end_ms']:
                    active['end_ms']+=STEP_MS;active['cells']+=1
                else:
                    active=dict(side=side,start_ms=row['timestamp_ms'],end_ms=row['timestamp_ms']+STEP_MS,cells=1)
                    result.append(active)
            else:active=None
    return sorted(result,key=lambda r:(r['start_ms'],r['side']))


def classification(rows,predictor):
    matrix=[[0]*3 for _ in LEVELS]
    for row in rows:matrix[LEVELS.index(row['label'])][LEVELS.index(row[predictor])]+=1
    classes={}
    for i,name in enumerate(LEVELS):
        tp=matrix[i][i];support=sum(matrix[i]);predicted=sum(r[i] for r in matrix)
        precision=tp/predicted if predicted else 0;recall=tp/support if support else 0
        classes[name]=dict(precision=precision,recall=recall,f1=2*precision*recall/(precision+recall) if precision+recall else 0,support=support)
    high=LEVELS.index('HIGH');high_tp=matrix[high][high]
    return dict(valid_cells=len(rows),class_order=list(LEVELS),confusion_matrix=matrix,classes=classes,
        macro_f1=sum(v['f1'] for v in classes.values())/3 if rows else None,
        accuracy=sum(matrix[i][i] for i in range(3))/len(rows) if rows else None,
        high_missed_cells=sum(matrix[high])-high_tp,
        high_false_cells=sum(r[high] for r in matrix)-high_tp)


def summarize(rows):
    result=dict(step_ms=STEP_MS,tolerance_ms=TOLERANCE_MS,duration_policy='50 ms grid cells; gaps remain uncovered; last partial cell omitted',
        sources={},disagreement_segments=segments(rows),scoring={})
    for source in ('esp32','ai'):
        result['sources'][source]={}
        for side in ('left','right'):
            selected=[r for r in rows if r['side']==side]
            high=[r for r in selected if r[source]=='HIGH']
            result['sources'][source][side]=dict(first_high_ms=high[0]['timestamp_ms'] if high else None,
                first_high_observation_ms=high[0][source+'_timestamp_ms'] if high else None,
                high_ms=len(high)*STEP_MS,covered_ms=sum(r[source] is not None for r in selected)*STEP_MS,
                unknown_ms=sum(r[source] is None for r in selected)*STEP_MS)
    labelled=[r for r in rows if r['label'] is not None and r['timestamp_ms']>=1000]
    valid=[r for r in labelled if r['score_valid']]
    result['scoring']=dict(labelled_after_warmup_cells=len(labelled),common_valid_cells=len(valid),
        common_coverage=len(valid)/len(labelled) if labelled else None,
        esp32_uncovered_labelled=sum(r['esp32'] is None for r in labelled),ai_uncovered_labelled=sum(r['ai'] is None for r in labelled),
        predictors={source:classification(valid,source) for source in ('esp32','ai')})
    result['scoring']['high_uncovered_labelled']={source:sum(r['label']=='HIGH' and r[source] is None for r in labelled) for source in ('esp32','ai')}
    result['scoring']['by_side']={side:{source:classification([r for r in valid if r['side']==side],source) for source in ('esp32','ai')} for side in ('left','right')}
    result['comparable_cells']=sum(r['comparable'] for r in rows)
    result['disagreement_cells']=sum(r['disagreement'] for r in rows)
    return result


def read_log(path):
    rules=[];ai=[];starts=set()
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            row=json.loads(line);message=row.get('message',{})
            if row.get('direction')=='TX' and message.get('type')=='SESSION_START':starts.add(message['session_id'])
            if row.get('direction')=='RX' and message.get('type')=='RESULT':rules.append(message)
            elif row.get('direction')=='AI' and message.get('type')=='AI':ai.append(message)
    sessions={r['session_id'] for r in rules+ai}
    if len(starts)>1:raise ValueError('Comparison log contains multiple sessions; split explicitly')
    if starts:
        active=next(iter(starts))
        return [r for r in rules if r['session_id']==active],[r for r in ai if r['session_id']==active],active
    if len(sessions)>1:raise ValueError('Comparison log contains multiple sessions; split explicitly')
    return rules,ai,next(iter(sessions),None)


def export_comparison(rows,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    summary=summarize(rows)
    (folder/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if rows:
        with (folder/'aligned.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with (folder/'disagreements.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['side','start_ms','end_ms','cells']);writer.writeheader();writer.writerows(summary['disagreement_segments'])
    return summary
