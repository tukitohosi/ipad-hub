"""Supervisor invariants, protocol validation and hardware-free host handshake."""
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication

from ipadhub.model import Mode, State
from ipadhub.runtime import EngineProcess, MAX_FRAME, RuntimeController


class FakeEngine(QObject):
    message = Signal(dict)
    finished = Signal(bool, str)
    fault = Signal(str)

    def __init__(self, mode, storage, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.session = uuid4().hex
        self.sent = []
        self.started = False
        self.released = False

    def start(self):
        self.started = True

    def send(self, command, payload=None):
        request = uuid4().hex
        self.sent.append((request, command, payload or {}))
        return request

    def event(self, name, payload):
        self.message.emit(dict(v=1, session=self.session, request="", type="event",
                               event=name, payload=payload))

    def release(self):
        self.released = True
        self.event("released", {"clean": True, "released": True})

    def exit(self, normal=True):
        clean = self.released and normal
        self.finished.emit(clean, "" if clean else "Unconfirmed resource release")


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.engines = []
        def factory(*args):
            engine = FakeEngine(*args)
            self.engines.append(engine)
            return engine
        self.controller = RuntimeController(SimpleNamespace(), factory=factory)
        self.stops = []
        self.controller.stopped.connect(lambda: self.stops.append(True))
        self.addCleanup(self.controller.stop_timer.stop)

    def active(self, mode):
        self.assertTrue(self.controller.request(mode, "start", {"target": "test"}))
        engine = self.controller.engine
        engine.event("status", {"state": "active", "running": True, "detail": "ready"})
        return engine

    def test_fifty_roundtrips_wait_for_release_and_exit(self):
        old = self.active(Mode.DISPLAY)
        for _ in range(50):
            for mode in (Mode.BRIDGE, Mode.DISPLAY):
                count = len(self.engines)
                self.assertTrue(self.controller.request(mode, "start"))
                self.assertEqual(self.controller.state, State.STOPPING)
                self.assertIs(self.controller.engine, old)
                self.assertEqual(old.sent[-1][1], "shutdown")
                old.release()
                self.assertEqual(len(self.engines), count)
                self.assertIs(self.controller.engine, old)
                old.exit()
                self.assertEqual(len(self.engines), count + 1)
                new = self.controller.engine
                self.assertIsNot(new, old)
                self.assertEqual(self.controller.owner, mode)
                self.assertTrue(new.started)
                self.assertEqual(new.sent[0][1], "start")
                new.event("status", {"state": "active", "detail": "active"})
                old = new
        self.assertEqual(len(self.engines), 101)

    def test_cancel_pending_switch_never_launches_target(self):
        engine = self.active(Mode.BRIDGE)
        self.controller.request(Mode.DISPLAY, "start")
        self.controller.stop()
        self.assertIsNone(self.controller.pending)
        engine.release()
        engine.exit()
        self.assertEqual(len(self.engines), 1)
        self.assertIsNone(self.controller.engine)
        self.assertEqual(self.controller.state, State.READY)

    def test_cancel_while_connecting_ignores_late_active_status(self):
        self.controller.request(Mode.BRIDGE, "start")
        engine = self.controller.engine
        self.controller.stop()
        engine.event("status", {"state": "active", "detail": "late"})
        self.assertEqual(self.controller.state, State.STOPPING)
        engine.release()
        engine.exit()
        engine.event("status", {"state": "active", "detail": "stale"})
        self.assertEqual(self.controller.state, State.READY)
        self.assertIsNone(self.controller.engine)

    def test_release_failure_blocks_all_new_modes(self):
        engine = self.active(Mode.BRIDGE)
        self.controller.request(Mode.DISPLAY, "start")
        engine.exit()
        self.assertTrue(self.controller.release_blocked)
        self.assertFalse(self.controller.request(Mode.DISPLAY, "start"))
        self.assertFalse(self.controller.request(Mode.BRIDGE, "discover"))
        self.assertFalse(self.controller.recover())
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(self.stops, [])

    def test_timeout_blocks_until_genuine_cleanup_and_cancels_switch(self):
        engine = self.active(Mode.DISPLAY)
        self.controller.request(Mode.BRIDGE, "start")
        self.controller._stop_timeout()
        self.assertTrue(self.controller.release_blocked)
        self.assertIsNone(self.controller.pending)
        self.assertFalse(self.controller.request(Mode.BRIDGE, "start"))
        self.assertTrue(self.controller.recover())
        self.assertIs(self.controller.engine, engine)
        engine.release()
        engine.exit()
        self.assertFalse(self.controller.release_blocked)
        self.assertIsNone(self.controller.engine)
        self.assertEqual(len(self.engines), 1)

    def test_fault_cancels_queued_mode_and_preserves_resource_block(self):
        engine = self.active(Mode.BRIDGE)
        self.controller.request(Mode.DISPLAY, "start")
        engine.fault.emit("pipe failed")
        self.assertTrue(self.controller.release_blocked)
        self.assertIsNone(self.controller.pending)
        engine.event("status", {"state": "ready", "detail": "late"})
        self.assertEqual(self.controller.state, State.FAILED)
        self.assertEqual(self.controller.detail, "pipe failed")

    def test_previous_engine_messages_faults_and_exit_do_not_affect_new_session(self):
        old = self.active(Mode.DISPLAY)
        self.controller.request(Mode.BRIDGE, "start")
        old.release()
        old.exit()
        current = self.controller.engine
        current.event("status", {"state": "active", "detail": "current"})
        old.event("status", {"state": "failed", "detail": "stale"})
        old.fault.emit("old pipe disconnected")
        old.exit(normal=False)
        self.assertIs(self.controller.engine, current)
        self.assertEqual(self.controller.state, State.ACTIVE)
        self.assertEqual(self.controller.detail, "current")
        self.assertFalse(self.controller.release_blocked)

    def test_normal_quit_waits_for_released_and_process_exit(self):
        engine = self.active(Mode.BRIDGE)
        self.assertTrue(self.controller.quit())
        self.assertTrue(self.controller.quitting)
        self.assertFalse(self.controller.request(Mode.DISPLAY, "start"))
        self.assertEqual(self.stops, [])
        engine.release()
        self.assertEqual(self.stops, [])
        engine.exit()
        self.assertEqual(self.stops, [True])
        self.assertIsNone(self.controller.engine)

    def test_quit_cancels_pending_mode(self):
        engine = self.active(Mode.DISPLAY)
        self.controller.request(Mode.BRIDGE, "start")
        self.controller.quit()
        engine.release()
        engine.exit()
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(self.stops, [True])

    def test_flash_ownership_blocks_modes_stop_and_quit(self):
        self.controller.busy_tool = True
        self.assertFalse(self.controller.request(Mode.BRIDGE, "start"))
        self.assertFalse(self.controller.request(Mode.DISPLAY, "discover"))
        self.assertFalse(self.controller.quit())
        self.controller.stop()
        self.assertEqual(self.engines, [])
        self.assertEqual(self.stops, [])


class BufferSocket:
    def __init__(self):
        self.incoming = b""
        self.writes = []
        self.aborted = False

    def readAll(self):
        value, self.incoming = self.incoming, b""
        return value

    def write(self, raw):
        self.writes.append(raw)
        return len(raw)

    def flush(self):
        return True

    def abort(self):
        self.aborted = True


class EngineProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.engine = EngineProcess(Mode.BRIDGE, SimpleNamespace(logs_dir=Path(tempfile.gettempdir())))
        self.socket = BufferSocket()
        self.engine.socket = self.socket
        self.messages, self.faults, self.finishes = [], [], []
        self.engine.message.connect(self.messages.append)
        self.engine.fault.connect(self.faults.append)
        self.engine.finished.connect(lambda clean, detail: self.finishes.append((clean, detail)))
        self.addCleanup(self.engine.deadline.stop)

    def feed(self, event, payload=None, **override):
        message = dict(v=1, session=self.engine.session, type="event", request="",
                       event=event, payload=payload or {})
        message.update(override)
        self.socket.incoming += (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        self.engine._read()

    def test_cancel_before_hello_drops_queued_start(self):
        self.engine.send("start")
        self.engine.send("shutdown")
        self.feed("hello", {"engine": "bridge"})
        self.assertEqual([json.loads(raw)["command"] for raw in self.socket.writes], ["shutdown"])

    def test_wrong_session_aborts_and_never_emits_status(self):
        self.feed("status", {"state": "active"}, session="stale-session")
        self.assertTrue(self.socket.aborted)
        self.assertTrue(self.faults)
        self.assertEqual(self.messages, [])

    def test_wrong_engine_in_hello_aborts(self):
        self.feed("hello", {"engine": "display"})
        self.assertTrue(self.socket.aborted)
        self.assertFalse(self.engine.ready)

    def test_invalid_version_aborts(self):
        self.feed("status", {"state": "active"}, v=2)
        self.assertTrue(self.socket.aborted)
        self.assertEqual(self.messages, [])

    def test_hello_missing_payload_aborts_without_key_error(self):
        self.socket.incoming = (json.dumps(dict(v=1, session=self.engine.session,
            type="event", request="", event="hello")) + "\n").encode("utf-8")
        self.engine._read()
        self.assertTrue(self.socket.aborted)
        self.assertTrue(self.faults)
        self.assertFalse(self.engine.ready)
        self.assertEqual(self.messages, [])

    def test_frame_limit_aborts_without_dispatch(self):
        self.socket.incoming = b"x" * (MAX_FRAME + 1)
        self.engine._read()
        self.assertTrue(self.socket.aborted)
        self.assertEqual(self.messages, [])

    def test_release_event_alone_is_not_process_completion(self):
        self.feed("released", {"engine": "bridge", "released": True, "clean": True})
        self.assertTrue(self.engine.clean_release)
        self.assertEqual(self.finishes, [])
        self.engine._exited(0, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.finishes, [(True, "")])

    def test_process_exit_without_release_is_not_safe(self):
        self.engine._exited(0, QProcess.ExitStatus.NormalExit)
        self.assertFalse(self.finishes[0][0])

    def test_crash_despite_release_is_not_safe(self):
        self.feed("released", {"clean": True})
        self.engine._exited(-1, QProcess.ExitStatus.CrashExit)
        self.assertFalse(self.finishes[0][0])

    def test_release_string_cannot_mark_clean(self):
        self.feed("released", {"clean": "true"})
        self.engine._exited(0, QProcess.ExitStatus.NormalExit)
        self.assertFalse(self.finishes[0][0])


@unittest.skipUnless(os.name == "nt", "Windows Qt named-pipe integration")
class RealQtHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_qt_host_engine_status_shutdown_without_discovering_hardware(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = SimpleNamespace(root=root, display_dir=root / "display", bridge_dir=root / "mouselink",
                                      logs_dir=root / "logs")
            storage.logs_dir.mkdir()
            engine = EngineProcess(Mode.BRIDGE, storage)
            messages, finishes, faults = [], [], []
            engine.message.connect(messages.append)
            engine.finished.connect(lambda clean, detail: finishes.append((clean, detail)))
            engine.fault.connect(faults.append)
            def wait_for(predicate):
                deadline = time.monotonic() + 10
                while not predicate() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertTrue(predicate(), repr((faults, finishes, messages)))
            try:
                request = engine.send("status")
                engine.start()
                wait_for(lambda: any(message.get("request") == request for message in messages) or finishes)
                self.assertFalse(finishes)
                status = next(message for message in messages if message.get("request") == request)
                self.assertEqual(status["payload"]["state"], "idle")
                self.assertFalse(status["payload"]["running"])
                engine.send("shutdown")
                wait_for(lambda: bool(finishes))
                self.assertEqual(finishes, [(True, "")])
                self.assertEqual(faults, [])
                self.assertFalse((storage.bridge_dir / "settings.json").exists())
            finally:
                engine.deadline.stop()
                engine.server.close()
                if engine.process.state() != QProcess.ProcessState.NotRunning:
                    engine.process.kill()  # This test never sends discover or start.
                    engine.process.waitForFinished(3000)


if __name__ == "__main__":
    unittest.main()
