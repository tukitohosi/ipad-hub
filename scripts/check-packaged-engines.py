"""Exercise the delivered executables through the real host without opening devices."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from ipadhub.model import Mode
from ipadhub.runtime import EngineProcess
from ipadhub.storage import HubStorage

parser = argparse.ArgumentParser()
parser.add_argument("--bundle", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
bundle = args.bundle.resolve()
app = QApplication([])
results = []
for mode, command in (
    (Mode.BRIDGE, [str(bundle / "iPadHub.exe"), "--engine", "bridge"]),
    (Mode.DISPLAY, [str(bundle / "_internal/engines/iPadHubDisplay.exe"), "--no-device"]),
):
    with tempfile.TemporaryDirectory(prefix="ipadhub-package-check-") as directory:
        storage = HubStorage(Path(directory), registry=None)
        storage.logs_dir.mkdir()
        engine = EngineProcess(mode, storage, command=command)
        messages, failures, finishes = [], [], []
        engine.message.connect(messages.append)
        engine.fault.connect(failures.append)
        engine.finished.connect(lambda clean, detail: finishes.append((clean, detail)))
        request = engine.send("status")
        engine.start()
        deadline = time.monotonic() + 20
        while not finishes and not any(m.get("request") == request and m.get("type") == "response" for m in messages) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        replies = [m for m in messages if m.get("request") == request and m.get("type") == "response"]
        engine.send("shutdown")
        deadline = time.monotonic() + 35
        while not finishes and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        ok = bool(replies and replies[0].get("ok") and finishes == [(True, "")] and not failures)
        results.append(dict(engine=mode.value, ok=ok, clean_exit=finishes, faults=failures,
                            commands=["status", "shutdown"], hardware_access=False))
        if engine.process.state() != engine.process.ProcessState.NotRunning:
            # Only this isolated status-only test child can exist here.
            engine.process.kill()
            engine.process.waitForFinished(5000)
        engine.deleteLater()
        app.processEvents()
report = dict(ok=all(r["ok"] for r in results), engines=results)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report["ok"] else 1)
