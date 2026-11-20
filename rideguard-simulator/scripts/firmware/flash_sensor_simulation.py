"""Flash the sensor_simulation firmware with the physical BOOT button held.

The IDE shell has no interactive stdin, so this script uses a file as the
handshake signal:

  1. The script prints a prompt and waits for <trigger-file> to appear.
  2. The user holds BOOT and creates that file (or runs the helper below).
  3. The chip is pulsed into the bootloader and esptool attaches without
     resetting it again.

Because the chip remains in the bootloader until it is reset, only the
initial EN pulse needs BOOT to be held.
"""

# Keep direct CLI execution working after moving this helper below scripts/.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import argparse
import subprocess
import sys
import time
from pathlib import Path

import serial

ESPTOOL = Path.home() / ".platformio/packages/tool-esptoolpy/esptool.py"
FIRMWARE = "R:/firmware/.pio/build/sensor_simulation/firmware.bin"
TRIGGER = Path(".flash-trigger")


def wait_for_trigger(path, timeout=300):
    """Wait until the trigger file exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            path.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--firmware", default=FIRMWARE)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    print(f"HOLD BOOT, then create the trigger file: {TRIGGER.resolve()}", flush=True)
    print("waiting for trigger...", flush=True)
    if not wait_for_trigger(TRIGGER, args.timeout):
        print("timed out waiting for trigger")
        return 1

    sock = serial.Serial(args.port, 115200, timeout=0.2)
    sock.dtr = False
    sock.rts = False
    time.sleep(0.1)
    sock.reset_input_buffer()
    sock.dtr = True     # EN low -> reset while GPIO0 is held low
    time.sleep(0.15)
    sock.dtr = False
    time.sleep(0.6)
    # Capture whatever the ROM emits: this shows whether BOOT was held.
    captured = sock.read(4096)
    sock.close()
    print("chip reset; BOOT can be released now", flush=True)
    print(f"  rom bytes: {len(captured)}  head: {captured[:120]!r}", flush=True)
    for marker in (b"boot:", b"rst:"):
        index = captured.find(marker)
        if index >= 0:
            end = captured.find(b"\r\n", index)
            print(f"  {captured[index:end].decode(errors='replace')}", flush=True)
    command = [sys.executable, str(ESPTOOL), "--chip", "esp32", "--port", args.port,
               "--baud", str(args.baud), "--before", "no_reset", "--after", "hard_reset",
               "write_flash", "0x10000", args.firmware]
    result = subprocess.run(command, capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    print("\n".join(output.splitlines()[-8:]))
    if result.returncode == 0 and "Hash of data verified" in output:
        print("\nFLASH OK - press EN once to run the new firmware.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
