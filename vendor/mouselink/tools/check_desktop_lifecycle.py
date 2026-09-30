"""Exercise the real serial handoff without enabling edge capture."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open_bridge"))
from PySide6 import QtCore, QtWidgets
import desktop_app as ui
from bridge import KVMController, keyboard

state_dir = ROOT / "test-results/desktop-lifecycle-state"
state_dir.mkdir(exist_ok=True)
ui.app_data = lambda: state_dir


def local_only(config):
    config.edge_enabled = False
    config.toggle_key = keyboard.Key.f24
    return KVMController(config)


ui.KVMController = local_only
app = QtWidgets.QApplication([])
window = ui.Window()
window.show()
results = []
started = time.monotonic()
mode_index = 0
choices = [("free", False), ("free", True), ("locked", False)]
phase = "ready"


def poll():
    global mode_index, phase
    if time.monotonic() - started > 20:
        results.append({"error": "timeout", "phase": phase, "worker": window.worker.error})
        window.close()
        return
    state = window.worker.snapshot()
    if phase == "ready" and state["ready"]:
        mode, hotkey = choices[mode_index]
        window.cards[mode].radio.setChecked(True)
        window.hotkey_toggle.setChecked(hotkey)
        window.refresh()
        assert window.start_button.isEnabled()
        window.toggle_bridge()
        phase = "running"
    elif phase == "running" and window.worker.running_bridge:
        window.refresh()
        assert not window.cards["free"].isEnabled()
        assert window.start_button.isEnabled()
        results.append({"mode": window.effective_mode(), "running": True, "remote": state["remote"]})
        assert not state["remote"]
        window.toggle_bridge()
        phase = "stopped"
    elif phase == "stopped" and not window.worker.running_bridge and state["ready"]:
        results[-1]["stopped_and_serial_reopened"] = True
        mode_index += 1
        if mode_index == len(choices):
            phase = "closed"
            window.close()
        else:
            phase = "ready"


timer = QtCore.QTimer()
timer.timeout.connect(poll)
timer.start(150)
app.exec()
assert not window.worker.isRunning()
(ROOT / "test-results/1.0.1-hardware-lifecycle.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
assert len(results) == 3 and all(r.get("stopped_and_serial_reopened") for r in results), results
print("All three behaviors with a visible window: start, stop, serial reopen, close passed; no remote input capture.")
