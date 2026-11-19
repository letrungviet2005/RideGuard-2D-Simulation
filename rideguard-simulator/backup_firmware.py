"""Read the current firmware off the ESP32 into a backup file.

The board's CP210x bridge is wired non-standard: ``RTS`` reaches ``GPIO0``
(BOOT) and ``DTR`` reaches ``EN`` (reset).  esptool assumes the opposite
mapping, so its own reset strategy always boots the application and it
reports "Wrong boot mode detected (0x13)".

This script therefore performs the bootloader handshake itself and then
hands the *already open* serial socket to esptool's Python API:

  1. assert RTS (GPIO0 low) and release DTR (EN high)
  2. pulse DTR low to reset the chip while GPIO0 is still low
  3. release DTR -- the chip now starts in the ROM bootloader
  4. keep the socket open (RTS stays asserted, so the chip stays in the
     bootloader) and let esptool talk to the ROM over that same socket

Run this *before* flashing so the currently installed firmware is kept.
"""
import argparse
import sys
import time
from pathlib import Path

import serial

ESPTOOL_DIR = Path.home() / ".platformio/packages/tool-esptoolpy"
TRIGGER = Path(".flash-trigger")


def wait_for_trigger(path, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            path.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return False


def enter_bootloader(port, baud=115200):
    """Reset the chip into the ROM bootloader and return the open socket.

    The socket must stay open: closing it releases RTS, which lets GPIO0 go
    high and the chip leaves the bootloader before esptool can talk to it.
    """
    sock = serial.Serial(port, baud, timeout=0.2)
    sock.reset_input_buffer()
    # GPIO0 low, EN released.
    sock.dtr = False
    sock.rts = True
    time.sleep(0.2)
    # Reset while GPIO0 is low.
    sock.dtr = True
    time.sleep(0.2)
    sock.dtr = False
    time.sleep(0.8)
    captured = sock.read(4096)
    print("chip reset into the bootloader", flush=True)
    print(f"  rom bytes: {len(captured)}  head: {captured[:120]!r}", flush=True)
    for marker in (b"boot:", b"rst:"):
        index = captured.find(marker)
        if index >= 0:
            end = captured.find(b"\r\n", index)
            print(f"  {captured[index:end].decode(errors='replace')}", flush=True)
    if b"boot:" not in captured:
        sock.close()
        print("\nCHIP KHÔNG Ở BOOTLOADER.", flush=True)
        return None
    print("chip is in the bootloader", flush=True)
    return sock


def read_flash(sock, offset, size, out):
    """Read the flash through esptool using the already open socket."""
    sys.path.insert(0, str(ESPTOOL_DIR))
    from esptool.cmds import detect_chip  # noqa: E402

    with detect_chip("ESP32", port=sock) as esp:
        # "no_reset" -- the chip is already in the bootloader and the socket
        # keeps GPIO0 low, so any reset would drop us back to the app.
        esp.connect("no_reset")
        print("esptool connected to the ROM bootloader", flush=True)
        esp.flash_read(address=int(offset, 0), length=int(size, 0), filename=str(out))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--offset", default="0x0")
    parser.add_argument("--size", default="0x400000")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"HOLD BOOT (optional), then create the trigger file: {TRIGGER.resolve()}", flush=True)
    print("waiting for trigger...", flush=True)
    if not wait_for_trigger(TRIGGER, args.timeout):
        print("timed out waiting for trigger")
        return 1

    sock = enter_bootloader(args.port, args.baud)
    if sock is None:
        return 1
    try:
        read_flash(sock, args.offset, args.size, out)
    finally:
        # Release GPIO0 and pulse EN so the board runs its application again.
        try:
            sock.rts = False
            sock.dtr = False
            time.sleep(0.1)
            sock.dtr = True
            time.sleep(0.15)
            sock.dtr = False
            time.sleep(0.5)
        except Exception:
            pass
        sock.close()

    if out.exists() and out.stat().st_size > 0:
        print(f"\nBACKUP OK -> {out} ({out.stat().st_size} bytes)")
        return 0
    print("\nBACKUP FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
