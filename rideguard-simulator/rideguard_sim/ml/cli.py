import argparse
import json
from pathlib import Path


def main(repo):
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['features','train','evaluate','worker','replay','test-suite','compare-suite','select'])
    parser.add_argument('--config',default='ai/configs/sensor-lab.json')
    parser.add_argument('--session',default='RG10_03')
    parser.add_argument('--port')
    parser.add_argument('--output',default='.local/ai/replay')
    parser.add_argument('--split',choices=['train','validation','test'],default='train')
    parser.add_argument('--input')
    args=parser.parse_args();config_path=(repo/args.config).resolve()
    if args.command=='select':
        from .selection import run
        run(repo,config_path,repo/args.output)
    elif args.command=='compare-suite':
        if not args.input:parser.error('compare-suite needs --input raw test-suite directory')
        from .suite_report import report_suite
        result=report_suite(repo,config_path,repo/args.input,repo/args.output)
        print(json.dumps(dict(completed=result['completed'],scoring=result['aggregate']['scoring']),indent=2))
    elif args.command=='test-suite':
        from .test_suite import run_test_suite
        run_test_suite(repo,config_path,args.port,repo/args.output)
    elif args.command=='features':
        import numpy as np
        from .training import prepare,write_json
        from .features import FEATURES
        from .stream import check_split
        config=json.loads(config_path.read_text());dataset=repo/config['dataset']
        manifest=json.loads((dataset/'sessions.json').read_text());check_split(manifest)
        x,rows,_=prepare(dataset,manifest,args.split,config)
        output=repo/args.output;output.mkdir(parents=True,exist_ok=True)
        if (output/f'{args.split}-features.npz').exists():raise ValueError('Feature output already exists')
        np.savez_compressed(output/f'{args.split}-features.npz',X=x)
        write_json(output/f'{args.split}-rows.json',dict(features=list(FEATURES),rows=rows))
    elif args.command=='train':
        from .training import train
        train(repo,config_path)
    elif args.command=='worker':
        from .inference import worker
        worker(repo,config_path)
    elif args.command=='evaluate':
        from .inference import load_trusted
        from .training import prepare,predict_rows,write_json
        from .evaluation import breakdown
        bundle=load_trusted(repo,config_path);config=bundle['config']
        manifest=json.loads((repo/config['dataset']/'sessions.json').read_text())
        output=repo/args.output
        if output.exists():raise ValueError('Evaluation output exists; choose a new evidence directory')
        output.mkdir(parents=True)
        for split in ('validation','test'):
            x,rows,rules=prepare(repo/config['dataset'],manifest,split,config)
            predicted=predict_rows(bundle['model'],x,rows)
            for row,p in zip(rows,rules):row['prediction']=p
            write_json(output/f'{split}-reevaluation.json',dict(ai=breakdown(predicted),offline_rules=breakdown(rows)))
    else:
        from .acceptance import replay
        replay(repo,config_path,args.session,args.port,repo/args.output)
