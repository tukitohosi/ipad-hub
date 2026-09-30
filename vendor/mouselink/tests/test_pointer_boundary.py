import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from pointer_boundary import AbsolutePointer, rotate_position


class BoundaryTests(unittest.TestCase):
    def test_large_jump_must_first_submit_edge_then_receive_fresh_outward_input(self):
        p = AbsolutePointer(x=30000)
        self.assertFalse(p.move(-50000, 0, 1))
        self.assertEqual(p.report_position[0], 0)
        self.assertFalse(p.move(-20, 0, 2))
        p.sent(0, 16384, 2, True)
        self.assertFalse(p.move(-10, 0, 2.1))
        self.assertFalse(p.move(0, 10, 2.2))
        self.assertTrue(p.move(-10, 0, 2.2))

    def test_failed_transmission_never_arms_return(self):
        p = AbsolutePointer(x=0)
        p.sent(0, 16384, 1, False)
        self.assertFalse(p.move(-100, 0, 10))

    def test_drag_stays_on_ipad_and_requires_a_new_edge_report(self):
        p = AbsolutePointer(x=0)
        p.sent(0, 16384, 1, True)
        self.assertFalse(p.move(-10, 0, 2, buttons=1))
        self.assertFalse(p.move(-10, 0, 3))
        p.sent(0, 16384, 3, True)
        self.assertTrue(p.move(-10, 0, 3.2))

    def test_moving_away_disarms_edge(self):
        p = AbsolutePointer(x=0)
        p.sent(0, 16384, 1, True)
        self.assertFalse(p.move(10000, 0, 2))
        self.assertFalse(p.move(-10000, 0, 3))
        self.assertIsNone(p.edge_sent_at)

    def test_locked_mode_never_exits_at_edge(self):
        p = AbsolutePointer(x=0, edge_enabled=False)
        p.sent(0, 16384, 1, True)
        self.assertFalse(p.move(-99999, 0, 10))

    def test_all_rotation_mappings_are_reversible(self):
        for q in range(4):
            pos = rotate_position(123, 789, q)
            self.assertEqual(rotate_position(*pos, -q), (123, 789))

    def test_serial_submission_alone_cannot_arm_return(self):
        p = AbsolutePointer(x=0)
        p.submitted(7, 0, 16384, 1, True)
        self.assertFalse(p.move(-30, 0, 1.3))
        p.acknowledge(7, 0, 16384, 0, True, 1.4)
        self.assertFalse(p.move(-30, 0, 1.5))
        self.assertTrue(p.move(-30, 0, 1.6))

    def test_stale_or_wrong_ack_and_drag_cannot_arm_return(self):
        for seq, x, y, accepted, delay in [(8,0,16384,True,.1), (7,1,16384,True,.1),
                                         (7,0,16384,False,.1), (7,0,16384,True,.8)]:
            p = AbsolutePointer(x=0)
            p.submitted(7, 0, 16384, 1, True)
            p.acknowledge(seq,x,y,0,accepted,1+delay)
            self.assertFalse(p.move(-30, 0, 2))
        p = AbsolutePointer(x=0)
        p.submitted(7, 0, 16384, 1, True)
        p.move(10, 0, 1.1)
        p.move(-10, 0, 1.2)
        p.acknowledge(7, 0, 16384, 0, True, 1.3)
        self.assertFalse(p.move(-30, 0, 1.6))

    def test_both_return_edges_require_a_later_push_after_matching_ack(self):
        for edge, x, dx in [("left", 0, -100), ("right", 32767, 100)]:
            with self.subTest(edge=edge):
                p = AbsolutePointer(x=16384, return_edge=edge)
                self.assertFalse(p.move(dx * 1000, 0, 1))
                self.assertEqual(p.x, x)
                p.submitted(7, x, 16384, 1.1, True)
                self.assertFalse(p.move(dx, 0, 1.3))
                p.acknowledge(7, x, 16384, 0, True, 1.4)
                self.assertFalse(p.move(dx, 0, 1.5))
                self.assertTrue(p.move(dx, 0, 1.6))

    def test_other_edge_and_inward_motion_never_return(self):
        for edge, x, outward in [("left", 32767, 10), ("right", 0, -10)]:
            with self.subTest(edge=edge):
                p = AbsolutePointer(x=x, return_edge=edge)
                p.submitted(1, x, 16384, 1, True)
                p.acknowledge(1, x, 16384, 0, True, 1.1)
                self.assertFalse(p.move(outward, 0, 2))
                p.x = p.return_x
                p.sent(p.return_x, 16384, 2, True)
                self.assertFalse(p.move(outward, 0, 3))
                self.assertIsNone(p.edge_sent_at)

    def test_right_edge_drag_locked_and_moving_away_disarm_return(self):
        p = AbsolutePointer(x=32767, return_edge="right")
        p.sent(32767, 16384, 1, True)
        self.assertFalse(p.move(10, 0, 2, buttons=1))
        self.assertFalse(p.move(10, 0, 3))
        p.sent(32767, 16384, 3, True)
        self.assertFalse(p.move(-10, 0, 4))
        self.assertFalse(p.move(10, 0, 5))
        self.assertIsNone(p.edge_sent_at)
        p.edge_enabled = False
        p.sent(32767, 16384, 5, True)
        self.assertFalse(p.move(10, 0, 6))

    def test_right_edge_rejects_wrong_stale_and_previous_visit_ack(self):
        for sequence, x, buttons, accepted, at in [
            (8, 32767, 0, True, 1.1), (7, 0, 0, True, 1.1),
            (7, 32767, 1, True, 1.1), (7, 32767, 0, False, 1.1),
            (7, 32767, 0, True, .9), (7, 32767, 0, True, 1.8),
        ]:
            with self.subTest(sequence=sequence, x=x, buttons=buttons, at=at):
                p = AbsolutePointer(x=32767, return_edge="right")
                p.submitted(7, 32767, 16384, 1, True)
                p.acknowledge(sequence, x, 16384, buttons, accepted, at)
                self.assertFalse(p.move(10, 0, 2))
        p = AbsolutePointer(x=32767, return_edge="right")
        p.submitted(7, 32767, 16384, 1, True)
        p.move(-10, 0, 1.1)
        p.move(10, 0, 1.2)
        p.acknowledge(7, 32767, 16384, 0, True, 1.3)
        self.assertFalse(p.move(10, 0, 1.6))


if __name__ == "__main__":
    unittest.main()
