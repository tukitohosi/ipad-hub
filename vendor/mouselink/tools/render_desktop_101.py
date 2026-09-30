"""Capture real Qt layouts at selected scale; no USB or input capture."""
import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open_bridge"))
from PySide6 import QtCore, QtGui, QtWidgets as W
from desktop_app import Window, STYLE
from flash_dialog import FlashDialog

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--width", type=int, default=1000)
parser.add_argument("--height", type=int, default=800)
parser.add_argument("--native-edges", action="store_true")
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
app = W.QApplication([])
window = Window(preview=True, native_edges=True,
                trial_seconds=180 if args.native_edges else 0)
window.resize(args.width, args.height)
window.show()
checks = []


def capture_layout():
    window.grab().save(str(args.out / "main.png"))
    toggle = window.hotkey_toggle
    assert window.cards["free"].rect().contains(toggle.mapTo(window.cards["free"], toggle.rect().bottomRight()))
    for name, card in window.cards.items():
        assert card.rect().contains(card.radio.geometry())
        checks.append({"card": name, "width": card.width(), "height": card.height()})
    window.hotkey_toggle.click()
    window.grab().save(str(args.out / "hotkey-on.png"))
    dialog = FlashDialog(ROOT / "test-results", window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(args.out / "flash.png"))
    assert dialog.rect().contains(dialog.flash_button.mapTo(dialog, dialog.flash_button.rect().bottomRight()))
    checks.append({"scale": window.devicePixelRatioF(), "window": [window.width(), window.height()],
                   "dialog": [dialog.width(), dialog.height()]})
    (args.out / "layout.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    dialog.close()
    window.close()


def capture():
    try:
        capture_layout()
    except Exception:
        traceback.print_exc()
        app.exit(1)


QtCore.QTimer.singleShot(250, capture)
raise SystemExit(app.exec())
