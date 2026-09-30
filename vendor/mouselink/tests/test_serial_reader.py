"""Read short status frames promptly without opening a serial device."""
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
from bridge import BridgeConfig, SerialBridge


ACK = bytes.fromhex("f7 2a 01 00 00 00 40 00 ce")


class FragmentedSerial:
    def __init__(self, chunks, available):
        self.chunks = chunks
        self.available = available
        self.index = 0
        self.is_open = True
        self.read_sizes = []

    @property
    def in_waiting(self):
        return self.available[self.index]

    def read(self, size):
        self.read_sizes.append(size)
        chunk = self.chunks[self.index]
        if len(chunk) > size:
            raise AssertionError("The reader requested less than the supplied fragment")
        self.index += 1
        self.is_open = self.index < len(self.chunks)
        return chunk


class PausedSerialRead:
    """Hold a read until a test releases it, without using a serial handle."""
    in_waiting = 0

    def __init__(self, fail=False):
        self.is_open = True
        self.started = threading.Event()
        self.release_read = threading.Event()
        self.closed = threading.Event()
        self.finished = False
        self.closed_during_read = False
        self.close_count = 0
        self.fail = fail

    def read(self, size):
        self.started.set()
        if not self.release_read.wait(1):
            raise AssertionError("Test did not release the paused read")
        self.finished = True
        if self.fail:
            raise serial.SerialException("cancelled during shutdown")
        return ACK[:1]

    def close(self):
        self.close_count += 1
        self.closed_during_read |= not self.finished
        self.is_open = False
        self.closed.set()


class SerialReaderTests(unittest.TestCase):
    def read_fragments(self, chunks, available):
        bridge = SerialBridge(BridgeConfig())
        connection = FragmentedSerial(chunks, available)
        bridge._read_status_loop(connection, threading.Event())
        return bridge, connection

    def test_idle_buffer_waits_for_one_byte(self):
        _, connection = self.read_fragments([b""], [0])
        self.assertEqual(connection.read_sizes, [1])

    def test_short_ack_does_not_request_a_full_read_block(self):
        bridge, connection = self.read_fragments([ACK], [len(ACK)])
        self.assertEqual(connection.read_sizes, [9])
        self.assertEqual(bridge.status.ack_sequence, 42)
        self.assertTrue(bridge.status.ack_accepted)

    def test_first_byte_then_fragments_reconstruct_the_ack(self):
        bridge, connection = self.read_fragments([ACK[:1], ACK[1:3], ACK[3:]], [0, 2, 6])
        self.assertEqual(connection.read_sizes, [1, 2, 6])
        self.assertEqual((bridge.status.ack_sequence, bridge.status.ack_x, bridge.status.ack_y),
                         (42, 0, 16384))
        self.assertTrue(bridge.status.ack_accepted)

    def test_large_buffer_keeps_the_existing_read_limit(self):
        data = b"noise" * 8 + ACK
        bridge, connection = self.read_fragments([data[:32], data[32:]], [len(data), len(data) - 32])
        self.assertEqual(connection.read_sizes, [32, 17])
        self.assertEqual(bridge.status.ack_sequence, 42)

    def test_queue_query_failure_closes_the_connection(self):
        for failure in (serial.SerialException("unplugged"), OSError("closed")):
            with self.subTest(failure=type(failure).__name__):
                bridge = SerialBridge(BridgeConfig())
                connection = Mock(is_open=True)
                type(connection).in_waiting = PropertyMock(side_effect=failure)
                connection.close.side_effect = lambda: setattr(connection, "is_open", False)
                bridge._serial = connection
                bridge._ble_connected = True
                bridge._read_status_loop(connection, threading.Event())
                connection.read.assert_not_called()
                connection.close.assert_called_once()
                self.assertFalse(bridge.is_connected)
                self.assertFalse(bridge._ble_connected)

    def test_disconnect_waits_for_reader_before_closing_handles(self):
        for fail in (False, True):
            with self.subTest(read_fails_during_shutdown=fail):
                bridge = SerialBridge(BridgeConfig())
                connection = PausedSerialRead(fail)
                bridge._serial = connection
                bridge._start_reader_unsafe()
                reader = bridge._reader_thread
                errors = []

                def disconnect():
                    try:
                        bridge.disconnect()
                    except Exception as exc:
                        errors.append(exc)

                closer = threading.Thread(target=disconnect, daemon=True)
                try:
                    self.assertTrue(connection.started.wait(1))
                    closer.start()
                    self.assertTrue(bridge._reader_stop.wait(1))
                    self.assertFalse(connection.closed.is_set())
                finally:
                    connection.release_read.set()
                    if closer.ident is not None:
                        closer.join(1)
                    reader.join(1)
                self.assertFalse(closer.is_alive())
                self.assertFalse(reader.is_alive())
                self.assertEqual(errors, [])
                self.assertFalse(connection.closed_during_read)
                self.assertEqual(connection.close_count, 1)
                self.assertIsNone(bridge._reader_thread)
                self.assertIsNone(bridge._serial)
                self.assertEqual(bridge.status.ack_sequence, -1)
                self.assertEqual(bridge._status_parser.buffer, bytearray())

    def test_repeated_disconnect_without_reader_closes_only_once(self):
        bridge = SerialBridge(BridgeConfig())
        connection = Mock(is_open=True)
        bridge._serial = connection
        bridge.disconnect()
        bridge.disconnect()
        connection.close.assert_called_once()
        self.assertIsNone(bridge._serial)
        self.assertIsNone(bridge._reader_thread)


if __name__ == "__main__":
    unittest.main()
