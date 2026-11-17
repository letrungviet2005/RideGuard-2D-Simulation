"""Headless smoke test for the PySide6 UI.

Creates the window offscreen, drives a short synthetic session and checks
that the warning panel reflects the session state. Run with:
    QT_QPA_PLATFORM=offscreen python tools/rideguard-simulator/tests/ui_smoke.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from rideguard_sim.ui import Window


def main():
    app = QApplication.instance() or QApplication([])
    window = Window(Path(tempfile.gettempdir()) / "rideguard-ui-smoke")
    window.show()

    # Offline synthetic session: the UI must still render and report levels.
    window.session.start(0)
    for _ in range(30):
        window.session.step()
        window.session.poll()
    window.refresh_panels()
    app.processEvents()

    print("session state:", window.session.state)
    print("result present:", window.session.result is not None)
    if window.session.result:
        result = window.session.result
        print("left level:", result["left"]["level"], result["left"]["reason_code"])
        print("right level:", result["right"]["level"], result["right"]["reason_code"])
    print("connection label:", window.connection_label.text())
    print("status label:", window.status.text())
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
