"""Lifecycle/protocol regression tests. Fake Worker never opens input or serial."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from PySide6 import QtCore
from PySide6.QtWidgets import QApplication

from ipadhub.engines.bridge import BridgeEngine, read_settings
from ipadhub.engines.pipe_client import (BridgeResourceLock, JsonLines, MAX_FRAME_BYTES,
                                        NamedPipeClient, ProtocolError, encode_message,
                                        kernel_api)


class FakeWorker(QtCore.QObject):
    finished = QtCore.Signal()
    start_handled = QtCore.Signal(object)
    calibration_done = QtCore.Signal(str, bool, str)

    def __init__(self, **kwargs):
        super().__init__()
        self.running_bridge = self.busy = self.finished_flag = False
        self.usb = self.ble = self.ready = self.remote = False
        self.error = self.phase = ""
        self.port = "COM3"
        self.device_serial = "board-one"
        self.started = self.stop_calls = self.shutdown_calls = 0
        self.start_requests = []
        self.side_requests = []
        self.calibration_requests = []

    def start(self):
        self.started += 1

    def snapshot(self):
        return {key: getattr(self, key) for key in ("usb", "ble", "ready", "remote")}

    def request_start(self, *args):
        self.start_requests.append(args)
        self.running_bridge = True
        self.start_handled.emit(args[-1])
        return True

    def stop_bridge(self):
        self.stop_calls += 1
        self.running_bridge = False

    def request_shutdown(self):
        self.shutdown_calls += 1
        self.running_bridge = False

    def complete_shutdown(self):
        self.finished_flag = True
        self.finished.emit()

    def isFinished(self):
        return self.finished_flag

    def wait(self, milliseconds=0):
        return self.finished_flag

    def request_side_change(self, *args):
        self.side_requests.append(args)

    def request_calibration(self, *args):
        self.calibration_requests.append(args)
        return True


class FakeLock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class BridgeEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = 100.0
        self.messages = []
        self.quits = []
        self.lock = FakeLock()
        self.worker = FakeWorker()
        self.engine = BridgeEngine("session-one", self.root, self.messages.append,
            lambda: self.quits.append(True), lambda **kwargs: self.worker,
            lambda: [SimpleNamespace(device="COM3", serial_number="board-one",
                description="Long Chinese name 中文开发板", vid=0x303A, pid=0x1001)],
            lock_factory=lambda: self.lock, clock=lambda: self.now)

    def command(self, command, payload=None, request=None, session="session-one"):
        request = request or str(uuid.uuid4())
        self.engine.handle({"v": 1, "session": session, "request": request,
                            "command": command, "payload": payload or {}})
        return request

    def response(self, request):
        return next((message for message in reversed(self.messages)
                     if message.get("request") == request and message.get("type") == "response"), None)

    def ready(self):
        self.worker.usb = self.worker.ble = self.worker.ready = True

    def test_discover_monitor_does_not_start_controller(self):
        self.command("discover")
        self.assertEqual(self.worker.started, 1)
        self.assertEqual(self.worker.start_requests, [])
        self.assertFalse(self.lock.closed)

    def test_start_waits_for_real_usb_ble_absolute_and_running(self):
        request = self.command("start", {"mode": "free", "speed": .65,
                                        "hotkey_return_enabled": True, "ipad_side": "left"})
        self.engine.tick()
        self.worker.usb = self.worker.ble = True
        self.engine.tick()
        self.assertEqual(self.worker.start_requests, [])
        self.assertIsNone(self.response(request))
        self.worker.ready = True
        self.engine.tick()
        self.assertEqual(self.worker.start_requests[0][:3], ("mixed", .65, "left"))
        self.assertIsNone(self.response(request))
        self.engine.tick()
        self.assertTrue(self.response(request)["ok"])
        self.assertEqual(self.response(request)["payload"]["state"], "active")

    def test_start_timeout_does_not_report_active(self):
        request = self.command("start")
        self.now += 21
        self.engine.tick()
        self.assertFalse(self.response(request)["ok"])
        self.assertEqual(self.engine.snapshot()["state"], "failed")
        self.assertFalse(self.engine.released)
        self.assertFalse(self.worker.running_bridge)

    def test_stop_cancels_pending_start_and_waits_for_thread_release(self):
        request = self.command("start")
        stop = self.command("stop")
        self.assertFalse(self.response(request)["ok"])
        self.assertEqual(self.worker.shutdown_calls, 1)
        self.assertIsNone(self.response(stop))
        self.assertFalse(self.lock.closed)
        self.assertEqual(self.quits, [])
        self.worker.start_handled.emit(request)
        self.worker.complete_shutdown()
        self.assertTrue(self.response(stop)["payload"]["released"])
        self.assertTrue(self.lock.closed)
        self.assertEqual(self.quits, [True])
        self.assertEqual(self.messages[-1]["event"], "released")
        self.assertTrue(self.messages[-1]["payload"]["clean"])

    def test_stop_timeout_keeps_resource_lock_and_blocks_new_start(self):
        self.command("discover")
        stop = self.command("stop")
        self.now += 16
        self.engine.tick()
        self.assertFalse(self.response(stop)["ok"])
        self.assertFalse(self.response(stop)["payload"]["released"])
        self.assertFalse(self.lock.closed)
        self.assertFalse(self.engine.released)
        self.assertEqual(self.quits, [])
        self.assertFalse(self.response(self.command("start"))["ok"])
        self.worker.complete_shutdown()
        self.assertTrue(self.lock.closed)
        self.assertEqual(self.quits, [True])

    def test_pipe_loss_shutdown_does_not_emit_false_release(self):
        self.command("discover")
        count = len(self.messages)
        self.engine.disconnected("pipe gone")
        self.assertEqual(self.worker.shutdown_calls, 1)
        self.assertFalse(self.engine.released)
        self.worker.complete_shutdown()
        self.assertEqual(len(self.messages), count)
        self.assertEqual(self.quits, [True])

    def test_stale_session_and_duplicate_request_never_repeat_action(self):
        self.command("start", session="old-session")
        self.assertEqual(self.worker.started, 0)
        request = self.command("discover", request="same")
        self.command("discover", request=request)
        self.assertEqual(self.worker.started, 1)
        self.command("start", request="pending")
        self.command("start", request="pending")
        self.ready()
        self.engine.tick()
        self.engine.tick()
        self.command("start", request="pending")
        self.assertEqual(len(self.worker.start_requests), 1)

    def test_invalid_protocol_stops_active_engine(self):
        self.command("discover")
        self.engine.handle({"v": 2, "session": "session-one", "request": "x",
                            "command": "start", "payload": {}})
        self.assertTrue(self.engine.transport_lost)
        self.assertEqual(self.worker.shutdown_calls, 1)

    def test_old_process_lock_rejection_starts_no_worker(self):
        def occupied():
            raise OSError("MouseLink is running")
        self.engine.lock_factory = occupied
        request = self.command("start")
        self.assertFalse(self.response(request)["ok"])
        self.assertEqual(self.worker.started, 0)

    def test_settings_keep_unknown_and_calibration_keys(self):
        self.engine.settings["unknown_legacy_field"] = {"retain": 42}
        self.engine.settings["calibration"] = {"device_serial": "saved", "portrait": True}
        request = self.command("configure", {"mode": "locked", "speed": 1.2, "ipad_side": "left",
                                            "hotkey_return_enabled": True})
        self.assertTrue(self.response(request)["ok"])
        value = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(value["speed"], 120)
        self.assertEqual(value["unknown_legacy_field"], {"retain": 42})
        self.assertEqual(value["calibration"]["device_serial"], "saved")
        self.assertTrue(value["hotkey_return_enabled"])

    def test_side_change_keeps_existing_worker_safety_path(self):
        self.command("discover")
        self.worker.running_bridge = True
        self.command("configure", {"ipad_side": "left", "speed": 1.1})
        self.assertEqual(self.worker.side_requests, [("edge", 1.1, "left")])

    def test_corrupt_settings_are_preserved_before_defaults(self):
        path = self.root / "broken.json"
        path.write_text("broken JSON", encoding="utf-8")
        value, warning = read_settings(path)
        self.assertEqual(value["speed"], 50)
        self.assertTrue(warning)
        self.assertEqual(next(self.root.glob("settings.corrupt-*.json")).read_text(), "broken JSON")

    def test_invalid_speed_does_not_write_settings(self):
        for speed in (float("nan"), float("inf"), -1, 2, True, "bad"):
            request = self.command("configure", {"speed": speed})
            self.assertFalse(self.response(request)["ok"])
        self.assertFalse(self.engine.settings_path.exists())

    def test_requested_port_mismatch_never_starts_input(self):
        request = self.command("start", {"port": "COM5"})
        self.ready()
        self.engine.tick()
        self.assertFalse(self.response(request)["ok"])
        self.assertEqual(self.worker.start_requests, [])

    def test_calibration_success_requires_human_confirmation(self):
        self.command("discover")
        self.ready()
        request = self.command("calibrate", {"orientation": "portrait"})
        self.assertTrue(self.response(request)["payload"]["accepted"])
        self.worker.calibration_done.emit("portrait", True, "")
        self.assertEqual(self.engine.settings["calibration"], {})
        self.assertTrue(self.messages[-1]["payload"]["confirmation_required"])
        confirm = self.command("calibrate", {"orientation": "portrait", "confirmed": True})
        self.assertTrue(self.response(confirm)["ok"])
        self.assertEqual(self.engine.settings["calibration"],
                         {"device_serial": "board-one", "portrait": True})

    def test_calibration_confirmation_cannot_be_reused_for_other_board(self):
        self.command("discover")
        self.ready()
        self.command("calibrate", {"orientation": "portrait"})
        self.worker.calibration_done.emit("portrait", True, "")
        self.worker.device_serial = "different-board"
        confirm = self.command("calibrate", {"orientation": "portrait", "confirmed": True})
        self.assertFalse(self.response(confirm)["ok"])
        self.assertEqual(self.engine.settings["calibration"], {})

    def test_calibration_uses_current_ui_placement_and_settings(self):
        self.command("discover")
        self.ready()
        request = self.command("calibrate", {"orientation": "landscape", "ipad_side": "left",
                    "speed": .75, "mode": "free", "hotkey_return_enabled": True})
        self.assertTrue(self.response(request)["ok"])
        self.assertEqual(self.worker.calibration_requests, [("landscape", "left")])
        settings = json.loads(self.engine.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["ipad_side"], "left")
        self.assertEqual(settings["speed"], 75)
        self.assertTrue(settings["hotkey_return_enabled"])

    def test_calibration_invalid_configuration_never_moves_pointer(self):
        self.command("discover")
        self.ready()
        request = self.command("calibrate", {"orientation": "landscape", "ipad_side": "invalid"})
        self.assertFalse(self.response(request)["ok"])
        self.assertEqual(self.worker.calibration_requests, [])

    def test_calibration_configuration_save_failure_never_moves_pointer(self):
        self.command("discover")
        self.ready()
        with patch.object(self.engine, "_save", side_effect=OSError("disk full")):
            request = self.command("calibrate", {"orientation": "landscape", "ipad_side": "left"})
        self.assertFalse(self.response(request)["ok"])
        self.assertEqual(self.worker.calibration_requests, [])


class JsonLinesTests(unittest.TestCase):
    def test_split_utf8_frames_and_multiple_messages(self):
        decoder = JsonLines()
        message = {"message": "开发板"}
        raw = encode_message(message)
        self.assertEqual(decoder.feed(raw[:15]), [])
        self.assertEqual(decoder.feed(raw[15:] + raw), [message, message])

    def test_rejects_non_object_invalid_utf8_and_large_frame(self):
        for raw in (b"[]\n", b"\xff\n", b"\n", b"{" + b"x" * MAX_FRAME_BYTES):
            with self.assertRaises(ProtocolError):
                JsonLines().feed(raw)


@unittest.skipUnless(os.name == "nt", "Windows named-pipe tests")
class WindowsPipeTests(unittest.TestCase):
    """Only local synthetic pipes/mutexes; never enumerate or connect devices."""
    def setUp(self):
        self.kernel = kernel_api()
        self.kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        self.name = "\\\\.\\pipe\\iPadHub-test-" + uuid.uuid4().hex
        self.server = self.kernel.CreateNamedPipeW(self.name, 3, 1, 1, 65536, 65536, 0, None)
        self.assertNotEqual(self.server, ctypes.c_void_p(-1).value)
        self.addCleanup(self.kernel.CloseHandle, self.server)

    def test_actual_nonblocking_pipe_roundtrip_and_disconnect(self):
        client = NamedPipeClient(self.name, os.getpid())
        self.addCleanup(client.close)
        client.connect(.5)
        message = {"session": "test", "message": "中文"}
        client.send(message)
        buffer = ctypes.create_string_buffer(65536)
        count = wintypes.DWORD()
        self.assertTrue(self.kernel.ReadFile(self.server, buffer, 65536, ctypes.byref(count), None))
        self.assertEqual(JsonLines().feed(buffer.raw[:count.value]), [message])
        raw = encode_message({"command": "status"})
        self.assertTrue(self.kernel.WriteFile(self.server, raw, len(raw), ctypes.byref(count), None))
        self.assertEqual(client.poll(), [{"command": "status"}])
        self.assertEqual(client.poll(), [])

    def test_pipe_does_not_block_when_host_is_not_reading(self):
        client = NamedPipeClient(self.name, os.getpid())
        self.addCleanup(client.close)
        client.connect(.5)
        start = time.monotonic()
        with self.assertRaises(BrokenPipeError):
            for _ in range(100):
                client.send({"message": "x" * 50000})
        self.assertLess(time.monotonic() - start, 2)

    def test_actual_legacy_resource_mutex_rejects_without_closing_other_handle(self):
        # Creating a mutex does not touch serial hardware or old applications.
        existing = self.kernel.CreateMutexW(None, False, "Local\\MouseLink.KVM.Desktop")
        self.addCleanup(self.kernel.CloseHandle, existing)
        with self.assertRaises(OSError):
            BridgeResourceLock()

    def test_headless_process_hello_status_shutdown_without_worker(self):
        # Only status/shutdown: production Worker is imported, never constructed.
        with tempfile.TemporaryDirectory() as temporary:
            session = str(uuid.uuid4())
            process = subprocess.Popen([sys.executable, "-m", "ipadhub.engines.bridge",
                "--pipe", self.name, "--session", session, "--parent-pid", str(os.getpid()),
                "--data-dir", temporary], cwd=Path(__file__).resolve().parents[1],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            messages, decoder = [], JsonLines()
            def read_until(predicate):
                deadline = time.monotonic() + 8
                while not predicate() and time.monotonic() < deadline:
                    available = wintypes.DWORD()
                    if self.kernel.PeekNamedPipe(self.server, None, 0, None,
                                                  ctypes.byref(available), None) and available.value:
                        buffer = ctypes.create_string_buffer(available.value)
                        count = wintypes.DWORD()
                        self.assertTrue(self.kernel.ReadFile(self.server, buffer, available.value,
                                                            ctypes.byref(count), None))
                        messages.extend(decoder.feed(buffer.raw[:count.value]))
                    else:
                        time.sleep(.02)
                self.assertTrue(predicate(), Path(temporary, "bridge.log").read_text(encoding="utf-8")
                                if Path(temporary, "bridge.log").exists() else "No engine log")
            try:
                read_until(lambda: any(m.get("event") == "hello" for m in messages))
                for request, command in (("status", "status"), ("bye", "shutdown")):
                    raw = encode_message({"v": 1, "session": session, "request": request,
                                          "command": command, "payload": {}})
                    count = wintypes.DWORD()
                    self.assertTrue(self.kernel.WriteFile(self.server, raw, len(raw),
                                                         ctypes.byref(count), None))
                read_until(lambda: any(m.get("event") == "released" for m in messages))
                self.assertEqual(process.wait(timeout=3), 0)
                status = next(m for m in messages if m.get("request") == "status")
                self.assertEqual(status["payload"]["state"], "idle")
                self.assertFalse(status["payload"]["running"])
                self.assertTrue(next(m for m in messages if m.get("request") == "bye")["ok"])
                self.assertTrue(next(m for m in messages if m.get("event") == "released")["payload"]["clean"])
                self.assertFalse(Path(temporary, "settings.json").exists())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
