"""Real Qt host / C++ display-engine interop without discovering any hardware."""
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from ipadhub.model import Mode
from ipadhub.runtime import EngineProcess


DISPLAY_ENGINE = (Path(__file__).resolve().parents[1]
                  / "build/ipadhub-display/Release/iPadHubDisplay.exe")
# unittest discovery imports every module before starting its first test. Keep
# the shared application a QApplication, so earlier QCoreApplication-only suites
# can reuse it and subsequent UI tests can still create widgets safely.
_APP = ((QApplication.instance() or QApplication([]))
        if os.name == "nt" and DISPLAY_ENGINE.is_file() else None)


@unittest.skipUnless(os.name == "nt", "Windows native named-pipe integration")
@unittest.skipUnless(DISPLAY_ENGINE.is_file(), "Build iPadHubDisplay.exe first")
class DisplayHostInteropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _APP
        if not isinstance(cls.app, QApplication):
            raise unittest.SkipTest("Run independently before a QCoreApplication-only session")

    def test_real_cpp_engine_status_and_shutdown_release_cleanly(self):
        with tempfile.TemporaryDirectory(prefix="ipadhub-display-host-") as temporary:
            root = Path(temporary)
            storage = SimpleNamespace(root=root, display_dir=root / "display",
                                      bridge_dir=root / "mouselink", logs_dir=root / "logs")
            storage.logs_dir.mkdir()
            # No discover/start is ever sent. The native engine's no-device
            # guard also prevents accidental future edits from touching hardware.
            engine = EngineProcess(Mode.DISPLAY, storage,
                                   command=[str(DISPLAY_ENGINE), "--no-device"])
            messages, finishes, faults = [], [], []
            engine.message.connect(messages.append)
            engine.finished.connect(lambda clean, detail: finishes.append((clean, detail)))
            engine.fault.connect(faults.append)

            def wait_for(predicate):
                deadline = time.monotonic() + 10
                while not predicate() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertTrue(predicate(), repr(dict(faults=faults, finishes=finishes,
                                                       messages=messages)))

            try:
                status_request = engine.send("status")
                engine.start()
                wait_for(lambda: any(item.get("request") == status_request for item in messages)
                         or bool(finishes))
                self.assertFalse(finishes, "Native process exited before status response")
                hello = next(item for item in messages if item.get("event") == "hello")
                self.assertEqual(hello["payload"], {"engine": "display", "version": "0.1.0"})
                status = next(item for item in messages if item.get("request") == status_request)
                self.assertIs(status["ok"], True)
                self.assertEqual(status["payload"]["state"], "idle")
                self.assertIs(status["payload"]["running"], False)
                self.assertIs(status["payload"]["receiver_ready"], False)
                self.assertFalse(engine.clean_release)

                shutdown_request = engine.send("shutdown")
                wait_for(lambda: bool(finishes))
                shutdown = next(item for item in messages
                                if item.get("request") == shutdown_request)
                release = next(item for item in messages if item.get("event") == "released")
                self.assertIs(shutdown["ok"], True)
                self.assertIs(release["payload"]["clean"], True)
                self.assertEqual(finishes, [(True, "")])
                self.assertEqual(faults, [])
                self.assertEqual(engine.process.state(), QProcess.ProcessState.NotRunning)
                self.assertEqual(engine.process.exitCode(), 0)
                self.assertEqual(engine.process.exitStatus(), QProcess.ExitStatus.NormalExit)
                self.assertTrue(all(item["v"] == 1 and item["session"] == engine.session
                                    for item in messages))
                self.assertFalse((storage.display_dir / "config.json").exists())
                self.assertFalse(storage.bridge_dir.exists())
            finally:
                engine.deadline.stop()
                engine.server.close()
                if engine.process.state() != QProcess.ProcessState.NotRunning:
                    if engine.socket is not None:
                        engine.socket.abort()
                    if not engine.process.waitForFinished(3000):
                        engine.process.kill()  # --no-device; only status/shutdown were sent.
                        engine.process.waitForFinished(3000)


if __name__ == "__main__":
    unittest.main()
