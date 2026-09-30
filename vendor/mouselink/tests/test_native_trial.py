"""The A/B harness must not claim accurate edge return in relative-only trials."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from trial_bridge import MouseTrace, TrialBridge, TrialController, trial_config
from bridge import KVMController, SerialBridge


class TrialConfigurationTests(unittest.TestCase):
    def test_hotkey_can_finish_diagnostic_without_ending_normal_edge_returns(self):
        for enabled, reason in [(True, "Ctrl+Alt"), (True, "iPad left edge"), (False, "Ctrl+Alt")]:
            with self.subTest(enabled=enabled, reason=reason):
                trace = MouseTrace()
                controller = TrialController(trial_config("native", "mixed", "COM99"), trace, enabled)
                controller._exit_reason = reason
                with patch.object(KVMController, "_enter_remote_mode"), patch.object(controller, "stop") as stop:
                    controller._enter_remote_mode()
                self.assertEqual(stop.call_count, int(enabled and reason == "Ctrl+Alt"))
                self.assertEqual(trace.events[-1]["reason"], reason)

    def test_trace_preserves_signed_motion_and_absolute_sequence_without_keyboard_content(self):
        trace = MouseTrace()
        bridge = TrialBridge(trial_config("native", "mixed", "COM99"), trace)
        with patch.object(SerialBridge, "_transmit", return_value=True):
            bridge.send_mouse_report(1, -32768, 32767, -1)
            bridge.send_absolute_report(0, 1234, 32767, 0, 77)
            bridge.send_keyboard_report(0, [4])
        self.assertEqual(len(trace.events), 2)
        relative, absolute = trace.events
        self.assertEqual((relative["channel"], relative["x"], relative["y"], relative["wheel"]),
                         ("relative", -32768, 32767, -1))
        self.assertEqual((absolute["channel"], absolute["x"], absolute["y"], absolute["sequence"]),
                         ("absolute", 1234, 32767, 77))

    def test_relative_trial_always_has_an_explicit_return(self):
        for mode in ("edge", "mixed", "locked"):
            config = trial_config("relative", mode, "COM99")
            self.assertEqual(config.mode, "locked")
            self.assertFalse(config.absolute_enabled)
            self.assertFalse(config.native_edges_enabled)

    def test_absolute_baseline_and_candidate_are_distinct(self):
        for pointer in ("absolute", "native"):
            for mode in ("edge", "mixed", "locked"):
                config = trial_config(pointer, mode, "COM99")
                self.assertTrue(config.absolute_enabled)
                self.assertEqual(config.native_edges_enabled, pointer == "native")
                self.assertEqual(config.mode, mode)


if __name__ == "__main__":
    unittest.main()
