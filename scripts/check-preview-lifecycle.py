"""Bounded Windows-only test of preview startup, tray close and clean exit.

Only this harness's children are stopped. A per-run virtual home changes the
preview instance name, so the test cannot activate a user's preview window.
No virtual-home directory is created and no device engine is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def child(suite: str, attempt: str) -> int:
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon
    import ipadhub.app as hub

    def emit(event, **fields):
        print(json.dumps(dict(event=event, attempt=attempt, pid=os.getpid(), **fields)), flush=True)

    # Isolate only the instance-name home input; normal paths still load UI assets.
    # The mutex and activation code remain unchanged in all five children.
    home_patch = patch.object(hub.Path, "home", return_value=ROOT / ("lifecycle-test-" + suite))
    home_patch.start()
    constructed = []

    class ObservedWindow(hub.HubWindow):
        def __init__(self):
            super().__init__()
            constructed.append(self)
            self._test_closed = False
            self.setWindowTitle("iPadHub · 生命周期验证 " + suite[:8])
            emit("constructed", platform=QApplication.platformName())
            QTimer.singleShot(1600, self.probe_window)
            QTimer.singleShot(2400, self.close_for_test)
            QTimer.singleShot(6500, self.quit_for_test)

        def probe_window(self):
            visible = sum(isinstance(w, QMainWindow) and w.isVisible()
                          for w in QApplication.topLevelWidgets())
            emit("window_probe", visibleMainWindows=visible, trayVisible=self.tray.isVisible())

        def close_for_test(self):
            self._test_closed = True
            self.close()
            QTimer.singleShot(150, lambda: emit(
                "close_result", windowHidden=not self.isVisible(),
                trayAvailable=QSystemTrayIcon.isSystemTrayAvailable(),
                trayVisible=self.tray.isVisible()))

        def show_window(self):
            super().show_window()
            emit("activation", afterTestClose=self._test_closed, windowVisible=self.isVisible())

        def quit_for_test(self):
            emit("quit_requested", windowVisible=self.isVisible(), trayVisible=self.tray.isVisible())
            self.quit_app()

    hub.HubWindow = ObservedWindow
    emit("ready")
    start_at = float(sys.stdin.readline().strip())
    time.sleep(max(0, start_at - time.monotonic()))
    watchdog = threading.Timer(15, lambda: os._exit(91))
    watchdog.daemon = True
    watchdog.start()
    sys.argv = [sys.argv[0], "--preview"]
    try:
        result = hub.main()
        emit("finished", result=result, constructors=len(constructed),
             trayVisible=constructed[0].tray.isVisible() if constructed else None,
             ownerReleased=constructed[0].session.owner is None if constructed else None)
        return result
    finally:
        watchdog.cancel()
        home_patch.stop()


def orchestrate() -> int:
    if sys.platform != "win32":
        raise RuntimeError("This lifecycle check requires the real Windows Qt backend.")
    suite = uuid4().hex
    app_path = ROOT / "ipadhub" / "app.py"
    original_hash = hashlib.sha256(app_path.read_bytes()).hexdigest()
    events = []
    updates = queue.Queue()
    processes = []
    failures = []
    started = time.monotonic()

    def launch(attempt):
        env = os.environ.copy()
        env.update(QT_QPA_PLATFORM="windows", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONIOENCODING="utf-8")
        env.pop("QT_SCALE_FACTOR", None)
        p = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--child", suite, attempt],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        item = dict(attempt=attempt, process=p, errors=[])
        processes.append(item)

        def read_stdout():
            for line in p.stdout:
                try:
                    updates.put(json.loads(line))
                except ValueError:
                    item["errors"].append(line.rstrip())

        def read_stderr():
            for line in p.stderr:
                item["errors"].append(line.rstrip())

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        return item

    def wait_for(predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Lifecycle event wait timed out.")
            try:
                events.append(updates.get(timeout=min(remaining, 0.2)))
            except queue.Empty:
                if all(x["process"].poll() is not None for x in processes):
                    raise RuntimeError("Children exited before the expected lifecycle event.")

    def check(name, condition):
        if not condition:
            failures.append(name)

    try:
        initial = [launch("concurrent-" + str(i)) for i in range(1, 5)]
        wait_for(lambda: sum(e["event"] == "ready" for e in events) == 4)
        start_at = time.monotonic() + 0.3
        for item in initial:
            item["process"].stdin.write(str(start_at) + "\n")
            item["process"].stdin.flush()
        wait_for(lambda: any(e["event"] == "close_result" for e in events))
        later = launch("later-reopen")
        wait_for(lambda: any(e["event"] == "ready" and e["attempt"] == "later-reopen" for e in events))
        later["process"].stdin.write(str(time.monotonic()) + "\n")
        later["process"].stdin.flush()
        wait_for(lambda: sum(e["event"] == "finished" for e in events) == 5)
        for item in processes:
            item["process"].wait(timeout=3)
        while not updates.empty():
            events.append(updates.get_nowait())

        constructors = [e for e in events if e["event"] == "constructed"]
        finished = [e for e in events if e["event"] == "finished"]
        close = next(e for e in events if e["event"] == "close_result")
        owner_exit = next(e for e in finished if e["constructors"] == 1)
        check("exactly_one_constructor", len(constructors) == 1)
        check("real_windows_platform", all(e["platform"] == "windows" for e in constructors))
        check("three_concurrent_followers_exit_without_window", sum(
            e["attempt"].startswith("concurrent-") and e["constructors"] == 0 and e["result"] == 0
            for e in finished) == 3)
        check("one_visible_main_window", any(
            e["event"] == "window_probe" and e["visibleMainWindows"] == 1 for e in events))
        check("close_hides_to_tray", close["windowHidden"] and close["trayVisible"] and close["trayAvailable"])
        check("later_start_reactivates_existing_window", any(
            e["event"] == "activation" and e["afterTestClose"] and e["windowVisible"] for e in events))
        check("later_start_has_no_constructor", any(
            e["attempt"] == "later-reopen" and e["constructors"] == 0 and e["result"] == 0 for e in finished))
        check("quit_clears_preview_session_and_tray", owner_exit["ownerReleased"] and not owner_exit["trayVisible"])
        check("all_five_exit_cleanly", all(x["process"].returncode == 0 for x in processes))
        check("source_unchanged_during_check", hashlib.sha256(app_path.read_bytes()).hexdigest() == original_hash)
    except Exception as exc:
        failures.append(type(exc).__name__ + ": " + str(exc))
    finally:
        # Only handles returned by this harness's own Popen calls are touched.
        for item in processes:
            p = item["process"]
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
        report = dict(
            schemaVersion=1, suite=suite, appSHA256=original_hash,
            elapsedSeconds=round(time.monotonic() - started, 3),
            namespaceIsolation="Virtual Path.home input used only for preview instance-name hashing.",
            passed=not failures, failures=failures, events=events,
            processes=[dict(attempt=x["attempt"], pid=x["process"].pid,
                            exitCode=x["process"].returncode, stderr=x["errors"]) for x in processes],
            allOwnedProcessesExited=all(x["process"].poll() is not None for x in processes),
        )
        out = ROOT / "artifacts" / "preview" / ("lifecycle-" + time.strftime("%Y%m%d-%H%M%S") + "-" + suite[:8] + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(passed=report["passed"], failures=failures,
                             allOwnedProcessesExited=report["allOwnedProcessesExited"], report=str(out)),
                         ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", nargs=2, metavar=("SUITE", "ATTEMPT"))
    arguments = parser.parse_args()
    raise SystemExit(child(*arguments.child) if arguments.child else orchestrate())
