"""Experimental absolute positioning with native relative screen-edge motion.

Coordinates after a relative report are estimates, not iPad feedback. They are
used only to propose a new absolute anchor, never to authorize an edge return.
The two Mouse collections still require physical iPad acceptance testing.
"""

from collections import deque
from dataclasses import dataclass


LIMIT = 32767.0
ACK_TIMEOUT = 0.8
RESYNC_INWARD_COUNTS = 6.0


@dataclass
class MouseReport:
    channel: str
    buttons: int
    x: float = 0
    y: float = 0
    wheel: int = 0
    barrier: str = ""


class NativeEdges:
    """An ordered input/report queue; only submitted absolute reports await ACKs."""

    def __init__(self, pointer, sensitivity=0.5, absolute_scale=32.0, recent_sequences=None):
        self.pointer = pointer
        self.sensitivity = sensitivity
        self.absolute_scale = absolute_scale
        self.state = "absolute"
        self.buttons = 0
        self.active_channel = "absolute"
        self.last_absolute = pointer.report_position
        self.edges = ()
        self.return_requested = False
        self.failure = ""
        self._events = deque()
        self._reports = deque()
        self._sent = {}
        self._order = 0
        self._sequence = 0
        self._recent_sequences = recent_sequences if recent_sequences is not None else {}
        self._waiting_sequence = None
        self._wheel_fraction = 0.0
        self._queue_absolute()

    @property
    def pending_events(self):
        return len(self._events)

    def _enqueue(self, kind, x, y, now):
        if len(self._events) >= 4096:
            self.failure = "native edge input queue overflow"
            return
        self._events.append((kind, x, y, now))

    def move(self, dx, dy, now):
        self._enqueue("move", dx, dy, now)

    def button(self, buttons, now):
        self._enqueue("button", buttons, 0, now)

    def scroll(self, wheel, now):
        self._enqueue("wheel", wheel, 0, now)

    def _disarm_return(self):
        self.pointer.edge_sent_at = None
        self.pointer.pending.clear()

    def _queue_absolute(self, wheel=0, barrier=""):
        report = MouseReport("absolute", self.buttons,
                             *self.pointer.report_position, wheel, barrier)
        # Coalesce movement only. Preserve every button/wheel event and barrier.
        if (self._reports and not barrier and not wheel
                and self._reports[-1].channel == "absolute"
                and not self._reports[-1].barrier and not self._reports[-1].wheel
                and self._reports[-1].buttons == self.buttons):
            self._reports[-1] = report
        else:
            self._reports.append(report)

    def _queue_relative(self, dx=0, dy=0, wheel=0, button_event=False):
        if (self._reports and not button_event
                and self._reports[-1].channel == "relative"
                and not self._reports[-1].barrier
                and self._reports[-1].buttons == self.buttons):
            report = self._reports[-1]
            report.x += dx
            report.y += dy
            report.wheel += wheel
        else:
            self._reports.append(MouseReport("relative", self.buttons, dx, dy,
                                             wheel, "button" if button_event else ""))

    def _first_edge(self, dx, dy):
        hits = []
        x, y = self.pointer.x, self.pointer.y
        if dx < 0 and x + dx <= 0:
            # At return corners, vertical intent belongs to iPad gestures.
            # Small horizontal jitter must not turn a bottom push into return.
            if self.pointer.return_edge != "left" or abs(dx) > abs(dy):
                hits.append((-x / dx, "left"))
        if dx > 0 and x + dx >= LIMIT:
            if self.pointer.return_edge != "right" or abs(dx) > abs(dy):
                hits.append(((LIMIT - x) / dx, "right"))
        if dy < 0 and y + dy <= 0:
            hits.append((-y / dy, "top"))
        if dy > 0 and y + dy >= LIMIT:
            hits.append(((LIMIT - y) / dy, "bottom"))
        if not hits:
            return None
        fraction = min(t for t, _ in hits)
        edges = tuple(edge for t, edge in hits if abs(t - fraction) < 1e-9)
        # The connection edge belongs exclusively to the confirmed Windows return.
        return None if self.pointer.return_edge in edges else (fraction, edges)

    def _inward(self, dx, dy):
        outward = {"top": -dy, "bottom": dy, "left": -dx, "right": dx}
        if any(outward[edge] > 0 for edge in self.edges):
            return False
        distance = {"top": self.pointer.y, "bottom": LIMIT - self.pointer.y,
                    "left": self.pointer.x, "right": LIMIT - self.pointer.x}
        threshold = self.absolute_scale * RESYNC_INWARD_COUNTS
        # Keep small reversals on the relative channel. The clamped shadow
        # naturally cancels this distance when the user pushes back to the edge.
        # At corners, only an edge being left by this event can authorize resync.
        return any(outward[edge] < 0 and distance[edge] >= threshold for edge in self.edges)

    def _move_absolute(self, dx, dy, at):
        scale = self.absolute_scale * self.sensitivity
        ax, ay = dx * scale, dy * scale
        hit = self._first_edge(ax, ay) if not self.buttons else None
        if hit is None:
            edge_return = self.pointer.move(ax, ay, at, self.buttons)
            self.return_requested = edge_return and abs(ax) > abs(ay)
            if not self.return_requested:
                self._queue_absolute()
            return
        fraction, self.edges = hit
        self.pointer.move(ax * fraction, ay * fraction, at, self.buttons)
        # Use exact endpoints despite floating point intersection arithmetic.
        if "top" in self.edges:
            self.pointer.y = 0.0
        if "bottom" in self.edges:
            self.pointer.y = LIMIT
        if "right" in self.edges:
            self.pointer.x = LIMIT
        if "left" in self.edges:
            self.pointer.x = 0.0
        self._disarm_return()
        self.state = "edge_wait"
        self._queue_absolute(barrier="edge")
        if fraction < 1:
            # Only the unconsumed segment enters the relative channel. Keeping
            # both components preserves diagonal motion without replaying X/Y.
            self._events.appendleft(("move", dx * (1 - fraction),
                                     dy * (1 - fraction), at))

    def _move_relative(self, dx, dy, at):
        scale = self.absolute_scale * self.sensitivity
        self.pointer.x = self.pointer._clamp(self.pointer.x + dx * scale)
        self.pointer.y = self.pointer._clamp(self.pointer.y + dy * scale)
        self._disarm_return()
        if not self.buttons and self._inward(dx, dy):
            self.state = "resync_wait"
            self._queue_absolute(barrier="resync")
        else:
            self._queue_relative(dx * self.sensitivity, dy * self.sensitivity)

    def _advance(self):
        while (self._events and self.state in ("absolute", "relative")
               and not self.return_requested and not self.failure):
            kind, x, y, at = self._events.popleft()
            if kind == "move":
                if self.state == "absolute":
                    self._move_absolute(x, y, at)
                else:
                    self._move_relative(x, y, at)
            elif kind == "button":
                self.buttons = x
                if self.buttons:
                    self._disarm_return()
                if self.state == "relative":
                    self._queue_relative(button_event=True)
                else:
                    self._queue_absolute(barrier="button")
            else:
                self._wheel_fraction += x
                wheel = int(self._wheel_fraction)
                self._wheel_fraction -= wheel
                if wheel:
                    if self.state == "relative":
                        self._queue_relative(wheel=wheel)
                    else:
                        self._queue_absolute(wheel=wheel)

    def next_report(self):
        self._advance()
        if self.failure or self.return_requested:
            return None
        while self._reports:
            report = self._reports[0]
            if report.channel == "absolute":
                self._reports.popleft()
                wheel = max(-127, min(127, report.wheel))
                if wheel != report.wheel:
                    self._reports.appendleft(MouseReport("absolute", report.buttons,
                                                         report.x, report.y, report.wheel - wheel))
                return MouseReport("absolute", report.buttons, report.x, report.y, wheel, report.barrier)
            dx = max(-32768, min(32767, int(report.x)))
            dy = max(-32768, min(32767, int(report.y)))
            wheel = max(-127, min(127, report.wheel))
            if dx or dy or wheel or report.barrier == "button":
                report.x -= dx
                report.y -= dy
                report.wheel -= wheel
                report.barrier = ""
                if not (report.x or report.y or report.wheel):
                    self._reports.popleft()
                return MouseReport("relative", report.buttons, dx, dy, wheel)
            if len(self._reports) == 1:
                return None  # Retain sub-count motion until it can be emitted.
            following = self._reports[1]
            if following.channel == "relative":
                following.x += report.x
                following.y += report.y
            # A resync's absolute target already includes the fractional motion
            # in its shadow coordinate, so it must not be emitted a second time.
            self._reports.popleft()
        return None

    def next_sequence(self, now):
        for key, at in list(self._recent_sequences.items()):
            if now - at >= ACK_TIMEOUT:
                del self._recent_sequences[key]
        for _ in range(256):
            self._sequence = (self._sequence + 1) & 255
            if self._sequence not in self._recent_sequences:
                self._recent_sequences[self._sequence] = now
                return self._sequence
        self.failure = "native edge sequence window exhausted"
        return None

    def submitted(self, report, sequence, now, success):
        if report.channel == "relative":
            # A failed/partial serial write may nevertheless reach the device.
            # Cleanup must not replay an old absolute point after that attempt.
            self.active_channel = "relative"
        if not success:
            self.failure = "native edge serial write failed"
            self._disarm_return()
            return
        self.active_channel = report.channel
        if report.channel == "relative":
            return
        self.last_absolute = (report.x, report.y)
        self._order += 1
        self._sent[sequence] = (report, now, self._order)
        self.pointer.submitted(sequence, report.x, report.y, now, True, report.buttons)
        if report.barrier in ("edge", "resync"):
            self._waiting_sequence = sequence

    def acknowledge(self, sequence, x, y, buttons, accepted, at):
        sent = self._sent.get(sequence)
        if sent is None:
            return False
        report, submitted_at, order = sent
        if ((report.x, report.y, report.buttons) != (x, y, buttons)
                or not 0 <= at - submitted_at < ACK_TIMEOUT):
            return False
        if not accepted:
            self.failure = "BLE report rejected"
            self._disarm_return()
            return False
        # The status parser exposes the latest ACK. A matching newer report
        # proves ordered progress without requiring every older ACK to surface.
        self._sent = {key: value for key, value in self._sent.items() if value[2] > order}
        if sequence == self._waiting_sequence:
            self._waiting_sequence = None
            if report.barrier == "edge":
                self.state = "relative"
                self._sent.clear()
                self._disarm_return()
            elif report.barrier == "resync":
                self.state = "absolute"
                self.pointer.acknowledge(sequence, x, y, buttons, accepted, at)
        elif self.state == "absolute":
            self.pointer.acknowledge(sequence, x, y, buttons, accepted, at)
        return True

    def acknowledgment_timed_out(self, now):
        if not self._sent:
            return False
        waiting = self._sent.get(self._waiting_sequence)
        submitted_at = waiting[1] if waiting else min(item[1] for item in self._sent.values())
        return now - submitted_at > ACK_TIMEOUT
