"""Time-bounded A/B trials. Reports transport evidence, never visual acceptance."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from bridge import BridgeConfig, KVMController, SerialBridge
from serial.tools.list_ports import comports


class MouseTrace:
    """Bounded, opt-in mouse-only trace; no keyboard or clipboard contents."""

    def __init__(self):
        self.started = time.perf_counter()
        self.events = []
        self.dropped = 0
        self._lock = threading.Lock()

    def record(self, kind, **values):
        with self._lock:
            if len(self.events) < 100000:
                self.events.append(dict(seconds=round(time.perf_counter() - self.started, 6),
                                        kind=kind, **values))
            else:
                self.dropped += 1


class TrialController(KVMController):
    def __init__(self, config, trace=None, stop_on_hotkey_return=False):
        super().__init__(config)
        self.trace = trace
        self.stop_on_hotkey_return = stop_on_hotkey_return

    def _enter_remote_mode(self):
        if self.trace:
            self.trace.record("remote_session", phase="start")
        try:
            return super()._enter_remote_mode()
        finally:
            if self.trace:
                self.trace.record("remote_session", phase="end", reason=self._exit_reason)
            if self.stop_on_hotkey_return and self._exit_reason == "Ctrl+Alt":
                self.stop()

    def _handle_raw_mouse_move(self, delta_x, delta_y):
        if self.trace and self._is_active and not self._exit_requested:
            self.trace.record("raw", dx=delta_x, dy=delta_y)
        return super()._handle_raw_mouse_move(delta_x, delta_y)

    def _release_all_remote_inputs(self):
        if self.trace:
            self.trace.record("input_release", reason=self._exit_reason)
        return super()._release_all_remote_inputs()

    def _native_state(self):
        native = self._native_edges
        return native.state if native else None

    def _record_transition(self, before, source):
        native = self._native_edges
        if self.trace and native and before != native.state:
            self.trace.record("state", before=before, after=native.state, source=source,
                              edges=native.edges, position=native.pointer.report_position,
                              pending=native.pending_events)

    def _flush_native_mouse(self, force=False):
        before = self._native_state()
        super()._flush_native_mouse(force)
        self._record_transition(before, "input")

    def _confirm_absolute_edge(self):
        before = self._native_state()
        super()._confirm_absolute_edge()
        self._record_transition(before, "ack")


class TrialBridge(SerialBridge):
    def __init__(self, config, trace=None):
        super().__init__(config)
        self.trace = trace
        self.report_counts = Counter()
        self.failed_writes = 0
        self.button_transitions = []
        self._last_buttons = {}
        self.started_at = time.monotonic()

    def _transmit(self, packet):
        success = super()._transmit(packet)
        if self.trace and packet[2] in (3, 4):
            self.trace.record("report", channel="relative" if packet[2] == 3 else "absolute",
                              x=int.from_bytes(packet[4:6], "little", signed=packet[2] == 3),
                              y=int.from_bytes(packet[6:8], "little", signed=packet[2] == 3),
                              buttons=packet[3], wheel=int.from_bytes(packet[8:9], "little", signed=True),
                              sequence=packet[1], success=success)
        if success:
            self.report_counts[f"0x{packet[2]:02x}"] += 1
            if packet[2] in (3, 4) and self._last_buttons.get(packet[2]) != packet[3]:
                self._last_buttons[packet[2]] = packet[3]
                if len(self.button_transitions) < 200:
                    self.button_transitions.append({"seconds": round(time.monotonic() - self.started_at, 3),
                                                    "report": packet[2], "buttons": packet[3]})
        else:
            self.failed_writes += 1
        return success


def trial_config(pointer, mode, port):
    # A purely relative diagnostic cannot locate the iPad's true left edge.
    return BridgeConfig(port=port, mode="locked" if pointer == "relative" else mode,
                        absolute_enabled=pointer != "relative",
                        native_edges_enabled=pointer == "native", reconnect_max_attempts=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["edge", "locked", "mixed"], default="mixed")
    parser.add_argument("--pointer", choices=["absolute", "relative", "native"], default="absolute")
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--port")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trace-mouse", action="store_true",
                        help="Save bounded raw mouse, outgoing reports and native transitions")
    parser.add_argument("--stop-on-hotkey-return", action="store_true",
                        help="Finish and save this trial after Ctrl+left Alt return")
    args = parser.parse_args()
    if args.trace_mouse and not args.output:
        parser.error("--trace-mouse requires --output")
    if not 15 <= args.seconds <= 600:
        parser.error("--seconds must be between 15 and 600")
    ports = [p.device for p in comports() if p.vid == 0x303A and p.pid == 0x1001]
    port = args.port if args.port in ports else ports[0] if not args.port and len(ports) == 1 else None
    if not port:
        raise SystemExit("Specify the connected ESP32-C3 --port; expected exactly one device")
    trace = MouseTrace() if args.trace_mouse else None
    controller = TrialController(trial_config(args.pointer, args.mode, port), trace,
                                 args.stop_on_hotkey_return)
    connection = TrialBridge(controller._config, trace)
    controller._bridge = connection
    started = time.monotonic()
    log = {"started_at": datetime.now(timezone.utc).isoformat(), "pointer": args.pointer,
           "mode": controller._config.mode, "port": port, "limit_seconds": args.seconds,
           "visual_result": "NOT_VERIFIED", "timer_expired": False}

    def expire():
        log["timer_expired"] = True
        controller.stop()

    timer = threading.Timer(args.seconds, expire)
    timer.daemon = True
    timer.start()
    print(f"TRIAL pointer={args.pointer} mode={controller._config.mode} limit={args.seconds:g}s", flush=True)
    print("Close other bridges first. Enter from the PC right edge. Scroll Lock always returns.", flush=True)
    if args.pointer == "relative":
        print("Relative diagnostic: use Ctrl+left Alt or Scroll Lock to return; no automatic left return.", flush=True)
    try:
        controller.run()
    except Exception as exc:
        log["error"] = str(exc)
        raise
    finally:
        timer.cancel()
        controller.stop()
        log.update(elapsed_seconds=round(time.monotonic() - started, 3),
                   report_counts=dict(connection.report_counts), failed_writes=connection.failed_writes,
                   button_transitions=connection.button_transitions,
                   final_status=vars(connection.status).copy(), last_exit_reason=controller._exit_reason)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if trace:
                trace_path = args.output.with_suffix(".mouse-trace.json")
                trace_path.write_text(json.dumps({"events": trace.events, "dropped": trace.dropped},
                                                ensure_ascii=False), encoding="utf-8")
                log["mouse_trace"] = str(trace_path)
                log["mouse_trace_events"] = len(trace.events)
                log["mouse_trace_dropped"] = trace.dropped
            args.output.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(log, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
