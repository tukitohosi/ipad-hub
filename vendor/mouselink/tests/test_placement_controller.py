"""Placement integration tests; every Windows input and capture API is mocked."""
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from absolute_protocol import Status
from bridge import BridgeConfig, CursorManager, KVMController
from pointer_boundary import AbsolutePointer


class PlacementControllerTests(unittest.TestCase):
    def windows(self, x, y=400, left=-1920, top=-1080, width=4480, height=2520):
        api = Mock()
        api.GetSystemMetrics.side_effect = {76: left, 77: top, 78: width, 79: height}.__getitem__
        api.GetAsyncKeyState.return_value = 0

        def cursor(pointer):
            pointer._obj.x, pointer._obj.y = x, y
            return True

        api.GetCursorPos.side_effect = cursor
        return api

    def test_missing_or_invalid_side_preserves_right_default(self):
        self.assertEqual(BridgeConfig().ipad_side, "right")
        for side in (None, "up", "", 0):
            self.assertEqual(BridgeConfig(ipad_side=side).ipad_side, "right")

    def test_remote_entry_mirrors_x_and_maps_virtual_desktop_height(self):
        for side, x, return_edge in [("left", 32112, "right"), ("right", 655, "left")]:
            for y in (-2000, -1080, 180, 1439, 2000):
                with self.subTest(side=side, y=y):
                    c = KVMController(BridgeConfig(ipad_side=side, absolute_enabled=True, mode="mixed"))
                    c._bridge = Mock()
                    c._cursor = Mock()
                    c._cursor.locked_context.return_value = nullcontext()
                    c._is_running = False  # Enter and release without polling actual hardware.
                    entered = []
                    expected_y = round(max(0, min(32767, (y + 1080) / 2519 * 32767)))
                    with patch("bridge._user32", self.windows(-1920, y)), \
                         patch("bridge.RawMouseCapture"), \
                         patch("bridge.keyboard.Listener"), \
                         patch("bridge.mouse.Listener"), \
                         patch.object(c, "_send_mouse_report", side_effect=lambda *args:
                                      entered.append((c._pointer.report_position, c._pointer.return_edge))), \
                         patch.object(c, "_release_all_remote_inputs"):
                        c._enter_remote_mode()
                    self.assertEqual(entered, [((x, expected_y), return_edge)])
                    self.assertFalse(c._is_active)
                    self.assertIsNone(c._pointer)

    def test_return_cursor_moves_inward_from_selected_virtual_desktop_edge(self):
        for side, x, expected in [("left", -1920, -1760), ("right", 2559, 2399),
                                  ("left", -100, -100), ("right", -100, -100)]:
            with self.subTest(side=side, x=x):
                c = KVMController(BridgeConfig(ipad_side=side))
                api = self.windows(x)
                with patch("bridge._user32", api):
                    c._park_cursor_inside_windows()
                api.SetCursorPos.assert_called_once_with(expected, 400)

    def test_return_cursor_inset_clamps_to_a_narrow_desktop(self):
        for side, expected in [("left", -1821), ("right", -1920)]:
            with self.subTest(side=side):
                c = KVMController(BridgeConfig(ipad_side=side))
                api = self.windows(-1900, width=100)
                with patch("bridge._user32", api):
                    c._park_cursor_inside_windows()
                api.SetCursorPos.assert_called_once_with(expected, 400)

    def activation(self, side, positions, held=False, cooldown=0):
        c = KVMController(BridgeConfig(ipad_side=side))
        c._bridge = Mock(is_connected=True)
        c._last_remote_exit_at = cooldown
        api = self.windows(0)
        api.GetAsyncKeyState.return_value = 0x8000 if held else 0
        samples = iter(positions)

        def cursor(pointer):
            try:
                pointer._obj.x, pointer._obj.y = next(samples), 400
            except StopIteration:
                c._is_running = False
                return False
            return True

        api.GetCursorPos.side_effect = cursor
        moments = iter(1 + i * .1 for i in range(100))
        with patch("bridge._user32", api), \
             patch("bridge.keyboard.Listener") as listener, \
             patch("bridge.time.sleep"), \
             patch("bridge.time.monotonic", side_effect=lambda: next(moments)):
            activated = c._wait_for_activation()
        listener.return_value.stop.assert_called_once()
        listener.return_value.join.assert_called_once()
        return activated

    def test_only_selected_edge_activates_after_dwell_and_reentry_is_armed(self):
        for side, chosen, other in [("left", -1920, 2559), ("right", 2559, -1920)]:
            with self.subTest(side=side):
                self.assertTrue(self.activation(side, [0, chosen, chosen, chosen, chosen]))
                self.assertFalse(self.activation(side, [0, other, other, other, other]))
                self.assertFalse(self.activation(side, [chosen] * 6))
                self.assertFalse(self.activation(side, [0, chosen, 0, chosen, 0]))

    def test_drag_and_recent_exit_prevent_both_edge_activations(self):
        for side, chosen in [("left", -1920), ("right", 2559)]:
            with self.subTest(side=side):
                self.assertFalse(self.activation(side, [0] + [chosen] * 5, held=True))
                self.assertFalse(self.activation(side, [0] + [chosen] * 5, cooldown=1))

    def test_left_placement_preserves_mouse_direction_and_return_reason(self):
        c = KVMController(BridgeConfig(ipad_side="left", absolute_enabled=True, mode="mixed"))
        c._is_active = True
        c._bridge = Mock(status=Status())
        c._pointer = AbsolutePointer(x=32112, return_edge="right")
        with patch("bridge.time.monotonic", return_value=2):
            c._handle_raw_mouse_move(-5, 3)
        self.assertEqual(c._pointer.report_position, (32032, 16432))
        c._pointer.x = 32767
        c._pointer.sent(32767, 16432, 2, True)
        with patch("bridge.time.monotonic", return_value=3):
            c._handle_raw_mouse_move(2, 0)
        self.assertTrue(c._exit_requested)
        self.assertEqual(c._exit_reason, "iPad right edge")

    def test_initial_connect_and_status_failures_always_close_serial_and_unlock(self):
        for failing_call in ("connect", "wait_for_ble_status"):
            with self.subTest(failing_call=failing_call):
                c = KVMController(BridgeConfig())
                c._bridge = Mock()
                c._cursor = Mock()
                getattr(c._bridge, failing_call).side_effect = RuntimeError("failed")
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    c.run()
                c._bridge.disconnect.assert_called_once()
                c._cursor.unlock.assert_called_once()

    def test_switch_start_requires_fresh_ready_channel_and_times_out(self):
        c = KVMController(BridgeConfig(require_ready_on_start=True))
        c._bridge = Mock(is_connected=True, absolute_ready=False)
        c._cursor = Mock()
        with patch("bridge.time.monotonic", side_effect=[0, .5, 1, 1.5]), \
             patch("bridge.time.sleep") as sleep, \
             patch.object(c, "_wait_for_activation") as activate:
            with self.assertRaisesRegex(RuntimeError, "not ready"):
                c.run()
        activate.assert_not_called()
        c._bridge.wait_for_ble_status.assert_not_called()
        c._bridge.disconnect.assert_called_once()
        c._cursor.unlock.assert_called_once()
        self.assertEqual(sleep.call_count, 2)

    def test_switch_start_readiness_wait_is_cancellable(self):
        c = KVMController(BridgeConfig(require_ready_on_start=True))
        c._bridge = Mock(is_connected=True, absolute_ready=False)
        c._cursor = Mock()
        with patch("bridge.time.sleep", side_effect=lambda _: c.stop()), \
             patch.object(c, "_wait_for_activation") as activate:
            c.run()
        activate.assert_not_called()
        c._bridge.disconnect.assert_called_once()
        c._cursor.unlock.assert_called_once()

    def test_failed_switch_connection_does_not_wait_for_future_device(self):
        c = KVMController(BridgeConfig(require_ready_on_start=True))
        c._bridge = Mock()
        c._bridge.connect.return_value = False
        c._cursor = Mock()
        with self.assertRaisesRegex(RuntimeError, "Cannot reconnect"):
            c.run()
        c._bridge.connect.assert_called_once()
        c._bridge.wait_for_ble_status.assert_not_called()
        c._bridge.disconnect.assert_called_once()

    def test_ready_switch_enters_local_standby_without_capturing_input(self):
        c = KVMController(BridgeConfig(require_ready_on_start=True))
        c._bridge = Mock(is_connected=True, absolute_ready=True)
        c._cursor = Mock()
        with patch.object(c, "_wait_for_activation", side_effect=c.stop) as activate, \
             patch.object(c, "_enter_remote_mode") as remote:
            c.run()
        activate.assert_called_once()
        remote.assert_not_called()
        c._bridge.disconnect.assert_called_once()

    def test_switch_standby_channel_loss_cannot_wait_for_later_reconnection(self):
        c = KVMController(BridgeConfig(require_ready_on_start=True))
        c._bridge = Mock(is_connected=True, absolute_ready=False)
        with patch("bridge._user32", self.windows(0)), \
             patch("bridge.keyboard.Listener") as listener:
            with self.assertRaisesRegex(RuntimeError, "channel disconnected"):
                c._wait_for_activation()
        listener.return_value.stop.assert_called_once()
        listener.return_value.join.assert_called_once()


