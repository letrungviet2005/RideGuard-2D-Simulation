"""Report frozen test-suite evidence; no fit, inference, or threshold changes."""
import csv
import json
from pathlib import Path
from .comparison import align,label_map,read_log,export_comparison,summarize
from .stream import check_split
from .test_suite import protected_hashes


def report_suite(repo,config_path,input_dir,output):
    config=json.loads(config_path.read_text());root=repo/config['dataset']
    manifest=json.loads((root/'sessions.json').read_text());check_split(manifest)
    expected=[r['session_id'] for r in manifest if r['split']=='test']
    input_dir=Path(input_dir);output=Path(output)
    if output.exists():raise ValueError('Report output exists; preserve previous evidence')
    progress=json.loads((input_dir/'progress.json').read_text());plan=json.loads((input_dir/'plan.json').read_text())
    if plan['sessions']!=expected or progress.get('status')!='PASS' or progress['completed']!=expected:
        raise ValueError('Test suite incomplete or differs from predeclared split; do not report complete')
    if plan['protected_before']!=progress['protected_after'] or protected_hashes(repo,config)!=plan['protected_before']:
        raise ValueError('Frozen model/rules/config/feature hashes differ')
    output.mkdir(parents=True)
    all_rows=[];details={};transport={};flat=[]
    for sid in expected:
        folder=input_dir/sid;logs=list(folder.glob('20*.jsonl'))
        if len(logs)!=1:raise ValueError(f'Expected one raw session log: {sid}')
        rules,ai,session_id=read_log(logs[0]);summary=json.loads((folder/'summary.json').read_text())
        if not rules or not ai:raise ValueError('Missing hardware/model evidence')
        if summary['parser_errors_max']!=0 or summary['common_sensor_events']!=summary['actual_sensor_writes']:
            raise ValueError(f'Parser/write failure: {sid}')
        if len({summary[k] for k in ('expected_sha256','pc_sha256','ai_sha256')})!=1:
            raise ValueError(f'Stream hash mismatch: {sid}')
        rows=align(rules,ai,label_map(root/'sessions'/sid),session_id,end_ms=summary['simulation_duration_s']*1000)
        details[sid]=export_comparison(rows,output/sid);transport[sid]=summary
        all_rows.extend(rows)
        for row in rows:flat.append(dict(dataset_session=sid,**row))
    aggregate=summarize(all_rows)
    # Episode ranges and first HIGH are per session, not a concatenation of
    # identical 0..24s clocks from 20 sessions. Keep those exclusively in details.
    aggregate.pop('disagreement_segments');aggregate.pop('sources')
    result=dict(test_sessions=expected,completed=len(details),aggregate=aggregate,sessions=details,
        transport=transport,protected=plan['protected_before'],
        conclusion='No evidence of overall AI superiority: compare labelled macro-F1, HIGH recall AND false HIGH separately; synthetic only. Agreement is not accuracy.')
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'all-aligned.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    summary_rows=[]
    for sid,detail in details.items():
        for source,sides in detail['sources'].items():
            for side,values in sides.items():
                score=detail['scoring']['by_side'][side][source]
                summary_rows.append(dict(dataset_session=sid,source=source,side=side,**values,
                    common_valid_cells=score['valid_cells'],high_support=score['classes']['HIGH']['support'],
                    high_missed_cells=score['high_missed_cells'],high_false_cells=score['high_false_cells'],
                    high_recall=score['classes']['HIGH']['recall'],high_precision=score['classes']['HIGH']['precision']))
    with (output/'session-summary.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(summary_rows[0]));writer.writeheader();writer.writerows(summary_rows)
    lines=['# So sánh 20 phiên test — model và bộ luật đóng băng','',
        'Nguồn luật: RESULT ESP32 thật. Mức đồng ý không phải độ chính xác. Lưới 50 ms, dung sai [-25,+25) ms; không nội suy qua gap.',
        '','Không đủ bằng chứng kết luận AI tốt hơn tổng thể hoặc ngoài thực tế. Số liệu dưới chỉ áp dụng dataset giả lập và mặt nạ chung có nhãn.','']
    scoring=aggregate['scoring'];lines.append(f"Mốc chung hợp lệ: {scoring['common_valid_cells']}/{scoring['labelled_after_warmup_cells']}.")
    for name,m in scoring['predictors'].items():
        lines.append(f"- {name}: macro-F1 {m['macro_f1']:.6f}, HIGH recall {m['classes']['HIGH']['recall']:.6f}, bỏ sót HIGH {m['high_missed_cells']} mốc, báo HIGH nhầm {m['high_false_cells']} mốc.")
    for sid,detail in details.items():
        lines.extend(['',f'## {sid}',f"Bất đồng {detail['disagreement_cells']}/{detail['comparable_cells']} mốc có hai kết quả (không phải accuracy)."])
        for source,sides in detail['sources'].items():
            for side,s in sides.items():
                first=f"{s['first_high_ms']} ms" if s['first_high_ms'] is not None else 'chưa quan sát'
                observed=f"{s['first_high_observation_ms']} ms" if s['first_high_observation_ms'] is not None else 'chưa quan sát'
                lines.append(f"- {source}/{side}: HIGH đầu (lưới) {first}; quan sát gốc {observed}; HIGH tổng {s['high_ms']} ms; thiếu {s['unknown_ms']} ms.")
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result
