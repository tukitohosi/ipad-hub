"""Physical cursor travel, asynchronous input ordering and short session lifetime."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from absolute_protocol import Status
from bridge import BridgeConfig, InputInbox, KVMController, MotionTiming
from native_edges import NativeEdges
from pointer_boundary import AbsolutePointer
from pointer_calibration import PhysicalMotion, WindowsStrokeSampler, build_profile, validate_profile
from pynput.mouse import Button


def profile():
    strokes = [dict(rate=rate, gain=gain, mouse="mouse-a", monitor="screen", windows="14,6,10,1")
               for rate, gain in [(100, .5), (1000, 1.5), (5000, 3)]]
    return build_profile(strokes, (2560, 1440), (600, 337.5), (250, 175), "landscape")


class PhysicalMotionTests(unittest.TestCase):
    def test_each_axis_matches_windows_millimetres_independent_of_screen_shape(self):
        cfg = profile()
        for freq in (125, 500, 1000):
            for rate, gain in cfg["curve"]:
                with self.subTest(freq=freq, rate=rate):
                    motion = PhysicalMotion(cfg)
                    # Warm up the 24 ms speed estimator, then compare the actual
                    # virtual displacement with the Windows movement in mm.
                    for index in range(freq):
                        motion.project(rate/freq, 0, index/freq, 1)
                    result = [motion.project(rate/freq, 0, 1 + i/freq, 1)[0]
                              for i in range(freq)]
                    windows_mm = rate * gain * 600 / 2560
                    self.assertLess(abs(sum(result) * 250 / 32767 / windows_mm - 1), .10)
        mx = PhysicalMotion(cfg).project(.5, 0, 1, 1)[0]
        my = PhysicalMotion(cfg).project(0, .5, 1, 1)[1]
        self.assertAlmostEqual(mx * 250, my * 175)
        self.assertAlmostEqual(PhysicalMotion(cfg).project(.5, 0, 0, 1.5)[0], mx * 1.5)

    def test_invalid_profile_rejected_and_mixed_devices_cannot_be_fitted(self):
        for bad in [dict(profile(), curve=[[0,.2],[1,.2],[2,.4]]),
                    dict(profile(), hid_per_px=[float("nan"), 1]),
                    dict(profile(), orientation="sideways")]:
            with self.assertRaises(ValueError):
                validate_profile(bad)
        sample = dict(mouse="first", monitor="screen", windows="settings", rate=100, gain=.5)
        with self.assertRaisesRegex(ValueError, "同一"):
            build_profile([sample, dict(sample, rate=400), dict(sample, mouse="second", rate=1200)],
                          (2500,1440), (600,338), (250,175), "landscape")

    def test_finish_excludes_initial_and_final_idle_and_releases_capture(self):
        sampler = WindowsStrokeSampler()
        capture = Mock(failure=None)
        cursor = [(200,300), (230,302), (270,301), (300,300), (300,300)]
        with patch("bridge.RawMouseCapture", return_value=capture), \
             patch("pointer_calibration.physical_cursor", side_effect=cursor), \
             patch("pointer_calibration.reference_monitor", return_value=("screen",(0,0,1000,800))), \
             patch("pointer_calibration.windows_pointer_signature", return_value="settings"), \
             patch("pointer_calibration.time.perf_counter", side_effect=[4,4.1,4.2]):
            sampler.start()
            for dx in (10,20,20):
                sampler._record(dx,0,"mouse")
            sample = sampler.finish()
        self.assertAlmostEqual(sample["rate"],250)
        self.assertEqual(sample["gain"],2)
        capture.stop.assert_called_once()
        self.assertIsNone(sampler.capture)

    def test_sampler_restores_capture_on_cursor_failure(self):
        sampler = WindowsStrokeSampler()
        sampler.capture = Mock()
        capture = sampler.capture
        with patch("pointer_calibration.physical_cursor", side_effect=OSError("失败")):
            with self.assertRaises(OSError):
                sampler.finish()
        capture.stop.assert_called_once()

    def controller(self):
        c = KVMController(BridgeConfig(absolute_enabled=True, native_edges_enabled=True,
                          pointer_profile=profile(), sensitivity=1))
        c._is_active = True
        c._pointer = AbsolutePointer(x=16000,y=16000)
        c._native_edges = NativeEdges(c._pointer,sensitivity=1)
        c._inbox = InputInbox()
        c._physical_motion = PhysicalMotion(profile())
        c._bridge = Mock(status=Status())
        c._bridge.send_absolute_report.return_value = True
        c._bridge.send_keyboard_report.return_value = True
        return c

    def test_real_callbacks_only_enqueue_and_writer_preserves_input_order(self):
        c = self.controller()
        inbox = c._inbox
        c._record_mouse_input(inbox,10,0,"mouse-a")
        c._submit_keys(2, ())
        inbox.put("button",Button.left,True)
        c._record_mouse_input(inbox,5,0,"mouse-a")
        inbox.put("button",Button.left,False)
        c._submit_keys(0, ())
        self.assertEqual(c._bridge.mock_calls,[])
        c._consume_input(inbox)
        operations = [(str(call[0]), *call.args[:1]) for call in c._bridge.mock_calls]
        self.assertEqual(operations,[("send_absolute_report",0),("send_keyboard_report",2),
                                     ("send_absolute_report",1),("send_absolute_report",1),
                                     ("send_absolute_report",0),("send_keyboard_report",0)])
        self.assertEqual(c.timing.matched_raw,2)
        self.assertGreater(c._pointer.x,16000)
        inbox.close()
        c._inbox = InputInbox()
        c._record_mouse_input(inbox,10,0,"mouse-a")
        self.assertFalse(inbox.take())
        self.assertFalse(c._inbox.take())

    def test_slow_serial_does_not_hold_input_capture_lock(self):
        c = self.controller()
        writing, done = threading.Event(), threading.Event()
        def slow_write(*args):
            writing.set()
            return done.wait(2)
        c._bridge.send_absolute_report.side_effect = slow_write
        c._inbox.put("button",Button.left,True)
        worker = threading.Thread(target=c._consume_input, args=(c._inbox,))
        worker.start()
        try:
            self.assertTrue(writing.wait(1))
            c._record_mouse_input(c._inbox,2,0,"mouse-a")
            self.assertEqual(len(c._inbox.events),1)
        finally:
            done.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_edge_remainder_is_projected_once_and_keyboard_waits_for_edge_ack(self):
        c = self.controller()
        n = c._native_edges
        n.pointer.y = 32766
        n.motion = Mock()
        n.motion.project.return_value = (10,40,False)
        n.move(1,4,1)
        n.keyboard(2,(),1.01)
        n.next_report() # centre report
        edge = n.next_report()
        self.assertEqual(edge.barrier,"edge")
        n.submitted(edge,1,1.02,True)
        self.assertIsNone(n.next_report())
        n.acknowledge(1,edge.x,edge.y,0,True,1.03)
        self.assertEqual(n.next_report().channel,"relative")
        self.assertEqual(n.next_report().channel,"keyboard")
        n.motion.project.assert_called_once()

    def test_interval_metrics_exclude_time_with_no_input(self):
        m = MotionTiming(.008)
        for at in (1,1.008,1.016,8):
            m.raw(at,at+.001)
            m.written(at+.002,True)
        m.publish(2,True)
        self.assertEqual(m.last_snapshot["continuous_intervals"],2)
        self.assertEqual(m.last_snapshot["gap_p95_ms"],8)
        self.assertEqual(m.last_snapshot["queue_age_p95_ms"],1)
