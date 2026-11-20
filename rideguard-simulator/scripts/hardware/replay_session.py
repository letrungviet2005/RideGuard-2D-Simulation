"""Replay actual TX records with a new session id; RX records are never sent."""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import json
from pathlib import Path
import time
import uuid
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.protocol import BAUD, CONTROL


def run(path,port):
    rows=[json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    tx=[r for r in rows if r.get('direction') in ('TX','OFFLINE')]
    if not tx or tx[0]['message']['type']!='SESSION_START':
        raise ValueError('Log must begin with SESSION_START')
    if any(b['wall_ns']<a['wall_ns'] for a,b in zip(tx,tx[1:])):
        raise ValueError('Non-monotonic wall log')
    link=SerialLink(port,BAUD);session=uuid.uuid4().hex
    out=Path(path).with_name(Path(path).stem+'-replayed-'+session[:8]+'.jsonl')
    try:
        # Drain the board's boot-ROM handshake noise before the first line.
        if not link.ready.wait(8):raise TimeoutError('Serial link never became ready')
        start=time.monotonic();base=tx[0]['wall_ns']
        with out.open('w',encoding='utf-8',buffering=1) as log:
            def receive():
                found=[]
                while not link.rx.empty():
                    _,msg=link.rx.get_nowait();found.append(msg)
                    log.write(json.dumps(dict(direction='RX',wall_ns=time.time_ns(),message=msg))+'\n')
                    if msg.get('type')=='ERROR':raise RuntimeError(msg)
                if link.error:raise RuntimeError(link.error)
                return found
            for row in tx:
                while time.monotonic()-start<(row['wall_ns']-base)/1e9:
                    receive();time.sleep(.002)
                msg=dict(row['message']);msg['session_id']=session
                if msg['type'] not in CONTROL|{'IMU','RADAR_FRAME'}:raise ValueError('Unsupported log type')
                link.send(msg);log.write(json.dumps(dict(direction='TX',wall_ns=time.time_ns(),message=msg))+'\n')
                if msg['type'] in CONTROL:
                    deadline=time.monotonic()+3
                    while True:
                        if any(m.get('type')=='ACK' and m.get('session_id')==session and
                               m.get('command')==msg['type'] and m.get('seq')==msg['seq'] for m in receive()):break
                        if time.monotonic()>deadline:raise TimeoutError('Control ACK missing')
                        time.sleep(.002)
            end=time.monotonic()+1.2
            while time.monotonic()<end:receive();time.sleep(.01)
        print(f'Replayed TX only; real RX log: {out}')
    finally:link.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('log');parser.add_argument('--port',required=True)
    args=parser.parse_args();run(args.log,args.port)
