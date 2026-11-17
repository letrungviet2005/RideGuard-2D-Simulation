import argparse
from pathlib import Path
from PySide6.QtWidgets import QApplication
from .ui import Window

parser=argparse.ArgumentParser()
parser.add_argument('--log-dir',default=str(Path(__file__).resolve().parents[3]/'.local'/'simulator-logs'))
args=parser.parse_args()
app=QApplication([])
window=Window(args.log_dir)
window.show()
app.exec()
