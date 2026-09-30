import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from absolute_protocol import Status
from native_edges import NativeEdges
from pointer_boundary import AbsolutePointer
from bridge import BridgeConfig, KVMController, mouse


class NativeEdgeTests(unittest.TestCase):
    def native(self, x=16384, y=16384, sensitivity=0.5, return_edge="left"):
        n = NativeEdges(AbsolutePointer(x=x, y=y, return_edge=return_edge), sensitivity)
        self.transmit(n, 1, acknowledge=True)
        return n

    def transmit(self, n, at=2, acknowledge=False):
        report = n.next_report()
        if report is None:
            return None
        sequence = n.next_sequence(at) if report.channel == "absolute" else None
        n.submitted(report, sequence, at, True)
        if acknowledge and sequence is not None:
            n.acknowledge(sequence, report.x, report.y, report.buttons, True, at + .01)
        return report

    def enter_bottom(self, n, at=2):
        n.move(0, (32767 - n.pointer.y) / 16, at)
        report = self.transmit(n, at, acknowledge=True)
        self.assertEqual((report.channel, report.y), ("absolute", 32767))
        self.assertEqual(n.state, "relative")

    def test_crossing_splits_at_first_intersection_and_consumes_remainder_once(self):
        n = self.native(x=10000, y=32751)
        n.move(4, 2, 2)
        edge = self.transmit(n)
        self.assertEqual((edge.x, edge.y), (10032, 32767))
        self.assertEqual(n.state, "edge_wait")
        self.assertIsNone(n.next_report())
        seq = n._waiting_sequence
        n.acknowledge(seq, edge.x, edge.y, 0, True, 2.01)
        tail = self.transmit(n, 2.02)
        self.assertEqual((tail.channel, tail.x, tail.y), ("relative", 1, 0))
        self.assertIsNone(n.next_report())
        n.move(0, 1, 2.03)
        fractional_tail = self.transmit(n, 2.04)
        self.assertEqual((fractional_tail.x, fractional_tail.y), (0, 1))
        self.assertIsNone(n.next_report())

    def test_all_native_edges_and_diagonal_corner(self):
        for x, y, dx, dy, edges in [
            (100, 16, 0, -2, ("top",)),
            (32751, 100, 2, 0, ("right",)),
            (32751, 16, 2, -2, ("right", "top")),
        ]:
            with self.subTest(edges=edges):
                n = self.native(x, y)
                n.move(dx, dy, 2)
                self.transmit(n, acknowledge=True)
                self.assertEqual(n.edges, edges)
                self.assertEqual(n.state, "relative")

    def test_left_corner_stays_exclusive_to_confirmed_return(self):
        n = self.native(x=0, y=0)
        n.move(-2, -1, 2)
        self.assertIsNone(n.next_report())
        self.assertTrue(n.return_requested)
        self.assertEqual(n.state, "absolute")

    def test_vertical_intent_at_left_corners_precedes_confirmed_left_return(self):
        for y, dy in [(32767, 100), (0, -100), (32767, 1), (0, -1)]:
            with self.subTest(y=y, dy=dy):
                n = self.native(x=0, y=y)
                n.move(-1, dy, 2)
                edge = self.transmit(n, 2)
                self.assertFalse(n.return_requested)
                self.assertEqual((n.state, edge.barrier), ("edge_wait", "edge"))

    def test_sequence_cannot_reuse_a_recent_acknowledged_identifier(self):
        n = self.native()
        issued = set()
        for i in range(256):
            at = 2 + i / 1000
            sequence = n.next_sequence(at)
            self.assertNotIn(sequence, issued)
            issued.add(sequence)
        self.assertIsNone(n.next_sequence(2.3))
        self.assertEqual(n.failure, "native edge sequence window exhausted")
        self.assertIsNotNone(n.next_sequence(3))

    def test_recent_sequence_quarantine_can_span_controller_sessions(self):
        history = {}
        first = NativeEdges(AbsolutePointer(), recent_sequences=history)
        first_sequence = first.next_sequence(1)
        second = NativeEdges(AbsolutePointer(), recent_sequences=history)
        self.assertNotEqual(second.next_sequence(1.1), first_sequence)

    def test_relative_idle_click_release_and_wheel_never_replay_absolute(self):
        n = self.native()
        self.enter_bottom(n)
        n.move(0, 8, 2.1)
        self.assertEqual(self.transmit(n, 2.1).channel, "relative")
        self.assertIsNone(n.next_report())
        n.button(1, 2.2)
        n.scroll(3, 2.3)
        n.button(0, 2.4)
        reports = [self.transmit(n, 2.5) for _ in range(3)]
        self.assertEqual([(r.channel, r.buttons, r.wheel) for r in reports],
                         [("relative", 1, 0), ("relative", 1, 3), ("relative", 0, 0)])
        self.assertIsNone(n.next_report())
        self.assertFalse(n.acknowledgment_timed_out(20))

    def test_relative_move_before_press_and_drag_before_release_keep_their_buttons(self):
        n = self.native()
        self.enter_bottom(n)
        n.move(0, 8, 2.1)
        n.button(1, 2.2)
        n.move(0, 6, 2.3)
        n.button(0, 2.4)
        reports = [self.transmit(n, 2.5) for _ in range(4)]
        self.assertEqual([(r.buttons, r.y) for r in reports], [(0, 4), (1, 0), (1, 3), (0, 0)])

    def test_absolute_drag_cannot_switch_channels(self):
        n = self.native(y=32751)
        n.button(1, 2)
        n.move(0, 100, 2.1)
        first = self.transmit(n, 2.2)
        second = self.transmit(n, 2.3)
        self.assertEqual((first.channel, first.buttons, first.y), ("absolute", 1, 32751))
        self.assertEqual((second.channel, second.buttons, second.y), ("absolute", 1, 32767))
        self.assertEqual(n.state, "absolute")

    def test_relative_drag_defers_resync_until_fresh_inward_motion_after_release(self):
        n = self.native()
        self.enter_bottom(n)
        n.button(1, 2.1)
        n.move(0, -4, 2.2)
        n.button(0, 2.3)
        reports = [self.transmit(n, 2.4) for _ in range(3)]
        self.assertTrue(all(r.channel == "relative" for r in reports))
        self.assertEqual(n.state, "relative")
        self.assertIsNone(n.next_report())
        n.move(0, -10, 2.5)
        resync = self.transmit(n, 2.6)
        self.assertEqual((resync.channel, resync.buttons, resync.barrier), ("absolute", 0, "resync"))
        self.assertEqual(n.state, "resync_wait")

    def test_low_speed_accumulates_and_large_relative_motion_is_split_without_clipping(self):
        n = self.native(y=32767, sensitivity=.25)
        n.move(0, 1, 2)
        self.transmit(n, 2, acknowledge=True)
        self.assertIsNone(n.next_report())
        for i in range(3):
            n.move(0, 1, 2.1 + i * .1)
        self.assertEqual(self.transmit(n, 2.5).y, 1)
        n.move(0, 400000, 3)
        values = []
        while (report := self.transmit(n, 3.1)) is not None:
            values.append(report.y)
        self.assertEqual(sum(values), 100000)
        self.assertTrue(all(-32768 <= value <= 32767 for value in values))

    def test_repeated_one_count_reversals_do_not_switch_any_native_edge(self):
        for x, y, outward in [(16384, 32767, (0, 2)), (16384, 0, (0, -2)),
                              (32767, 16384, (2, 0))]:
            with self.subTest(outward=outward):
                n = self.native(x, y)
                n.move(*outward, 2)
                self.transmit(n, 2, acknowledge=True)
                self.transmit(n, 2.02)
                for i in range(20):
                    for sign in (-.5, .5):
                        n.move(outward[0] * sign, outward[1] * sign, 3 + i * .1)
                        report = self.transmit(n, 3.01 + i * .1)
                        self.assertEqual(n.state, "relative")
                        if report is not None:
                            self.assertEqual(report.channel, "relative")
                self.assertIsNone(n.next_report())

    def test_small_reverse_movement_is_delivered_then_return_to_edge_cancels_distance(self):
        n = self.native()
        self.enter_bottom(n)
        n.move(0, -2, 2.1)
        report = self.transmit(n, 2.1)
        self.assertEqual((report.channel, report.y), ("relative", -1))
        n.move(0, -9, 2.2)  # 5.5 relative counts from the edge, still below six.
        self.assertEqual(self.transmit(n, 2.2).channel, "relative")
        n.move(0, 11, 2.3)
        self.assertEqual(self.transmit(n, 2.3).channel, "relative")
        self.assertEqual(n.pointer.y, 32767)
        n.move(0, -2, 2.4)
        self.assertEqual(self.transmit(n, 2.4).channel, "relative")

    def test_slow_inward_motion_accumulates_until_six_relative_counts(self):
        n = self.native(y=32767, sensitivity=.25)
        n.move(0, 1, 2)
        self.transmit(n, 2, acknowledge=True)
        self.transmit(n, 2.02)
        for i in range(23):
            n.move(0, -1, 3 + i * .01)
            report = self.transmit(n, 3.001 + i * .01)
            self.assertEqual(n.state, "relative")
            if report is not None:
                self.assertEqual(report.channel, "relative")
        n.move(0, -1, 3.3)
        report = self.transmit(n, 3.3)
        self.assertEqual((report.channel, report.barrier, report.y),
                         ("absolute", "resync", 32767 - 192))

    def test_obvious_inward_motion_resyncs_without_a_time_delay(self):
        n = self.native()
        self.enter_bottom(n)
        n.move(0, -100, 2.1)
        report = self.transmit(n, 2.1)
        self.assertEqual((report.channel, report.barrier), ("absolute", "resync"))

    def test_corner_requires_distance_from_the_edge_currently_being_left(self):
        n = self.native(x=32767, y=0)
        n.move(2, -2, 2)
        self.transmit(n, 2, acknowledge=True)
        self.transmit(n, 2.02)
        n.button(1, 2.1)
        self.transmit(n, 2.1)
        n.move(-20, 0, 2.2)  # Drag away from the right edge, but not the top.
        self.transmit(n, 2.2)
        n.button(0, 2.3)
        self.transmit(n, 2.3)
        n.move(0, 1, 2.4)  # Existing right distance cannot authorize this top movement.
        self.assertIsNone(self.transmit(n, 2.4))
        self.assertEqual(n.state, "relative")
        n.move(-1, 0, 2.5)
        self.assertEqual(self.transmit(n, 2.5).barrier, "resync")

    def test_corner_outward_component_blocks_resync_even_after_threshold(self):
        n = self.native(x=32767, y=0)
        n.move(2, -2, 2)
        self.transmit(n, 2, acknowledge=True)
        self.transmit(n, 2.02)
        n.move(-20, -2, 2.1)
        self.assertEqual(self.transmit(n, 2.1).channel, "relative")
        self.assertEqual(n.state, "relative")

    def test_release_idle_and_wheel_do_not_resync_after_a_long_inward_drag(self):
        n = self.native()
        self.enter_bottom(n)
        n.button(1, 2.1)
        self.transmit(n, 2.1)
        n.move(0, -100, 2.2)
        self.assertEqual(self.transmit(n, 2.2).channel, "relative")
        n.button(0, 2.3)
        self.assertEqual(self.transmit(n, 2.3).channel, "relative")
        self.assertIsNone(n.next_report())
        n.scroll(1, 2.4)
        self.assertEqual(self.transmit(n, 2.4).channel, "relative")
        n.move(1, 0, 2.5)  # Tangential motion cannot authorize resync either.
        self.assertIsNone(self.transmit(n, 2.5))
        self.assertEqual(n.state, "relative")
        n.move(0, -1, 2.6)
        self.assertEqual(self.transmit(n, 2.6).barrier, "resync")

    def test_wrong_stale_and_mismatched_ack_cannot_release_barrier_or_timeout(self):
        for mutate in ("sequence", "coordinates", "buttons", "stale"):
            with self.subTest(mutate=mutate):
                n = self.native(y=32751)
                n.move(0, 2, 2)
                edge = self.transmit(n, 2)
                seq, x, y, buttons, at = n._waiting_sequence, edge.x, edge.y, 0, 2.1
                if mutate == "sequence": seq += 1
                if mutate == "coordinates": x += 1
                if mutate == "buttons": buttons = 1
                if mutate == "stale": at = 1.9
                self.assertFalse(n.acknowledge(seq, x, y, buttons, True, at))
                self.assertEqual(n.state, "edge_wait")
                self.assertTrue(n.acknowledgment_timed_out(2.81))

    def test_newer_matching_ack_progress_tolerates_parser_coalescing(self):
        n = self.native()
        n.move(1, 0, 2)
        self.transmit(n, 2)
        n.move(1, 0, 2.1)
        report = self.transmit(n, 2.1)
        seq = n._sequence
        self.assertTrue(n.acknowledge(seq, report.x, report.y, 0, True, 2.2))
        self.assertFalse(n.acknowledgment_timed_out(4))

    def test_old_matching_ack_does_not_clear_a_newer_barrier_timeout(self):
        n = self.native(y=32751)
        n.move(1, 0, 2)
        old = self.transmit(n, 2)
        old_seq = n._sequence
        n.move(0, 2, 2.1)
        self.transmit(n, 2.1)
        n.acknowledge(old_seq, old.x, old.y, 0, True, 2.2)
        self.assertEqual(n.state, "edge_wait")
        self.assertTrue(n.acknowledgment_timed_out(2.91))

    def test_relative_estimate_and_old_ack_cannot_authorize_left_return(self):
        n = self.native(x=0, y=100)
        old_seq = n._sequence
        n.move(0, -10, 2)
        self.transmit(n, 2, acknowledge=True)
        self.assertEqual(n.state, "relative")
        self.assertIsNone(n.pointer.edge_sent_at)
        n.acknowledge(old_seq, 0, 100, 0, True, 2.1)
        n.move(-100, -2, 2.2)
        self.transmit(n, 2.3)
        self.assertFalse(n.return_requested)
        self.assertIsNone(n.pointer.edge_sent_at)
        n.move(0, 12, 2.4)
        resync = self.transmit(n, 2.5)
        self.assertEqual(resync.barrier, "resync")
        n.move(-1, 0, 2.51)  # Queued before confirmation, cannot become a new push.
        n.acknowledge(n._waiting_sequence, resync.x, resync.y, 0, True, 2.6)
        self.transmit(n, 2.8)
        self.assertFalse(n.return_requested)
        n.move(-1, 0, 2.81)
        self.assertIsNone(n.next_report())
        self.assertTrue(n.return_requested)

    def test_rejected_ack_and_failed_write_disarm_and_stop(self):
        n = self.native(y=32751)
        n.move(0, 2, 2)
        report = self.transmit(n, 2)
        n.acknowledge(n._waiting_sequence, report.x, report.y, 0, False, 2.1)
        self.assertEqual(n.failure, "BLE report rejected")
        self.assertIsNone(n.next_report())
        other = self.native()
        other.move(1, 0, 2)
        report = other.next_report()
        other.submitted(report, 2, 2, False)
        self.assertEqual(other.failure, "native edge serial write failed")

    def test_fractional_relative_tail_is_consumed_by_absolute_resync_once(self):
        n = self.native()
        self.enter_bottom(n)
        n.move(1, 0, 2.1)
        self.assertIsNone(n.next_report())
        n.move(0, -12, 2.2)
        resync = self.transmit(n, 2.3, acknowledge=True)
        self.assertEqual((resync.channel, resync.x), ("absolute", 16400))
        self.assertIsNone(n.next_report())

    def test_left_placement_native_edges_and_corner_are_mirrored(self):
        for x, y, dx, dy, edges in [
            (100, 16, 0, -4, ("top",)),
            (16, 100, -4, 0, ("left",)),
            (16, 16, -4, -4, ("left", "top")),
            (16, 32751, -4, 4, ("left", "bottom")),
        ]:
            with self.subTest(edges=edges):
                n = self.native(x, y, return_edge="right")
                n.move(dx, dy, 2)
                edge = self.transmit(n, acknowledge=True)
                self.assertEqual(n.edges, edges)
                self.assertEqual(n.state, "relative")
                if "left" in edges:
                    self.assertEqual(edge.x, 0)
                tail = self.transmit(n, 2.02)
                self.assertEqual(tail.channel, "relative")
                self.assertLessEqual(tail.x, 0)
                self.assertEqual(tail.y > 0, dy > 0)

    def test_right_corner_horizontal_push_returns_and_vertical_intent_is_native(self):
        n = self.native(x=32767, y=0, return_edge="right")
        n.move(2, -1, 2)
        self.assertIsNone(n.next_report())
        self.assertTrue(n.return_requested)
        self.assertEqual(n.state, "absolute")
        for y, dy in [(0, -100), (32767, 100), (0, -1), (32767, 1)]:
            with self.subTest(y=y, dy=dy):
                n = self.native(x=32767, y=y, return_edge="right")
                n.move(1, dy, 2)
                edge = self.transmit(n, 2)
                self.assertFalse(n.return_requested)
                self.assertEqual((n.state, edge.barrier), ("edge_wait", "edge"))

    def test_native_left_edge_resyncs_after_inward_motion_with_original_signs(self):
        n = self.native(x=0, return_edge="right")
        n.move(-2, 0, 2)
        self.transmit(n, 2, acknowledge=True)
        relative = self.transmit(n, 2.02)
        self.assertEqual((relative.channel, relative.x), ("relative", -1))
        n.move(2, 0, 2.1)
        relative = self.transmit(n, 2.1)
        self.assertEqual((relative.channel, relative.x), ("relative", 1))
        n.move(10, 0, 2.2)
        resync = self.transmit(n, 2.2, acknowledge=True)
        self.assertEqual((resync.channel, resync.barrier, resync.x),
                         ("absolute", "resync", 192))
        self.assertEqual(n.state, "absolute")

    def test_right_return_requires_fresh_ack_after_leaving_native_channel(self):
        n = self.native(x=32767, y=100, return_edge="right")
        old_seq = n._sequence
        n.move(0, -10, 2)
        self.transmit(n, 2, acknowledge=True)
        n.acknowledge(old_seq, 32767, 100, 0, True, 2.1)
        n.move(100, -2, 2.2)
        self.transmit(n, 2.3)
        self.assertFalse(n.return_requested)
        self.assertIsNone(n.pointer.edge_sent_at)
        n.move(0, 12, 2.4)
        resync = self.transmit(n, 2.5)
        n.move(1, 0, 2.51)
        n.acknowledge(n._waiting_sequence, resync.x, resync.y, 0, True, 2.6)
        self.transmit(n, 2.8)
        self.assertFalse(n.return_requested)
        n.move(1, 0, 2.81)
        self.assertIsNone(n.next_report())
        self.assertTrue(n.return_requested)

    def test_right_return_drag_requires_release_ack_and_locked_mode_stays_remote(self):
        n = self.native(x=32767, return_edge="right")
        n.button(1, 2)
        n.move(20, 0, 2.1)
        self.transmit(n, 2.2, acknowledge=True)
        self.transmit(n, 2.3, acknowledge=True)
        self.assertFalse(n.return_requested)
        n.button(0, 2.4)
        n.move(20, 0, 2.5)
        self.transmit(n, 2.6, acknowledge=True)
        self.assertFalse(n.return_requested)
        n.move(20, 0, 2.9)
        self.assertIsNone(n.next_report())
        self.assertTrue(n.return_requested)
        locked = self.native(x=32767, return_edge="right")
        locked.pointer.edge_enabled = False
        locked.move(20, 0, 2)
        self.assertEqual(self.transmit(locked).channel, "absolute")
        self.assertFalse(locked.return_requested)


