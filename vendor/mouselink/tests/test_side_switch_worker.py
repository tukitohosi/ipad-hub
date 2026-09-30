"""Concurrent lifecycle checks with fake serial owners; never opens USB or hooks."""
from contextlib import ExitStack
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
import desktop_app as ui


class ManualTimer:
    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class FakeSerial:
    def __init__(self, harness, name):
        self.harness = harness
        self.name = name
        self.is_connected = False
        self.ble_connected = True

    @property
    def absolute_ready(self):
        if self.name == "probe":
            self.harness.probe_polled.set()
            return self.harness.probe_ready.is_set()
        return True

    def connect(self):
        if self.name == "probe":
            self.harness.probe_opened.set()
            if self.harness.probe_fails:
                return False
        if self.harness.owner is not None:
            raise AssertionError(f"Serial is already owned by {self.harness.owner}")
        self.harness.owner = self.name
        self.harness.events.append(f"{self.name}:connect")
        self.is_connected = True
        return True

    def disconnect(self):
        if self.is_connected:
            self.harness.events.append(f"{self.name}:disconnect")
            self.harness.owner = None
            self.is_connected = False


class FakeController:
    def __init__(self, harness, config):
        self.config = config
        self.harness = harness
        self.number = len(harness.controllers) + 1
        self._bridge = FakeSerial(harness, f"controller{self.number}")
        self._is_active = False
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.cleanup_gate = harness.first_cleanup if self.number == 1 else threading.Event()
        if self.number != 1:
            self.cleanup_gate.set()
        harness.events.append(f"controller{self.number}:construct")
        harness.controllers.append(self)

    def run(self):
        self._bridge.connect()
        self.started.set()
        try:
            if self.number == self.harness.fail_controller:
                raise RuntimeError("Simulated fresh connection failure")
            if not self.stopped.wait(3):
                raise AssertionError("Test did not stop its controller")
            if not self.cleanup_gate.wait(3):
                raise AssertionError("Test did not release its cleanup gate")
        finally:
            self.harness.events.append(f"controller{self.number}:release-input")
            self.harness.events.append(f"controller{self.number}:listener-stopped")
            self._bridge.disconnect()

    def stop(self):
        self.stopped.set()


class Harness:
    def __init__(self, trial_seconds=0):
        self.worker = ui.Worker(trial_seconds=trial_seconds)
        self.port = SimpleNamespace(device="COM99", serial_number="board-A", location="usb1",
                                    vid=0x303A, pid=0x1001)
        self.ports = [self.port]
        self.events = []
        self.controllers = []
        self.monitors = []
        self.timers = []
        self.errors = []
        self.owner = None
        self.first_cleanup = threading.Event()
        self.probe_opened = threading.Event()
        self.probe_polled = threading.Event()
        self.probe_ready = threading.Event()
        self.probe_ready.set()
        self.probe_fails = False
        self.fail_controller = None
        self.thread = None

    def serial(self, config):
        bridge = FakeSerial(self, "probe" if self.worker.switching else "monitor")
        self.monitors.append(bridge)
        return bridge

    def timer(self, seconds, callback):
        timer = ManualTimer(seconds, callback)
        self.timers.append(timer)
        return timer

    def __enter__(self):
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(ui, "comports", side_effect=lambda: list(self.ports)))
        self.patches.enter_context(patch.object(ui, "SerialBridge", side_effect=self.serial))
        self.patches.enter_context(patch.object(ui, "KVMController",
                                              side_effect=lambda config: FakeController(self, config)))
        self.patches.enter_context(patch.object(ui.threading, "Timer", side_effect=self.timer))
        return self

    def start(self, side="right", request_token=None, enqueue=True):
        self.worker.stop_requested = False
        if enqueue:
            self.worker.commands.put(("start", ("mixed", .5, side, request_token)))

        def run():
            try:
                self.worker._idle()
            except BaseException as exc:
                self.errors.append(exc)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def controller(self, index):
        # Event.wait releases the GIL; no timing-dependent sleep or real device.
        deadline = ui.time.monotonic() + 2
        while len(self.controllers) <= index and ui.time.monotonic() < deadline:
            threading.Event().wait(.005)
        if len(self.controllers) <= index or not self.controllers[index].started.wait(2):
            raise AssertionError(f"Controller {index + 1} did not start: {self.errors}")
        return self.controllers[index]

    def finish(self):
        if self.thread:
            self.thread.join(2)
            if self.thread.is_alive():
                raise AssertionError("Worker failed to finish")
        if self.errors:
            raise self.errors[0]

    def __exit__(self, *exc):
        self.worker.request_shutdown()
        self.first_cleanup.set()
        if self.thread:
            self.thread.join(4)
        self.patches.close()


