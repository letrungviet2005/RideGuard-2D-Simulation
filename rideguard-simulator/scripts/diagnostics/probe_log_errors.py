"""Report errors and reason codes from a simulator session log."""

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
    errors = [(index, row) for index, row in enumerate(rows, 1)
              if row["direction"] == "RX" and row["message"].get("type") == "ERROR"]
    print(f"messages={len(rows)} errors={len(errors)}")
    for index, row in errors[:8]:
        message = row["message"]
        print(f"  idx={index} seq={message.get('seq')} cmd={message.get('command')} "
              f"reason={message.get('reason_code')} sess={message.get('session_id')!r}")

    results = [row["message"] for row in rows
               if row["direction"] == "RX" and row["message"].get("type") == "RESULT"]
    if results:
        reasons = sorted({r["left"]["reason_code"] for r in results})
        print(f"left reason codes: {reasons}")
        print("first 5:", [(r["seq"], r["t_ms"], r["left"]["reason_code"]) for r in results[:5]])
        print("last 5: ", [(r["seq"], r["t_ms"], r["left"]["reason_code"]) for r in results[-5:]])
        sessions = sorted({str(r.get("session_id")) for r in results})
        print(f"sessions in RESULT: {sessions}")


if __name__ == "__main__":
    main()
