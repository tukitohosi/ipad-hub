import sys
import unittest
from unittest.mock import Mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from absolute_protocol import StatusParser, absolute_packet
from return_hotkey import ReturnChord


class ProtocolTests(unittest.TestCase):
    def test_absolute_bounds_and_layout(self):
        p = absolute_packet(0, 32767, 5, -1)
        self.assertEqual(p[:9], bytes.fromhex("fd 00 04 05 00 00 ff 7f ff"))
        self.assertEqual(len(p), 10)
        with self.assertRaises(ValueError):
            absolute_packet(32768, 0)

    def test_fragmented_status_and_noise(self):
        parser = StatusParser()
        parser.feed(b"garbage\xfc\x01", 10)
        self.assertFalse(parser.status.ready(10))
        parser.feed(bytes.fromhex("a4 fb 02 01 a6"), 10)
        self.assertTrue(parser.status.ready(10.2))
        self.assertFalse(parser.status.ready(11.1))
        parser.feed(bytes.fromhex("fc 00 a5"), 10.3)
        self.assertFalse(parser.status.ready(10.3))

    def test_bad_checksum_cannot_enable_absolute(self):
        parser = StatusParser()
        parser.feed(bytes.fromhex("fc 01 a4 fb 02 01 00"), 10)
        self.assertFalse(parser.status.ready(10))

    def test_transport_result_does_not_prove_pointer_position(self):
        parser = StatusParser()
        frame = bytearray.fromhex("f8 04 00 00 7b 00 01")
        checksum = 0xA5
        for byte in frame[1:]:
            checksum ^= byte
        frame.append(checksum)
        parser.feed(frame[:4], 10)
        parser.feed(frame[4:], 10.1)
        self.assertEqual(parser.status.last_send_result, 0)
        self.assertEqual(parser.status.last_report_id, 4)
        self.assertEqual(parser.status.report_count, 123)
        self.assertFalse(parser.status.ready(10.1))

    def test_absolute_ack_is_fragment_safe_and_validated(self):
        parser = StatusParser()
        frame = bytearray.fromhex("f7 2a 01 00 00 00 40 00")
        checksum = 0xA5
        for byte in frame[1:]:
            checksum ^= byte
        frame.append(checksum)
        parser.feed(frame[:6], 1)
        self.assertEqual(parser.status.ack_sequence, -1)
        parser.feed(frame[6:], 1.1)
        self.assertTrue(parser.status.ack_accepted)
        self.assertEqual((parser.status.ack_sequence, parser.status.ack_x, parser.status.ack_y), (42,0,16384))
        frame[1] = 43
        parser.feed(frame, 1.2)
        self.assertEqual(parser.status.ack_sequence, 42)


class HotkeyTests(unittest.TestCase):
    def test_both_press_orders_and_repeated_press(self):
        for ctrl, alt in [(0xA2, 0xA4), (0xA4, 0xA2), (0xA3, 0xA4)]:
            chord = ReturnChord()
            chord.press(ctrl)
            chord.press(alt)
            chord.press(alt)
            self.assertFalse(chord.release(ctrl))
            self.assertTrue(chord.release(alt))
            self.assertFalse(chord.release(alt))

    def test_other_shortcuts_and_altgr_do_not_exit(self):
        for keys in [(0xA2, 0xA4, 0x41), (0x41, 0xA2, 0xA4), (0xA2, 0xA5), (0x1B,)]:
            chord = ReturnChord()
            for vk in keys:
                chord.press(vk)
            for vk in reversed(keys):
                self.assertFalse(chord.release(vk))

    def test_cancelled_chord_can_be_used_again(self):
        chord = ReturnChord()
        for key in [0xA2, 0xA4, 0x41]:
            chord.press(key)
        for key in [0x41, 0xA2, 0xA4]:
            self.assertFalse(chord.release(key))
        chord.press(0xA2)
        chord.press(0xA4)
        self.assertFalse(chord.release(0xA4))
        self.assertTrue(chord.release(0xA2))

    def test_real_controller_releases_only_after_full_chord(self):
        from bridge import BridgeConfig, KVMController, keyboard
        controller = KVMController(BridgeConfig())
        controller._is_active = True
        controller._bridge.send_keyboard_report = Mock(return_value=True)
        controller._handle_key_press(keyboard.Key.ctrl_l)
        controller._handle_key_press(keyboard.Key.alt_l)
        self.assertFalse(controller._exit_requested)
        controller._handle_key_release(keyboard.Key.alt_l)
        self.assertFalse(controller._exit_requested)
        controller._handle_key_release(keyboard.Key.ctrl_l)
        self.assertTrue(controller._exit_requested)
        self.assertEqual(controller._exit_reason, "Ctrl+Alt")

    def test_plain_escape_is_forwarded_to_ipad(self):
        from bridge import BridgeConfig, KVMController, keyboard
        controller = KVMController(BridgeConfig())
        controller._is_active = True
        controller._bridge.send_keyboard_report = Mock(return_value=True)
        controller._handle_key_press(keyboard.Key.esc)
        self.assertFalse(controller._exit_requested)
        controller._bridge.send_keyboard_report.assert_called_with(0, [0x29])


if __name__ == "__main__":
    unittest.main()