class SideSwitchWorkerTests(unittest.TestCase):
    def test_cleanup_precedes_new_owner_latest_choice_wins_and_starts_local(self):
        with Harness() as h:
            h.start()
            first = h.controller(0)
            h.worker.request_side_change("mixed", .6, "left")
            self.assertTrue(first.stopped.wait(1))
            h.worker.request_side_change("mixed", .7, "right")
            h.worker.request_side_change("edge", .9, "left")
            self.assertTrue(h.worker.switching)
            self.assertEqual(len(h.controllers), 1)
            h.first_cleanup.set()
            second = h.controller(1)
            self.assertFalse(first.config.require_ready_on_start)
            self.assertTrue(second.config.require_ready_on_start)
            self.assertEqual((second.config.mode, second.config.sensitivity, second.config.ipad_side),
                             ("edge", .9, "left"))
            self.assertFalse(second._is_active)
            self.assertFalse(h.worker.snapshot()["remote"])
            self.assertFalse(h.worker.switching)
            for earlier, later in (("controller1:release-input", "controller1:disconnect"),
                                   ("controller1:listener-stopped", "controller1:disconnect"),
                                   ("controller1:disconnect", "probe:connect"),
                                   ("probe:disconnect", "controller2:construct")):
                self.assertLess(h.events.index(earlier), h.events.index(later))
            h.worker.stop_bridge()
            h.finish()
            self.assertIsNone(h.owner)

    def test_stop_or_shutdown_during_cleanup_cancels_pending_switch(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown), Harness() as h:
                h.start()
                h.controller(0)
                h.worker.request_side_change("mixed", .5, "left")
                (h.worker.request_shutdown if shutdown else h.worker.stop_bridge)()
                h.worker.request_side_change("mixed", .5, "right")
                h.first_cleanup.set()
                h.finish()
                self.assertEqual(len(h.controllers), 1)
                self.assertFalse(h.worker.switching)
                self.assertIsNone(h.worker._pending_config)
                self.assertIsNone(h.owner)

    def test_stop_or_shutdown_cancels_readiness_wait_without_delayed_restart(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown), Harness() as h:
                h.probe_ready.clear()
                h.start()
                h.controller(0)
                h.worker.request_side_change("mixed", .5, "left")
                h.first_cleanup.set()
                self.assertTrue(h.probe_polled.wait(1))
                (h.worker.request_shutdown if shutdown else h.worker.stop_bridge)()
                h.thread.join(.5)
                self.assertFalse(h.thread.is_alive())
                h.probe_ready.set()
                h.finish()
                self.assertEqual(len(h.controllers), 1)
                self.assertIsNone(h.owner)

    def test_latest_choice_during_readiness_wait_is_applied(self):
        with Harness() as h:
            h.probe_ready.clear()
            h.start()
            h.controller(0)
            h.worker.request_side_change("mixed", .5, "left")
            h.first_cleanup.set()
            self.assertTrue(h.probe_polled.wait(1))
            h.worker.request_side_change("edge", .8, "right")
            h.probe_ready.set()
            second = h.controller(1)
            self.assertEqual((second.config.ipad_side, second.config.mode, second.config.sensitivity),
                             ("right", "edge", .8))
            h.worker.stop_bridge()
            h.finish()

    def test_failed_or_changed_device_never_rebuilds_or_leaves_pending_restart(self):
        for failure in ("connect", "missing", "changed", "multiple", "disconnected", "timeout"):
            with self.subTest(failure=failure), Harness() as h:
                h.start()
                h.controller(0)
                h.worker.request_side_change("mixed", .5, "left")
                if failure == "connect":
                    h.probe_fails = True
                elif failure == "missing":
                    h.ports.clear()
                elif failure == "changed":
                    h.port.serial_number = "board-B"
                elif failure == "multiple":
                    h.ports.append(h.port)
                elif failure in ("disconnected", "timeout"):
                    h.probe_ready.clear()
                h.first_cleanup.set()
                if failure == "disconnected":
                    self.assertTrue(h.probe_polled.wait(1))
                    h.monitors[-1].is_connected = False
                    h.owner = None
                h.finish()
                h.probe_ready.set()
                self.assertEqual(len(h.controllers), 1)
                self.assertFalse(h.worker.running_bridge)
                self.assertFalse(h.worker.switching)
                self.assertIsNone(h.worker._pending_config)
                self.assertIsNone(h.worker._session_token)
                self.assertIn("重新开启", h.worker.error)

    def test_trial_timer_spans_switches_and_expires_current_controller(self):
        with Harness(trial_seconds=180) as h:
            h.start()
            h.controller(0)
            timer = h.timers[0]
            h.worker.request_side_change("mixed", .5, "left")
            h.first_cleanup.set()
            second = h.controller(1)
            self.assertEqual(len(h.timers), 1)
            self.assertEqual(timer.seconds, 180)
            self.assertTrue(timer.started)
            self.assertFalse(timer.cancelled)
            timer.callback()
            self.assertTrue(second.stopped.wait(1))
            h.finish()
            self.assertTrue(timer.cancelled)
            self.assertTrue(h.worker.stop_requested)

    def test_new_controller_failure_does_not_keep_pending_resume_or_serial_owner(self):
        with Harness(trial_seconds=180) as h:
            h.fail_controller = 2
            h.start()
            h.controller(0)
            h.worker.request_side_change("mixed", .5, "left")
            h.first_cleanup.set()
            with self.assertRaisesRegex(RuntimeError, "位置已保存，请重新开启"):
                h.finish()
            self.assertEqual(len(h.controllers), 2)
            self.assertIsNone(h.owner)
            self.assertIsNone(h.worker.controller)
            self.assertIsNone(h.worker._session_token)
            self.assertIsNone(h.worker._pending_config)
            self.assertFalse(h.worker.running_bridge)
            self.assertFalse(h.worker.switching)
            self.assertTrue(h.timers[0].cancelled)

    def test_trial_expiry_during_switch_cancels_rebuild(self):
        with Harness(trial_seconds=180) as h:
            h.probe_ready.clear()
            h.start()
            h.controller(0)
            h.worker.request_side_change("mixed", .5, "left")
            h.first_cleanup.set()
            self.assertTrue(h.probe_polled.wait(1))
            h.timers[0].callback()
            h.finish()
            self.assertEqual(len(h.controllers), 1)
            self.assertIn("时限", h.worker.error)

    def test_old_timer_callback_cannot_stop_a_new_user_session(self):
        with Harness(trial_seconds=180) as h:
            h.start()
            h.controller(0)
            old_timer = h.timers[0]
            h.worker.stop_bridge()
            h.first_cleanup.set()
            h.finish()
            h.start("left")
            second = h.controller(1)
            old_timer.callback()  # A cancelled Timer may already be executing.
            self.assertFalse(second.stopped.is_set())
            self.assertTrue(h.worker.running_bridge)
            self.assertFalse(h.worker.stop_requested)
            self.assertEqual(len(h.timers), 2)
            h.worker.stop_bridge()
            h.finish()

    def test_idle_side_request_does_not_arm_a_future_start(self):
        worker = ui.Worker()
        worker.request_side_change("mixed", .5, "left")
        self.assertFalse(worker.switching)
        self.assertIsNone(worker._pending_config)

    def test_missing_multiple_failed_or_dropped_device_cancels_queued_actions(self):
        for failure in ("missing", "multiple", "connect", "dropped"):
            with self.subTest(failure=failure):
                worker = ui.Worker()
                worker.shutdown.set()  # Do not spend time on the idle retry delay.
                request = object()
                starts, checks = [], []
                worker.start_handled.connect(starts.append, ui.QtCore.Qt.ConnectionType.DirectConnection)
                worker.calibration_done.connect(lambda *args: checks.append(args),
                                                ui.QtCore.Qt.ConnectionType.DirectConnection)
                worker.commands.put(("start", ("mixed", .5, "left", request)))
                worker.commands.put(("calibrate", ("portrait", "left")))
                worker.busy = True
                port = SimpleNamespace(device="COM99", serial_number="A", vid=0x303A, pid=0x1001)
                ports = [] if failure == "missing" else [port, port] if failure == "multiple" else [port]
                bridge = Mock(is_connected=False, absolute_ready=False)
                bridge.connect.return_value = failure != "connect"
                with patch.object(ui, "comports", return_value=ports), \
                     patch.object(ui, "SerialBridge", return_value=bridge), \
                     patch.object(ui, "KVMController") as constructor:
                    worker._idle()
                    constructor.assert_not_called()
                self.assertEqual(starts, [request])
                self.assertEqual(len(checks), 1)
                self.assertEqual(checks[0][:2], ("portrait", False))
                self.assertTrue(checks[0][2])
                self.assertFalse(worker.busy)
                self.assertTrue(worker.commands.empty())

    def test_disconnected_request_is_rejected_without_waiting_for_reconnection(self):
        worker = ui.Worker()
        request = object()
        starts, checks = [], []
        worker.start_handled.connect(starts.append, ui.QtCore.Qt.ConnectionType.DirectConnection)
        worker.calibration_done.connect(lambda *args: checks.append(args),
                                        ui.QtCore.Qt.ConnectionType.DirectConnection)
        self.assertFalse(worker.request_start("mixed", .5, "left", request))
        self.assertFalse(worker.request_calibration("portrait", "left"))
        self.assertEqual(starts, [request])
        self.assertEqual(checks[0][:2], ("portrait", False))
        self.assertTrue(worker.commands.empty())
        self.assertFalse(worker.busy)

    def test_stop_cancels_only_existing_queue_and_preserves_subsequent_new_request(self):
        worker = ui.Worker()
        worker.monitor = Mock(is_connected=True, absolute_ready=True)
        old_request, new_request = object(), object()
        notices = []

        def notified(request):
            notices.append(request)
            if request is old_request:
                worker.request_start("mixed", .8, "left", new_request)

        worker.start_handled.connect(notified, ui.QtCore.Qt.ConnectionType.DirectConnection)
        self.assertTrue(worker.request_start("mixed", .5, "right", old_request))
        worker.stop_bridge()
        self.assertEqual(notices, [old_request])
        self.assertFalse(worker.stop_requested)
        self.assertEqual(worker.commands.get_nowait(), ("start", ("mixed", .8, "left", new_request)))
        self.assertTrue(worker.commands.empty())

    def test_stop_then_new_start_cannot_revive_an_already_dequeued_old_request(self):
        with Harness() as h:
            disconnecting = threading.Event()
            allow_disconnect = threading.Event()
            original_disconnect = FakeSerial.disconnect
            old_request, new_request = object(), object()
            notices = []
            h.worker.start_handled.connect(notices.append, ui.QtCore.Qt.ConnectionType.DirectConnection)

            def disconnect(bridge):
                if bridge.name == "monitor" and not disconnecting.is_set():
                    disconnecting.set()
                    if not allow_disconnect.wait(2):
                        raise AssertionError("Test did not release initial serial disconnect")
                return original_disconnect(bridge)

            with patch.object(FakeSerial, "disconnect", disconnect):
                h.start(request_token=old_request)
                self.assertTrue(disconnecting.wait(1))
                h.worker.stop_bridge()
                self.assertTrue(h.worker.request_start("mixed", .8, "left", new_request))
                allow_disconnect.set()
                h.finish()
            self.assertEqual(len(h.controllers), 0)
            self.assertEqual(notices, [old_request])
            h.start(enqueue=False)
            controller = h.controller(0)
            self.assertEqual(controller.config.ipad_side, "left")
            self.assertEqual(controller.config.sensitivity, .8)
            h.worker.stop_bridge()
            h.first_cleanup.set()
            h.finish()
            self.assertEqual(notices, [old_request, new_request, new_request])

    def test_start_notifications_keep_their_original_request_token_across_switches(self):
        with Harness() as h:
            request_token = object()
            notices = []
            h.worker.start_handled.connect(notices.append, ui.QtCore.Qt.ConnectionType.DirectConnection)
            h.start(request_token=request_token)
            h.controller(0)
            h.worker.request_side_change("mixed", .5, "left")
            h.first_cleanup.set()
            h.controller(1)
            h.worker.stop_bridge()
            h.finish()
            self.assertEqual(len(notices), 3)
            self.assertTrue(all(token is request_token for token in notices))

    def test_legacy_and_cancelled_start_notifications_have_matching_request_token(self):
        for request_token in (None, object()):
            with self.subTest(request_token=request_token), Harness() as h:
                notices = []
                h.worker.start_handled.connect(notices.append, ui.QtCore.Qt.ConnectionType.DirectConnection)
                h.worker.stop_bridge()
                value = ("mixed", .5) if request_token is None else ("mixed", .5, "left", request_token)
                h.worker._run_session(value, h.worker._board_identity(h.port))
                self.assertEqual(len(notices), 1)
                self.assertIs(notices[0], request_token)

    def test_cancelled_start_does_not_construct_a_controller(self):
        with Harness() as h:
            h.worker.stop_bridge()
            with patch.object(ui, "KVMController") as controller:
                h.worker._run_session(("mixed", .5, "left"), h.worker._board_identity(h.port))
                controller.assert_not_called()

    def test_calibration_mirrors_return_edge_and_preserves_center_and_orientation(self):
        for side in ("left", "right"):
            with self.subTest(side=side):
                worker = ui.Worker()
                bridge = Mock(is_connected=True, absolute_ready=True)
                sent = []

                def send(buttons, x, y, wheel, sequence):
                    sent.append((x, y, worker.phase))
                    if sequence == 7:
                        bridge.is_connected = False
                    return True

                bridge.send_absolute_report.side_effect = send
                results = []
                worker.calibration_done.connect(lambda *args: results.append(args),
                                                ui.QtCore.Qt.ConnectionType.DirectConnection)
                worker.commands.put(("calibrate", ("portrait", side)))
                port = SimpleNamespace(device="COM99", serial_number="board-A", vid=0x303A, pid=0x1001)
                tick = iter(range(100))
                with patch.object(ui, "comports", return_value=[port]), \
                     patch.object(ui, "SerialBridge", return_value=bridge), \
                     patch.object(ui.time, "monotonic", side_effect=lambda: next(tick) * 3):
                    worker._idle()
                expected = [16384, 29490, 32767, 3277, 16384, 16384, 16384] if side == "left" else [
                    x for _, x, _ in ui.POINTS]
                self.assertEqual([x for x, _, _ in sent], expected)
                self.assertIn("最右边缘" if side == "left" else "最左边缘", sent[2][2])
                self.assertEqual(results, [("portrait", True, "")])
                bridge.send_mouse_report.assert_called_once_with(0, 0, 0, 0)


if __name__ == "__main__":
    unittest.main()
