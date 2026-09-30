"""The backwards-compatible serial extension for a real absolute HID mouse."""

import struct
from dataclasses import dataclass
from functools import reduce
from operator import xor


def packet(report_type, payload):
    data = bytes((0, report_type)) + payload
    if len(data) != 8:
        raise ValueError("A report must contain six payload bytes")
    return b"\xfd" + data + bytes((reduce(xor, data, 0),))


def absolute_packet(x, y, buttons=0, wheel=0, sequence=0):
    if not (0 <= x <= 32767 and 0 <= y <= 32767):
        raise ValueError("Absolute coordinates must be 0..32767")
    if not (0 <= buttons <= 31 and -127 <= wheel <= 127):
        raise ValueError("Invalid buttons or wheel value")
    if not 0 <= sequence <= 255:
        raise ValueError("Invalid sequence")
    data = bytes((sequence, 4)) + struct.pack("<BHHb", buttons, x, y, wheel)
    return b"\xfd" + data + bytes((reduce(xor, data, 0),))


@dataclass
class Status:
    connected: bool = False
    version: int = 0
    absolute_subscribed: bool = False
    connected_at: float = 0.0
    capability_at: float = 0.0
    last_report_id: int = 0
    last_send_result: int = -1
    report_count: int = 0
    hid_mode: int = -1
    transport_at: float = 0.0
    ack_sequence: int = -1
    ack_accepted: bool = False
    ack_x: int = -1
    ack_y: int = -1
    ack_buttons: int = 0
    ack_at: float = 0.0

    def ready(self, now):
        return (self.connected and self.version in (2, 3) and self.absolute_subscribed
                and now - self.connected_at < 1.0 and now - self.capability_at < 1.0)


class StatusParser:
    def __init__(self):
        self.buffer = bytearray()
        self.status = Status()

    def feed(self, data, now):
        self.buffer.extend(data)
        while self.buffer:
            head = self.buffer[0]
            size = {0xFC: 3, 0xFB: 4, 0xF8: 8, 0xF7: 9}.get(head, 0)
            if size == 0:
                del self.buffer[0]
                continue
            if len(self.buffer) < size:
                break
            frame = self.buffer[:size]
            if reduce(xor, frame[1:-1], 0xA5) != frame[-1]:
                del self.buffer[0]
                continue
            if head == 0xFC:
                self.status.connected = bool(frame[1] & 1)
                self.status.connected_at = now
            elif head == 0xFB:
                self.status.version = frame[1]
                self.status.absolute_subscribed = bool(frame[2] & 1)
                self.status.capability_at = now
            elif head == 0xF8:
                self.status.last_report_id = frame[1]
                self.status.last_send_result = int.from_bytes(frame[2:4], "little")
                self.status.report_count = int.from_bytes(frame[4:6], "little")
                self.status.hid_mode = frame[6]
                self.status.transport_at = now
            else:
                self.status.ack_sequence = frame[1]
                self.status.ack_accepted = bool(frame[2] & 1)
                self.status.ack_x = int.from_bytes(frame[3:5], "little")
                self.status.ack_y = int.from_bytes(frame[5:7], "little")
                self.status.ack_buttons = frame[7]
                self.status.ack_at = now
            del self.buffer[:size]
        return self.status
