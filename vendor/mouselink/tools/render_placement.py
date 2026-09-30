"""Render the real placement UI with simulated status, without opening USB."""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open_bridge"))
from PySide6 import QtCore, QtGui, QtWidgets as W
from desktop_app import Window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1000)
    parser.add_argument("--height", type=int, default=880)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ["MOUSELINK_DATA_DIR"] = str(args.out / "preview-data")
    app = W.QApplication([])
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 10))
    window = Window(preview=True)
    window.resize(args.width, args.height)
    window.timer.stop()
    window.show()
    checks = []

    def capture(name):
        app.processEvents()
        assert window.grab().save(str(args.out / f"{name}.png"))
        buttons = list(window.side_buttons.values()) + [window.start_button]
        for button in buttons:
            # Verify that primary controls remain inside the scrollable viewport.
            position = button.mapTo(window.centralWidget().viewport(), QtCore.QPoint(0, 0))
            rect = QtCore.QRect(position, button.size())
            assert window.centralWidget().viewport().rect().contains(rect), (name, button.text(), rect)
        checks.append({"state": name, "side": window.selected_side(),
                       "size": [window.width(), window.height()],
                       "scale": window.devicePixelRatioF()})

    try:
        for side in ("right", "left"):
            window.side_changed(side)
            capture(side)
        # Only display a simulated switching status; no worker or hooks run.
        window.preview = False
        window.worker = SimpleNamespace(
            snapshot=lambda: {"usb": False, "ble": False, "ready": False, "remote": False},
            running_bridge=False, switching=True, busy=False, error="")
        window.refresh()
        capture("switching")
        assert window.start_button.isEnabled()
        assert not window.check_button.isEnabled()
        assert not window.speed.isEnabled()
        (args.out / "layout.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    finally:
        window.preview = True
        window.worker = None
        window.close()
    print(f"Rendered {len(checks)} UI states to {args.out.resolve()}")


if __name__ == "__main__":
    main()
