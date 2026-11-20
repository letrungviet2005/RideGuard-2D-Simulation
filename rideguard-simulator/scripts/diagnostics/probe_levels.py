"""Show the board's per-side levels over time for one logged session."""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import json
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    results = [r["message"] for r in rows
               if r["direction"] == "RX" and r["message"].get("type") == "RESULT"]
    print(f"{path.name}: {len(results)} RESULT")
    for message in results:
        left = message["left"]
        right = message["right"]
        targets = message.get("targets", [])
        summary = ",".join(f"{t.get('track_id')}@{t.get('y_m')}m/{t.get('v_kmh')}kph"
                           for t in targets)
        print(f"  t={message['t_ms']:6} motion={message.get('motion'):8} "
              f"L={left['level']:8}({left.get('reason_code')}) "
              f"R={right['level']:8}({right.get('reason_code')}) "
              f"yaw={message.get('filtered_yaw_dps')} [{summary}]")


if __name__ == "__main__":
    main()
