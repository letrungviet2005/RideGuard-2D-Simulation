"""Check UART integrity in application mode by validating RESULT JSON lines.

The application firmware emits one JSON object per line at 100 ms. Any
byte corruption shows up as unparsable lines or transport notices.
"""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import json
import time

import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--baud", type=int, default=921600)
    args = parser.parse_args()

    sock = serial.Serial(args.port, args.baud, timeout=0.2)
    sock.dtr = False
    sock.rts = False
    time.sleep(0.3)
    sock.reset_input_buffer()
    raw = b""
    until = time.monotonic() + args.seconds
    while time.monotonic() < until:
        chunk = sock.read(8192)
        if chunk:
            raw += chunk
    sock.close()

    lines = [line for line in raw.split(b"\n") if line.strip()]
    good = 0
    bad = []
    for line in lines:
        try:
            decoded = json.loads(line)
            if isinstance(decoded, dict):
                good += 1
                continue
        except Exception:
            pass
        bad.append(line[:110])
    print(f"bytes={len(raw)} lines={len(lines)} valid_json={good} corrupt={len(bad)}")
    for line in bad[:6]:
        print("  corrupt:", repr(line))


if __name__ == "__main__":
    main()
