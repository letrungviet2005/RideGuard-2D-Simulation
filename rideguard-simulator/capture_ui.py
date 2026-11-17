"""Render the simulator UI to PNG files for visual verification.

Captures the window in three states:
  1. offline (no board),
  2. connected to the real ESP32 over COM5,
  3. running a scenario with the board's real feedback applied.

Usage:
    python tools/rideguard-simulator/capture_ui.py --port COM5 --out .local/ui
"""
import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from rideguard_sim.ui import Window


def grab(window, path, title):
    QApplication.processEvents()
    pixmap = window.grab()
    pixmap.save(str(path))
    print(f"{title}: {path} ({pixmap.width()}x{pixmap.height()})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--out", default=".local/ui")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    window = Window(out.parent / "simulator-logs")
    window.resize(1460, 900)
    window.show()

    # 1. Offline.
    grab(window, out / "01-offline.png", "offline")

    # 2. Connected, waiting for the board.
    from rideguard_sim.protocol import BAUD
    from rideguard_sim.serial_link import SerialLink
    window.session.link = SerialLink(args.port, BAUD)
    window.refresh_panels()
    grab(window, out / "02-connecting.png", "connecting")

    # 3. Running with the board's real feedback.
    # The window's own QTimer drives the simulation, so only pump the event
    # loop here; stepping manually would race with tick().
    window.session.wait_link_ready(10)
    window.refresh_panels()
    window.session.start(0)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    window.refresh_panels()
    result = window.session.visible_result()
    print("board result:", None if not result else
          {side: result[side]["level"] for side in ("left", "right")})
    grab(window, out / "03-running-real-feedback.png", "running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