class ControllerNativeEdgeTests(unittest.TestCase):
    def controller(self, y=32767):
        c = KVMController(BridgeConfig(absolute_enabled=True, native_edges_enabled=True, mode="mixed"))
        c._is_active = True
        c._pointer = AbsolutePointer(x=16384, y=y)
        c._native_edges = NativeEdges(c._pointer)
        c._bridge = Mock()
        c._bridge.status = Status()
        c._bridge.send_absolute_report.return_value = True
        c._bridge.send_mouse_report.return_value = True
        return c

    def flush(self, c, at):
        with patch("bridge.time.monotonic", return_value=at):
            c._flush_native_mouse(force=True)

    def ack(self, c, at):
        buttons, x, y, _, seq = c._bridge.send_absolute_report.call_args.args
        c._bridge.status = Status(ack_sequence=seq, ack_x=x, ack_y=y,
                                 ack_buttons=buttons, ack_accepted=True, ack_at=at)
        c._confirm_absolute_edge()

    def relative_controller(self):
        c = self.controller()
        self.flush(c, 1)
        self.ack(c, 1.01)
        with patch("bridge.time.monotonic", return_value=2):
            c._handle_raw_mouse_move(0, 2)
        self.ack(c, 2.01)
        self.flush(c, 2.02)
        return c

    def test_integration_uses_relative_for_motion_buttons_and_scroll(self):
        c = self.relative_controller()
        before = c._bridge.send_absolute_report.call_count
        with patch("bridge.time.monotonic", return_value=3):
            c._handle_raw_mouse_move(0, 8)
        self.assertEqual(c._bridge.send_mouse_report.call_args.args, (0, 0, 4, 0))
        with patch("bridge.time.monotonic", return_value=3.1):
            c._handle_mouse_click(0, 0, mouse.Button.left, True)
        self.assertEqual(c._bridge.send_mouse_report.call_args.args, (1, 0, 0, 0))
        with patch("bridge.time.monotonic", return_value=3.2):
            c._handle_mouse_scroll(0, 0, 0, 2)
        self.assertEqual(c._bridge.send_mouse_report.call_args.args, (1, 0, 0, 2))
        self.assertEqual(c._bridge.send_absolute_report.call_count, before)

    def test_cleanup_relative_never_moves_to_old_absolute_position(self):
        c = self.relative_controller()
        before = c._bridge.send_absolute_report.call_count
        c._state.mouse_buttons = 1
        c._release_all_remote_inputs()
        self.assertEqual(c._bridge.send_absolute_report.call_count, before)
        c._bridge.send_mouse_report.assert_called_with(0, 0, 0, 0)
        c._bridge.send_keyboard_report.assert_called_with(0, [])
        self.assertEqual(c._state.mouse_buttons, 0)

    def test_cleanup_pending_resync_still_releases_actual_relative_channel(self):
        c = self.relative_controller()
        c._native_edges.move(0, -12, 3)
        c._native_edges._advance()  # Resync generated but not yet submitted.
        before = c._bridge.send_absolute_report.call_count
        c._release_all_remote_inputs()
        self.assertEqual(c._bridge.send_absolute_report.call_count, before)
        c._bridge.send_mouse_report.assert_called_with(0, 0, 0, 0)

    def test_exhausted_sequence_window_cannot_prevent_absolute_button_release(self):
        c = self.controller()
        self.flush(c, 1)
        c._state.mouse_buttons = 1
        c._native_edges._recent_sequences = {i: 2 for i in range(256)}
        with patch("bridge.time.monotonic", return_value=2.1):
            c._release_all_remote_inputs()
        c._bridge.send_absolute_report.assert_called_with(0, 16384, 32767, 0, 0)
        c._bridge.send_mouse_report.assert_called_with(0, 0, 0, 0)

    def test_first_relative_partial_write_cleanup_does_not_replay_absolute(self):
        c = self.controller()
        self.flush(c, 1)
        self.ack(c, 1.01)
        with patch("bridge.time.monotonic", return_value=2):
            c._handle_raw_mouse_move(0, 2)
        self.ack(c, 2.01)
        c._bridge.send_mouse_report.return_value = False
        self.flush(c, 2.02)
        self.assertTrue(c._exit_requested)
        before = c._bridge.send_absolute_report.call_count
        c._release_all_remote_inputs()
        self.assertEqual(c._bridge.send_absolute_report.call_count, before)

    def test_serial_failure_returns_control(self):
        c = self.relative_controller()
        c._bridge.send_mouse_report.return_value = False
        with patch("bridge.time.monotonic", return_value=3):
            c._handle_raw_mouse_move(0, 8)
        self.assertTrue(c._exit_requested)
        self.assertEqual(c._exit_reason, "ESP32 USB disconnected")

    def test_candidate_flag_is_opt_in(self):
        self.assertFalse(BridgeConfig().native_edges_enabled)
        self.assertIsNone(KVMController(BridgeConfig())._native_edges)


if __name__ == "__main__":
    unittest.main()
