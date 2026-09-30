"""Read status or send bounded, click-free absolute positions. Never captures PC input."""

import argparse
import json
import time
from pathlib import Path

import serial
from serial.tools.list_ports import comports

from absolute_protocol import StatusParser, absolute_packet, packet


POINTS = [
    ("center", 16384, 16384),
    ("near_left_10_percent", 3277, 16384),
    ("left_edge", 0, 16384),
    ("near_right_90_percent", 29490, 16384),
    ("near_top_10_percent", 16384, 3277),
    ("near_bottom_90_percent", 16384, 29490),
    ("center", 16384, 16384),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port")
    ap.add_argument("--move", action="store_true", help="Run click-free position test")
    ap.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--seconds", type=float, default=8)
    args = ap.parse_args()
    ports = [p.device for p in comports() if p.vid == 0x303A and p.pid == 0x1001]
    port = args.port or (ports[0] if len(ports) == 1 else None)
    if not port:
        raise SystemExit("Specify --port; expected exactly one ESP32-C3")
    parser = StatusParser()
    log = {"port": port, "orientation": args.orientation, "positions_sent": [],
           "visual_result": "NOT_VERIFIED", "transport": "BLE HID over ESP32-C3"}
    connection = serial.Serial(port=None, baudrate=115200, timeout=0.05, write_timeout=0.5)
    connection.dtr = False
    connection.rts = False
    connection.port = port
    connection.open()
    last_point = None
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            status = parser.feed(connection.read(128), time.monotonic())
            if status.ready(time.monotonic()):
                break
        print(json.dumps(vars(parser.status), ensure_ascii=False), flush=True)
        log["status"] = vars(parser.status).copy()
        if args.move:
            if not parser.status.ready(time.monotonic()):
                raise RuntimeError("Absolute report is not ready; re-pair if firmware changed")
            for label, x, y in POINTS:
                print(f"POSITION {label}: ({x}, {y})", flush=True)
                connection.write(absolute_packet(x, y))
                connection.flush()
                last_point = (x, y)
                log["positions_sent"].append({"name": label, "x": x, "y": y})
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    parser.feed(connection.read(128), time.monotonic())
                    if not parser.status.ready(time.monotonic()):
                        raise RuntimeError("Connection or report subscription lost")
                log["positions_sent"][-1]["transport_status"] = vars(parser.status).copy()
                print(f"BLE result={parser.status.last_send_result} "
                      f"report={parser.status.last_report_id} "
                      f"count={parser.status.report_count}", flush=True)
    finally:
        try:
            if last_point is not None:
                connection.write(absolute_packet(*last_point))
                connection.write(packet(3, bytes(6)))
                connection.flush()
        finally:
            connection.close()
            log["final_status"] = vars(parser.status).copy()
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
