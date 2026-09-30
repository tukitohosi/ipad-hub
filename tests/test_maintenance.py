"""Mocked elevation lifecycle; no test invokes UAC or writes driver settings."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from ipadhub.maintenance import ResolutionTask


class ResolutionTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ipadhub-resolution-test-")
        self.addCleanup(self.temp.cleanup)
        self.exe = Path(self.temp.name) / "iPadHubDisplay.exe"
        self.exe.write_bytes(b"mock helper only")
        self.launch = self.enterContext(patch("ipadhub.maintenance._launch_elevated", return_value=123))
        self.poll = self.enterContext(patch("ipadhub.maintenance._poll_process", return_value=None))
        self.close = self.enterContext(patch("ipadhub.maintenance._close_process"))
        self.tasks = []
        self.addCleanup(self.cleanup_tasks)

    def cleanup_tasks(self):
        for task in self.tasks:
            task.timer.stop()
            if task._worker is not None:
                task._worker.wait(3000)
            task.deleteLater()
        self.app.processEvents()

    def task(self, width=2732, height=2048, executable=None):
        task = ResolutionTask(executable or self.exe, width, height)
        finished, progress = [], []
        task.finished.connect(lambda ok, detail: finished.append((ok, detail)))
        task.progress.connect(progress.append)
        self.tasks.append(task)
        return task, finished, progress

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate())

    def launched(self, task):
        task.start()
        self.wait_for(lambda: task._handle == 123 and not task._worker.isRunning())
        self.app.processEvents()

    def test_constructing_task_does_not_prompt_or_launch(self):
        task, finished, _ = self.task()
        self.assertFalse(task.busy)
        self.assertFalse(task.timer.isActive())
        self.assertEqual(finished, [])
        self.launch.assert_not_called()

    def test_parameters_reject_invalid_and_no_uac(self):
        for width, height in [(319, 2048), (2733, 2048), (8194, 2048),
                              (2732, True), ("2732", 2048), (8192, 8192)]:
            with self.subTest(width=width, height=height):
                task, finished, _ = self.task(width, height)
                task.start()
                self.assertEqual(len(finished), 1)
                self.assertFalse(finished[0][0])
                self.assertFalse(task.busy)
        self.launch.assert_not_called()

    def test_missing_helper_never_requests_elevation(self):
        task, finished, _ = self.task(executable=Path(self.temp.name) / "missing.exe")
        task.start()
        self.assertFalse(finished[0][0])
        self.launch.assert_not_called()

    def test_success_waits_for_process_and_closes_once(self):
        task, finished, _ = self.task()
        self.launched(task)
        self.assertTrue(task.busy)
        self.assertEqual(finished, [])
        self.launch.assert_called_once_with(self.exe, 2732, 2048)
        self.close.assert_not_called()
        self.poll.return_value = 0
        task._poll()
        self.assertEqual(len(finished), 1)
        self.assertTrue(finished[0][0])
        self.assertFalse(task.busy)
        self.assertFalse(task.timer.isActive())
        self.close.assert_called_once_with(123)
        task.start()
        task._poll()
        self.assertEqual(len(finished), 1)
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(self.close.call_count, 1)

    def test_slow_helper_stays_busy_until_confirmed_exit(self):
        task, finished, progress = self.task()
        self.launched(task)
        task._started_at = time.monotonic() - 60
        task._poll()
        self.assertTrue(task.busy)
        self.assertEqual(finished, [])
        self.assertTrue(any("仍在等待" in message for message in progress))
        self.close.assert_not_called()
        self.poll.return_value = 0
        task._poll()
        self.assertTrue(finished[0][0])

    def test_uac_cancel_finishes_after_launch_thread(self):
        cancelled = OSError("cancelled")
        cancelled.winerror = 1223
        self.launch.side_effect = cancelled
        task, finished, _ = self.task()
        task.start()
        self.wait_for(lambda: bool(finished))
        self.assertFalse(finished[0][0])
        self.assertIn("已取消", finished[0][1])
        self.assertFalse(task._worker.isRunning())
        self.assertFalse(task.busy)
        self.close.assert_not_called()

    def test_old_sender_occupancy_error_is_specific(self):
        task, finished, _ = self.task()
        self.launched(task)
        self.poll.return_value = 9
        task._poll()
        self.assertFalse(finished[0][0])
        self.assertIn("占用", finished[0][1])
        self.close.assert_called_once_with(123)

    def test_uncertain_poll_does_not_unlock_or_close(self):
        task, finished, progress = self.task()
        self.launched(task)
        self.poll.side_effect = OSError("temporary wait failure")
        task._poll()
        self.assertTrue(task.busy)
        self.assertEqual(finished, [])
        self.assertTrue(any("无法确认" in message for message in progress))
        self.close.assert_not_called()
        self.poll.side_effect = None
        self.poll.return_value = 0
        task._poll()
        self.assertTrue(finished[0][0])
        self.close.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