class CursorLifecycleTests(unittest.TestCase):
    def windows(self):
        api = Mock()

        def cursor(pointer):
            pointer._obj.x, pointer._obj.y = -1800, 400
            return True

        api.GetCursorPos.side_effect = cursor
        return api

    def test_unlocked_cleanup_always_releases_clip_without_changing_visibility(self):
        api = self.windows()
        with patch("bridge._user32", api):
            cursor = CursorManager()
            cursor.unlock()
            cursor.unlock()
        self.assertEqual(api.ClipCursor.call_args_list, [call(None), call(None)])
        api.ShowCursor.assert_not_called()
        api.SetCursorPos.assert_not_called()

    def test_repeated_lock_and_unlock_balance_visibility_and_restore_position_once(self):
        api = self.windows()
        with patch("bridge._user32", api):
            cursor = CursorManager()
            cursor.lock()
            cursor.lock()
            cursor.unlock()
            cursor.unlock()
        api.GetCursorPos.assert_called_once()
        self.assertEqual(api.ShowCursor.call_args_list, [call(False), call(True)])
        api.SetCursorPos.assert_called_once_with(-1800, 400)
        self.assertEqual(api.ClipCursor.call_count, 3)
        self.assertEqual(api.ClipCursor.call_args_list[-2:], [call(None), call(None)])

    def test_remote_exception_then_session_cleanup_does_not_increment_visibility_twice(self):
        api = self.windows()
        with patch("bridge._user32", api):
            cursor = CursorManager()
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                with cursor.locked_context():
                    raise RuntimeError("capture failed")
            cursor.unlock()  # KVMController.run performs a second cleanup.
        self.assertEqual(api.ShowCursor.call_args_list, [call(False), call(True)])
        api.SetCursorPos.assert_called_once_with(-1800, 400)
        self.assertEqual(api.ClipCursor.call_args_list[-2:], [call(None), call(None)])

    def test_repeated_stopped_local_sessions_do_not_increment_cursor_visibility(self):
        api = self.windows()
        with patch("bridge._user32", api):
            for _ in range(3):
                controller = KVMController(BridgeConfig())
                controller._bridge = Mock()
                controller.stop()
                controller.run()
                controller._bridge.connect.assert_not_called()
                controller._bridge.disconnect.assert_called_once()
        api.ShowCursor.assert_not_called()
        self.assertEqual(api.ClipCursor.call_args_list, [call(None)] * 3)

    def test_missing_cursor_position_still_pairs_hide_with_show(self):
        api = Mock()
        api.GetCursorPos.return_value = False
        with patch("bridge._user32", api):
            cursor = CursorManager()
            with cursor.locked_context():
                pass
            cursor.unlock()
        self.assertEqual(api.ShowCursor.call_args_list, [call(False), call(True)])
        api.SetCursorPos.assert_not_called()
        self.assertEqual(api.ClipCursor.call_args_list, [call(None), call(None)])


if __name__ == "__main__":
    unittest.main()
