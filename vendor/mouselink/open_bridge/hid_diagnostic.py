"""Send isolated HID test reports without capturing or blocking Windows input."""

import argparse
import time

import serial
import serial.tools.list_ports


HEADER = 0xFD
ESPRESSIF_VID = 0x303A


def find_port():
    for port in serial.tools.list_ports.comports():
        if port.vid == ESPRESSIF_VID:
            return port.device
    return None


def packet(data):
    checksum = 0
    for value in data:
        checksum ^= value
    return bytes([HEADER, *data, checksum])


def mouse_report(dx=0, dy=0, wheel=0, buttons=0):
    dx &= 0xFFFF
    dy &= 0xFFFF
    data = [
        0x00,
        0x03,
        buttons & 0x1F,
        dx & 0xFF,
        (dx >> 8) & 0xFF,
        dy & 0xFF,
        (dy >> 8) & 0xFF,
        wheel & 0xFF,
    ]
    return packet(data)


def keyboard_report(modifiers=0, keys=()):
    padded = list(keys[:6]) + [0] * (6 - len(keys[:6]))
    return packet([modifiers & 0xFF, 0x00, *padded])


def run_mouse_wiggle(connection, seconds):
    deadline = time.monotonic() + seconds
    direction = 1
    while time.monotonic() < deadline:
        connection.write(mouse_report(dx=24 * direction))
        connection.flush()
        direction *= -1
        time.sleep(0.5)
    connection.write(mouse_report())
    connection.flush()


def run_keyboard_a(connection):
    # HID usage 0x04 is the A key.
    connection.write(keyboard_report(keys=(0x04,)))
    connection.write(keyboard_report())
    connection.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("mouse", "keyboard"))
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    port = find_port()
    if not port:
        raise SystemExit("ESP32-C3 serial port was not found")

    print(f"Using {port}; Windows input remains local", flush=True)
    connection = serial.Serial()
    connection.port = port
    connection.baudrate = 115200
    connection.timeout = 0.1
    connection.write_timeout = 0.5
    connection.dtr = False
    connection.rts = False
    with connection:
        if args.mode == "mouse":
            run_mouse_wiggle(connection, args.seconds)
        else:
            run_keyboard_a(connection)
    print("Diagnostic finished", flush=True)


if __name__ == "__main__":
    main()
