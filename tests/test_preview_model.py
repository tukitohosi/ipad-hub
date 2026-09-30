import unittest

from ipadhub.model import Mode, PreviewSession, State


class SessionTests(unittest.TestCase):
    def test_fifty_roundtrips_release_before_start(self):
        s = PreviewSession()
        token = s.request_start(Mode.DISPLAY)
        s.advance(token)
        for _ in range(50):
            for target in (Mode.BRIDGE, Mode.DISPLAY):
                old = s.owner
                token = s.request_start(target)
                self.assertEqual(s.state, State.STOPPING)
                self.assertEqual(s.owner, old)
                self.assertIsNone(s.request_start(target))
                s.advance(token)
                self.assertEqual((s.state, s.owner), (State.CONNECTING, target))
                s.advance(token)
                self.assertEqual(s.state, State.ACTIVE)

    def test_cancel_invalidates_late_connect_event(self):
        s = PreviewSession()
        old = s.request_start(Mode.BRIDGE)
        new = s.stop()
        self.assertFalse(s.advance(old))
        s.advance(new)
        self.assertIsNone(s.owner)
        self.assertEqual(s.state, State.READY)

    def test_stop_failure_blocks_other_mode_until_recovery(self):
        s = PreviewSession(scenario="停止失败")
        s.advance(s.request_start(Mode.BRIDGE))
        s.advance(s.request_start(Mode.DISPLAY))
        self.assertTrue(s.release_blocked)
        self.assertEqual(s.owner, Mode.BRIDGE)
        self.assertIsNone(s.request_start(Mode.DISPLAY))
        self.assertIsNone(s.stop())
        token = s.recover()
        self.assertIsNone(s.request_start(Mode.DISPLAY))
        s.advance(token)
        self.assertIsNone(s.owner)
        self.assertIsNotNone(s.request_start(Mode.DISPLAY))

    def test_missing_conditions_checked_after_release(self):
        s = PreviewSession()
        s.advance(s.request_start(Mode.DISPLAY))
        s.scenario = "缺少条件"
        token = s.request_start(Mode.BRIDGE)
        self.assertEqual(s.state, State.STOPPING)
        s.advance(token)
        self.assertEqual(s.state, State.MISSING)
        self.assertIsNone(s.owner)

    def test_flash_blocks_mode_and_stale_completion(self):
        s = PreviewSession()
        self.assertTrue(s.begin_tool())
        token = s.epoch
        self.assertIsNone(s.request_start(Mode.DISPLAY))
        self.assertIsNone(s.stop())
        self.assertFalse(s.finish_tool(token - 1))
        self.assertTrue(s.finish_tool(token))
        self.assertIsNotNone(s.request_start(Mode.DISPLAY))
        self.assertFalse(s.begin_tool())

    def test_cancel_pending_switch_never_starts_target(self):
        s = PreviewSession()
        s.advance(s.request_start(Mode.DISPLAY))
        old = s.request_start(Mode.BRIDGE)
        token = s.stop()
        self.assertFalse(s.advance(old))
        s.advance(token)
        self.assertEqual((s.owner, s.state), (None, State.READY))

    def test_failed_connection_can_retry(self):
        s = PreviewSession(scenario="连接失败")
        s.advance(s.request_start(Mode.DISPLAY))
        self.assertEqual((s.owner, s.state), (None, State.FAILED))
        s.scenario = "正常连接"
        s.advance(s.request_start(Mode.BRIDGE))
        self.assertEqual(s.state, State.ACTIVE)

    def test_reset_invalidates_callbacks(self):
        s = PreviewSession()
        token = s.request_start(Mode.DISPLAY)
        s.reset()
        self.assertFalse(s.advance(token))
        self.assertEqual(s.state, State.READY)


if __name__ == "__main__":
    unittest.main()
