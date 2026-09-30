"""Exercise the packaged entry points over real pipes without hardware actions."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QCoreApplication, QProcess
from ipadhub.model import Mode
from ipadhub.runtime import EngineProcess
from ipadhub.storage import HubStorage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    bundle.relative_to(ROOT / "build")
    report_path = args.report.resolve()
    report_path.relative_to(ROOT / "build")
    app = QCoreApplication([])
    storage = HubStorage(report_path.parent / "engine-smoke-data", registry=False)
    for directory in (storage.root, storage.display_dir, storage.bridge_dir, storage.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "hardware_commands_sent": [], "engines": []}
    try:
        for mode, command in (
            (Mode.BRIDGE, [str(bundle / "iPadHub.exe"), "--engine", "bridge"]),
            (Mode.DISPLAY, [str(bundle / "_internal/engines/iPadHubDisplay.exe"), "--no-device"]),
        ):
            engine = EngineProcess(mode, storage, command=command)
            messages, finishes, faults = [], [], []
            engine.message.connect(messages.append)
            engine.finished.connect(lambda clean, detail: finishes.append((clean, detail)))
            engine.fault.connect(faults.append)

            def wait_for(predicate):
                deadline = time.monotonic() + 20
                while not predicate() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.005)
                if not predicate():
                    raise RuntimeError(f"{mode.value} timeout: {faults!r} {finishes!r}")

            try:
                request = engine.send("status")
                engine.start()
                wait_for(lambda: any(item.get("request") == request for item in messages) or finishes)
                if finishes or faults:
                    raise RuntimeError(f"{mode.value} startup failed: {faults!r} {finishes!r}")
                response = next(item for item in messages if item.get("request") == request)
                payload = response.get("payload", {})
                if not response.get("ok") or payload.get("running") or payload.get("state") not in ("idle", "ready"):
                    raise RuntimeError(f"{mode.value} unexpected initial status: {response!r}")
                engine.send("shutdown")
                wait_for(lambda: bool(finishes))
                if finishes != [(True, "")] or faults:
                    raise RuntimeError(f"{mode.value} did not release cleanly: {finishes!r} {faults!r}")
                report["engines"].append({"engine": mode.value, "hello": True, "state": payload.get("state"),
                                          "clean_release": True, "exit_code": engine.process.exitCode()})
            finally:
                engine.deadline.stop()
                engine.server.close()
                if engine.process.state() != QProcess.ProcessState.NotRunning:
                    # No discover/start/configure command is ever sent here.
                    engine.process.kill()
                    engine.process.waitForFinished(3000)
        report["ok"] = True
    except Exception as error:
        report["error"] = str(error)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
