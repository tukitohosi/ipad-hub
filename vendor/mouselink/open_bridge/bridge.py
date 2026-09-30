import ctypes
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

import serial
import serial.tools.list_ports
from pynput import keyboard, mouse
from return_hotkey import ReturnChord
from absolute_protocol import StatusParser, absolute_packet
from pointer_boundary import AbsolutePointer
from native_edges import NativeEdges


class WindowsMessage(IntEnum):
    INPUT = 0x00FF
    DESTROY = 0x0002
    QUIT = 0x0012


class RawInputFlag(IntFlag):
    INPUT_SINK = 0x00000100


class RawInputType(IntEnum):
    MOUSE = 0


class RawInputCommand(IntEnum):
    INPUT = 0x10000003


class HidUsage(IntEnum):
    GENERIC_DESKTOP = 0x01
    MOUSE = 0x02


class MouseButton(IntFlag):
    LEFT = 1
    RIGHT = 2
    MIDDLE = 4
    BACK = 8
    FORWARD = 16

    
class KeyboardModifier(IntFlag):
    CTRL_LEFT = 0x01
    SHIFT_LEFT = 0x02
    ALT_LEFT = 0x04
    GUI_LEFT = 0x08
    CTRL_RIGHT = 0x10
    SHIFT_RIGHT = 0x20
    ALT_RIGHT = 0x40
    GUI_RIGHT = 0x80


# Shortcut keys configuration
TOGGLE_KEY = keyboard.Key.scroll_lock
PASTE_TRIGGER_VK = 0x2D  # VK_INSERT
PASTE_TRIGGER_MODIFIERS = KeyboardModifier.CTRL_LEFT | KeyboardModifier.CTRL_RIGHT


# Windows Virtual Key Code -> HID Usage Code
VK_TO_HID = {
    # Letters A-Z (VK 0x41-0x5A -> HID 0x04-0x1D)
    0x41: 0x04, 0x42: 0x05, 0x43: 0x06, 0x44: 0x07, 0x45: 0x08, 0x46: 0x09,
    0x47: 0x0A, 0x48: 0x0B, 0x49: 0x0C, 0x4A: 0x0D, 0x4B: 0x0E, 0x4C: 0x0F,
    0x4D: 0x10, 0x4E: 0x11, 0x4F: 0x12, 0x50: 0x13, 0x51: 0x14, 0x52: 0x15,
    0x53: 0x16, 0x54: 0x17, 0x55: 0x18, 0x56: 0x19, 0x57: 0x1A, 0x58: 0x1B,
    0x59: 0x1C, 0x5A: 0x1D,
    # Digits 0-9 (VK 0x30-0x39 -> HID 0x27, 0x1E-0x26)
    0x30: 0x27, 0x31: 0x1E, 0x32: 0x1F, 0x33: 0x20, 0x34: 0x21, 0x35: 0x22,
    0x36: 0x23, 0x37: 0x24, 0x38: 0x25, 0x39: 0x26,
    # Function keys F1-F12 (VK 0x70-0x7B -> HID 0x3A-0x45)
    0x70: 0x3A, 0x71: 0x3B, 0x72: 0x3C, 0x73: 0x3D, 0x74: 0x3E, 0x75: 0x3F,
    0x76: 0x40, 0x77: 0x41, 0x78: 0x42, 0x79: 0x43, 0x7A: 0x44, 0x7B: 0x45,
    # Function keys F13-F24 (VK 0x7C-0x87 -> HID 0x68-0x73)
    0x7C: 0x68, 0x7D: 0x69, 0x7E: 0x6A, 0x7F: 0x6B, 0x80: 0x6C, 0x81: 0x6D,
    0x82: 0x6E, 0x83: 0x6F, 0x84: 0x70, 0x85: 0x71, 0x86: 0x72, 0x87: 0x73,
    # Special keys
    0x08: 0x2A,  # VK_BACK -> Backspace
    0x09: 0x2B,  # VK_TAB -> Tab
    0x0D: 0x28,  # VK_RETURN -> Enter
    0x13: 0x48,  # VK_PAUSE -> Pause
    0x14: 0x39,  # VK_CAPITAL -> Caps Lock
    0x1B: 0x29,  # VK_ESCAPE -> Escape
    0x20: 0x2C,  # VK_SPACE -> Space
    0x21: 0x4B,  # VK_PRIOR -> Page Up
    0x22: 0x4E,  # VK_NEXT -> Page Down
    0x23: 0x4D,  # VK_END -> End
    0x24: 0x4A,  # VK_HOME -> Home
    0x25: 0x50,  # VK_LEFT -> Left Arrow
    0x26: 0x52,  # VK_UP -> Up Arrow
    0x27: 0x4F,  # VK_RIGHT -> Right Arrow
    0x28: 0x51,  # VK_DOWN -> Down Arrow
    0x2C: 0x46,  # VK_SNAPSHOT -> Print Screen
    0x2D: 0x49,  # VK_INSERT -> Insert
    0x2E: 0x4C,  # VK_DELETE -> Delete
    # Numpad (VK 0x60-0x6F)
    0x60: 0x62,  # VK_NUMPAD0
    0x61: 0x59,  # VK_NUMPAD1
    0x62: 0x5A,  # VK_NUMPAD2
    0x63: 0x5B,  # VK_NUMPAD3
    0x64: 0x5C,  # VK_NUMPAD4
    0x65: 0x5D,  # VK_NUMPAD5
    0x66: 0x5E,  # VK_NUMPAD6
    0x67: 0x5F,  # VK_NUMPAD7
    0x68: 0x60,  # VK_NUMPAD8
    0x69: 0x61,  # VK_NUMPAD9
    0x6A: 0x55,  # VK_MULTIPLY
    0x6B: 0x57,  # VK_ADD
    0x6C: 0x85,  # VK_SEPARATOR (Keypad Comma)
    0x6D: 0x56,  # VK_SUBTRACT
    0x6E: 0x63,  # VK_DECIMAL
    0x6F: 0x54,  # VK_DIVIDE
    # Lock keys
    0x90: 0x53,  # VK_NUMLOCK
    0x91: 0x47,  # VK_SCROLL
    # OEM keys (US ANSI layout)
    0xBA: 0x33,  # VK_OEM_1 -> Semicolon
    0xBB: 0x2E,  # VK_OEM_PLUS -> Equal
    0xBC: 0x36,  # VK_OEM_COMMA -> Comma
    0xBD: 0x2D,  # VK_OEM_MINUS -> Minus
    0xBE: 0x37,  # VK_OEM_PERIOD -> Period
    0xBF: 0x38,  # VK_OEM_2 -> Slash
    0xC0: 0x35,  # VK_OEM_3 -> Grave Accent (backtick)
    0xDB: 0x2F,  # VK_OEM_4 -> Left Bracket
    0xDC: 0x31,  # VK_OEM_5 -> Backslash
    0xDD: 0x30,  # VK_OEM_6 -> Right Bracket
    0xDE: 0x34,  # VK_OEM_7 -> Apostrophe
    0xE2: 0x64,  # VK_OEM_102 -> Non-US Backslash (ISO keyboard)
    # Application key
    0x5D: 0x65,  # VK_APPS -> Application/Menu key
}

# Windows Virtual Key Code -> HID Modifier
VK_TO_MODIFIER = {
    0xA0: KeyboardModifier.SHIFT_LEFT,   # VK_LSHIFT
    0xA1: KeyboardModifier.SHIFT_RIGHT,  # VK_RSHIFT
    0xA2: KeyboardModifier.CTRL_LEFT,    # VK_LCONTROL
    0xA3: KeyboardModifier.CTRL_RIGHT,   # VK_RCONTROL
    0xA4: KeyboardModifier.ALT_LEFT,     # VK_LMENU
    0xA5: KeyboardModifier.ALT_RIGHT,    # VK_RMENU
    0x5B: KeyboardModifier.GUI_LEFT,     # VK_LWIN
    0x5C: KeyboardModifier.GUI_RIGHT,    # VK_RWIN
}

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_ESCAPE = 0x1B

HID_KEY = {
    "A": 0x04, "B": 0x05, "C": 0x06, "D": 0x07, "E": 0x08, "F": 0x09,
    "G": 0x0A, "H": 0x0B, "I": 0x0C, "J": 0x0D, "K": 0x0E, "L": 0x0F,
    "M": 0x10, "N": 0x11, "O": 0x12, "P": 0x13, "Q": 0x14, "R": 0x15,
    "S": 0x16, "T": 0x17, "U": 0x18, "V": 0x19, "W": 0x1A, "X": 0x1B,
    "Y": 0x1C, "Z": 0x1D,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "ENTER": 0x28, "ESC": 0x29, "BACKSPACE": 0x2A, "TAB": 0x2B, "SPACE": 0x2C,
    "MINUS": 0x2D, "EQUAL": 0x2E, "LBRACKET": 0x2F, "RBRACKET": 0x30,
    "BACKSLASH": 0x31, "SEMICOLON": 0x33, "APOSTROPHE": 0x34, "GRAVE": 0x35,
    "COMMA": 0x36, "PERIOD": 0x37, "SLASH": 0x38,
}

CHAR_TO_HID = {
    "a": (0, HID_KEY["A"]), "b": (0, HID_KEY["B"]), "c": (0, HID_KEY["C"]),
    "d": (0, HID_KEY["D"]), "e": (0, HID_KEY["E"]), "f": (0, HID_KEY["F"]),
    "g": (0, HID_KEY["G"]), "h": (0, HID_KEY["H"]), "i": (0, HID_KEY["I"]),
    "j": (0, HID_KEY["J"]), "k": (0, HID_KEY["K"]), "l": (0, HID_KEY["L"]),
    "m": (0, HID_KEY["M"]), "n": (0, HID_KEY["N"]), "o": (0, HID_KEY["O"]),
    "p": (0, HID_KEY["P"]), "q": (0, HID_KEY["Q"]), "r": (0, HID_KEY["R"]),
    "s": (0, HID_KEY["S"]), "t": (0, HID_KEY["T"]), "u": (0, HID_KEY["U"]),
    "v": (0, HID_KEY["V"]), "w": (0, HID_KEY["W"]), "x": (0, HID_KEY["X"]),
    "y": (0, HID_KEY["Y"]), "z": (0, HID_KEY["Z"]),
    "1": (0, HID_KEY["1"]), "2": (0, HID_KEY["2"]), "3": (0, HID_KEY["3"]),
    "4": (0, HID_KEY["4"]), "5": (0, HID_KEY["5"]), "6": (0, HID_KEY["6"]),
    "7": (0, HID_KEY["7"]), "8": (0, HID_KEY["8"]), "9": (0, HID_KEY["9"]),
    "0": (0, HID_KEY["0"]),
    " ": (0, HID_KEY["SPACE"]), "\t": (0, HID_KEY["TAB"]),
    "\n": (0, HID_KEY["ENTER"]),
    "-": (0, HID_KEY["MINUS"]), "=": (0, HID_KEY["EQUAL"]),
    "[": (0, HID_KEY["LBRACKET"]), "]": (0, HID_KEY["RBRACKET"]),
    "\\": (0, HID_KEY["BACKSLASH"]),
    ";": (0, HID_KEY["SEMICOLON"]), "'": (0, HID_KEY["APOSTROPHE"]),
    "`": (0, HID_KEY["GRAVE"]), ",": (0, HID_KEY["COMMA"]),
    ".": (0, HID_KEY["PERIOD"]), "/": (0, HID_KEY["SLASH"]),
    "!": (KeyboardModifier.SHIFT_LEFT, HID_KEY["1"]),
    "@": (KeyboardModifier.SHIFT_LEFT, HID_KEY["2"]),
    "#": (KeyboardModifier.SHIFT_LEFT, HID_KEY["3"]),
    "$": (KeyboardModifier.SHIFT_LEFT, HID_KEY["4"]),
    "%": (KeyboardModifier.SHIFT_LEFT, HID_KEY["5"]),
    "^": (KeyboardModifier.SHIFT_LEFT, HID_KEY["6"]),
    "&": (KeyboardModifier.SHIFT_LEFT, HID_KEY["7"]),
    "*": (KeyboardModifier.SHIFT_LEFT, HID_KEY["8"]),
    "(": (KeyboardModifier.SHIFT_LEFT, HID_KEY["9"]),
    ")": (KeyboardModifier.SHIFT_LEFT, HID_KEY["0"]),
    "_": (KeyboardModifier.SHIFT_LEFT, HID_KEY["MINUS"]),
    "+": (KeyboardModifier.SHIFT_LEFT, HID_KEY["EQUAL"]),
    "{": (KeyboardModifier.SHIFT_LEFT, HID_KEY["LBRACKET"]),
    "}": (KeyboardModifier.SHIFT_LEFT, HID_KEY["RBRACKET"]),
    "|": (KeyboardModifier.SHIFT_LEFT, HID_KEY["BACKSLASH"]),
    ":": (KeyboardModifier.SHIFT_LEFT, HID_KEY["SEMICOLON"]),
    "\"": (KeyboardModifier.SHIFT_LEFT, HID_KEY["APOSTROPHE"]),
    "~": (KeyboardModifier.SHIFT_LEFT, HID_KEY["GRAVE"]),
    "<": (KeyboardModifier.SHIFT_LEFT, HID_KEY["COMMA"]),
    ">": (KeyboardModifier.SHIFT_LEFT, HID_KEY["PERIOD"]),
    "?": (KeyboardModifier.SHIFT_LEFT, HID_KEY["SLASH"]),
    "ą": (KeyboardModifier.ALT_RIGHT, HID_KEY["A"]),
    "ć": (KeyboardModifier.ALT_RIGHT, HID_KEY["C"]),
    "ę": (KeyboardModifier.ALT_RIGHT, HID_KEY["E"]),
    "ł": (KeyboardModifier.ALT_RIGHT, HID_KEY["L"]),
    "ń": (KeyboardModifier.ALT_RIGHT, HID_KEY["N"]),
    "ó": (KeyboardModifier.ALT_RIGHT, HID_KEY["O"]),
    "ś": (KeyboardModifier.ALT_RIGHT, HID_KEY["S"]),
    "ż": (KeyboardModifier.ALT_RIGHT, HID_KEY["Z"]),
    "ź": (KeyboardModifier.ALT_RIGHT, HID_KEY["X"]),
    "Ą": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["A"]),
    "Ć": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["C"]),
    "Ę": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["E"]),
    "Ł": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["L"]),
    "Ń": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["N"]),
    "Ó": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["O"]),
    "Ś": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["S"]),
    "Ż": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["Z"]),
    "Ź": (KeyboardModifier.ALT_RIGHT | KeyboardModifier.SHIFT_LEFT, HID_KEY["X"]),
}

PYNPUT_TO_MOUSE_BUTTON = {
    mouse.Button.left: MouseButton.LEFT,
    mouse.Button.right: MouseButton.RIGHT,
    mouse.Button.middle: MouseButton.MIDDLE,
    mouse.Button.x1: MouseButton.BACK,
    mouse.Button.x2: MouseButton.FORWARD,
}


@dataclass
class BridgeConfig:
    port: str = ""
    baudrate: int = 115200
    sensitivity: float = 0.5
    poll_rate_hz: int = 125
    max_delta: int = 120
    reconnect_delay_seconds: float = 2.0
    reconnect_max_attempts: int = 5
    toggle_key: keyboard.Key = TOGGLE_KEY
    edge_enabled: bool = True
    edge_margin_px: int = 1
    edge_dwell_seconds: float = 0.18
    edge_reentry_cooldown_seconds: float = 0.8
    return_cursor_inset_px: int = 160
    mode: str = "locked"
    absolute_enabled: bool = False
    absolute_scale: float = 32.0
    native_edges_enabled: bool = False
    ipad_side: str = "right"
    require_ready_on_start: bool = False

    def __post_init__(self):
        if self.ipad_side not in ("left", "right"):
            self.ipad_side = "right"


@dataclass
class InputState:
    pressed_keys: set = field(default_factory=set)
    active_modifiers: int = 0
    mouse_buttons: int = 0
    accumulated_dx: float = 0.0
    accumulated_dy: float = 0.0
    accumulated_wheel: float = 0.0
    last_transmission_time: float = 0.0

    def reset(self):
        self.pressed_keys.clear()
        self.active_modifiers = 0
        self.mouse_buttons = 0
        self.accumulated_dx = 0.0
        self.accumulated_dy = 0.0
        self.accumulated_wheel = 0.0
        self.last_transmission_time = 0.0


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE

_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t

_LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
_HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
_WNDPROC = ctypes.WINFUNCTYPE(
    _LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

_user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
_user32.DefWindowProcW.restype = _LONG_PTR


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class _RAWINPUT_MOUSE(ctypes.Structure):
    _fields_ = [
        ("header", _RAWINPUTHEADER),
        ("mouse", _RAWMOUSE),
    ]


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", _HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class SerialBridge:
    PROTOCOL_HEADER = 0xFD
    STATUS_HEADER = 0xFC
    STATUS_CHECK_XOR = 0xA5
    STATUS_STALE_SECONDS = 1.0
    KEYBOARD_KEYS_COUNT = 6

    def __init__(self, config):
        self._config = config
        self._serial = None
        self._lock = threading.Lock()
        self._reader_stop = threading.Event()
        self._reader_thread = None
        self._ble_connected = False
        self._last_status_at = 0.0
        self._status_parser = StatusParser()

    @property
    def status(self):
        return self._status_parser.status

    @property
    def absolute_ready(self):
        return self.status.version == 3 and self.status.ready(time.monotonic())

    def send_absolute_report(self, buttons, x, y, wheel, sequence):
        return self._transmit(absolute_packet(x, y, buttons, wheel, sequence))

    @property
    def is_connected(self):
        return self._serial is not None and self._serial.is_open

    @property
    def ble_connected(self):
        return (
            self._ble_connected
            and time.monotonic() - self._last_status_at <= self.STATUS_STALE_SECONDS
        )

    def wait_for_ble_status(self, timeout=1.5):
        deadline = time.monotonic() + timeout
        while self.is_connected and time.monotonic() < deadline:
            if self._last_status_at > 0:
                return self.ble_connected
            time.sleep(0.02)
        return False

    def connect(self):
        with self._lock:
            self._close_connection_unsafe()

            for attempt in range(self._config.reconnect_max_attempts):
                try:
                    self._serial = serial.Serial()
                    self._serial.port = self._config.port
                    self._serial.baudrate = self._config.baudrate
                    self._serial.timeout = 0.1
                    self._serial.write_timeout = 0.5
                    
                    self._serial.dtr = False
                    self._serial.rts = False
                    self._serial.open()
                    self._start_reader_unsafe()

                    return True
                except serial.SerialException:
                    if attempt < self._config.reconnect_max_attempts - 1:
                        time.sleep(self._config.reconnect_delay_seconds)

            return False

    def disconnect(self):
        with self._lock:
            self._close_connection_unsafe()

    def send_keyboard_report(self, modifiers, keys):
        # Packet: [HEADER, modifiers, type=0x00, keys[0..5]]
        padded_keys = (keys + [0] * self.KEYBOARD_KEYS_COUNT)[:self.KEYBOARD_KEYS_COUNT]
        data = bytes([modifiers, 0x00] + padded_keys)
        checksum = self._calc_checksum(data)
        packet = bytes([self.PROTOCOL_HEADER]) + data + bytes([checksum])
        return self._transmit(packet)

    def send_mouse_report(self, buttons, delta_x, delta_y, delta_wheel):
        # Packet: [HEADER, 0x00, type=0x03, buttons, dx_lo, dx_hi, dy_lo, dy_hi, wheel]
        dx = self._to_signed_word(delta_x)
        dy = self._to_signed_word(delta_y)
        data = bytes([
            0x00,
            0x03,
            buttons,
            dx & 0xFF,
            (dx >> 8) & 0xFF,
            dy & 0xFF,
            (dy >> 8) & 0xFF,
            self._to_signed_byte(delta_wheel),
        ])
        checksum = self._calc_checksum(data)
        packet = bytes([self.PROTOCOL_HEADER]) + data + bytes([checksum])
        return self._transmit(packet)

    def _close_connection_unsafe(self):
        self._reader_stop.set()
        reader = self._reader_thread
        if reader is not None and reader is not threading.current_thread():
            # Reads have a 0.1-second timeout. Let the reader leave before
            # pyserial closes and clears its Windows OVERLAPPED handles.
            reader.join()
        self._reader_thread = None
        if self._serial is not None:
            try:
                self._serial.close()
            except (serial.SerialException, OSError):
                pass
            self._serial = None
        self._ble_connected = False
        self._last_status_at = 0.0

    def _start_reader_unsafe(self):
        self._status_parser = StatusParser()
        self._reader_stop = threading.Event()
        serial_connection = self._serial
        self._reader_thread = threading.Thread(
            target=self._read_status_loop,
            args=(serial_connection, self._reader_stop),
            daemon=True,
        )
        self._reader_thread.start()

    def _read_status_loop(self, serial_connection, stop_event):
        while not stop_event.is_set() and serial_connection.is_open:
            try:
                # Wait for one byte when idle; an ACK is only nine bytes and
                # must not wait for a 32-byte read to fill or time out.
                available = serial_connection.in_waiting
                chunk = serial_connection.read(max(1, min(32, available)))
            except (serial.SerialException, OSError):
                if stop_event.is_set():
                    return  # The disconnecting thread owns the ordered close.
                self._ble_connected = False
                try:
                    serial_connection.close()
                except (serial.SerialException, OSError):
                    pass
                return

            if stop_event.is_set():
                return
            status = self._status_parser.feed(chunk, time.monotonic())
            self._ble_connected = status.connected
            self._last_status_at = status.connected_at

    def _transmit(self, data):
        with self._lock:
            if not self.is_connected:
                return False
            try:
                return self._serial.write(data) == len(data)
            except (serial.SerialException, OSError):
                self._close_connection_unsafe()
                return False

    @staticmethod
    def _to_signed_byte(value):
        clamped = max(-127, min(127, int(value)))
        return clamped & 0xFF

    @staticmethod
    def _to_signed_word(value):
        clamped = max(-32768, min(32767, int(value)))
        return clamped & 0xFFFF

    @staticmethod
    def _calc_checksum(data):
        xor_sum = 0
        for byte in data:
            xor_sum ^= byte
        return xor_sum

    @staticmethod
    def find_esp32_port():
        known_chips = ("CP210", "CH34", "FTDI", "USB-SERIAL", "JTAG", "WCH")
        espressif_vids = ("303A",)

        for port_info in serial.tools.list_ports.comports():
            desc = (port_info.description or "").upper()
            hwid = (port_info.hwid or "").upper()
            
            if any(chip in desc for chip in known_chips) or "USB SERIAL DEVICE" in desc:
                return port_info.device
                
            if any(vid in hwid for vid in espressif_vids):
                return port_info.device

        return None


class RawInputCapture(ABC):
    def __init__(self, window_class_name, usage):
        self._window_class_name = (
            f"{window_class_name}_{id(self):x}_{time.monotonic_ns()}"
        )
        self._usage = usage
        self._hwnd = None
        self._thread = None
        self._thread_id = None
        self._ready_event = threading.Event()
        self._failed_event = threading.Event()
        self._failure = None
        self._wndproc_reference = None

    def start(self):
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=2.0)
        return self.is_healthy

    @property
    def is_healthy(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._failed_event.is_set()
        )

    @property
    def failure(self):
        return self._failure

    def stop(self):
        if self._thread_id is not None:
            _user32.PostThreadMessageW(self._thread_id, WindowsMessage.QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @abstractmethod
    def _process_input(self, lparam):
        pass

    def _message_loop(self):
        self._thread_id = _kernel32.GetCurrentThreadId()
        h_instance = _kernel32.GetModuleHandleW(None)
        class_registered = False

        @ctypes.WINFUNCTYPE(
            _LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        def window_procedure(hwnd, msg, wparam, lparam):
            if msg == WindowsMessage.INPUT:
                self._process_input(lparam)
                return 0
            if msg == WindowsMessage.DESTROY:
                _user32.PostQuitMessage(0)
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_reference = window_procedure

        try:
            window_class = _WNDCLASS()
            window_class.lpfnWndProc = window_procedure
            window_class.hInstance = h_instance
            window_class.lpszClassName = self._window_class_name
            if not _user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError()
            class_registered = True

            self._hwnd = _user32.CreateWindowExW(
                0,
                self._window_class_name,
                "",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                h_instance,
                None,
            )
            if not self._hwnd:
                raise ctypes.WinError()

            raw_input_device = _RAWINPUTDEVICE(
                HidUsage.GENERIC_DESKTOP,
                self._usage,
                RawInputFlag.INPUT_SINK,
                self._hwnd,
            )
            if not _user32.RegisterRawInputDevices(
                ctypes.byref(raw_input_device), 1, ctypes.sizeof(_RAWINPUTDEVICE)
            ):
                raise ctypes.WinError()

            self._ready_event.set()

            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except BaseException as exc:
            self._failure = exc
            self._failed_event.set()
            self._ready_event.set()
        finally:
            if self._hwnd:
                _user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if class_registered:
                _user32.UnregisterClassW(self._window_class_name, h_instance)


class RawMouseCapture(RawInputCapture):
    def __init__(self, on_move):
        super().__init__("RawMouseWindow", HidUsage.MOUSE)
        self._on_move = on_move

    def _process_input(self, lparam):
        size = wintypes.UINT(0)
        _user32.GetRawInputData(
            lparam,
            RawInputCommand.INPUT,
            None,
            ctypes.byref(size),
            ctypes.sizeof(_RAWINPUTHEADER),
        )

        if size.value == 0:
            return

        buffer = (ctypes.c_byte * size.value)()
        _user32.GetRawInputData(
            lparam,
            RawInputCommand.INPUT,
            buffer,
            ctypes.byref(size),
            ctypes.sizeof(_RAWINPUTHEADER),
        )

        raw_input = ctypes.cast(buffer, ctypes.POINTER(_RAWINPUT_MOUSE)).contents

        if raw_input.header.dwType == RawInputType.MOUSE:
            delta_x = raw_input.mouse.lLastX
            delta_y = raw_input.mouse.lLastY
            if delta_x or delta_y:
                self._on_move(delta_x, delta_y)


class CursorManager:
    def __init__(self):
        self._saved_position = None
        self._locked = False

    def lock(self):
        if self._locked:
            return
        point = wintypes.POINT()
        if _user32.GetCursorPos(ctypes.byref(point)):
            self._saved_position = (point.x, point.y)
            clip_rect = wintypes.RECT(point.x, point.y, point.x + 1, point.y + 1)
            _user32.ClipCursor(ctypes.byref(clip_rect))
        _user32.ShowCursor(False)
        self._locked = True

    def unlock(self):
        _user32.ClipCursor(None)
        if self._saved_position is not None:
            _user32.SetCursorPos(self._saved_position[0], self._saved_position[1])
            self._saved_position = None
        if self._locked:
            _user32.ShowCursor(True)
            self._locked = False

    @contextmanager
    def locked_context(self):
        self.lock()
        try:
            yield
        finally:
            self.unlock()


class KVMController:
    # Protect short stop/start cycles from reusing an ACK sequence immediately.
    _native_sequence_history = {}

    def __init__(self, config):
        self._config = config
        self._bridge = SerialBridge(config)
        self._state = InputState()
        self._cursor = CursorManager()
        self._is_active = False
        self._is_running = True
        self._exit_requested = False
        self._exit_reason = ""
        self._last_remote_exit_at = 0.0
        self._is_pasting = False
        self._paste_cancel_requested = False
        self._ctrl_physical_down = False
        self._alt_physical_down = False
        self._activation_requested = threading.Event()
        self._return_chord = ReturnChord()
        self._motion_lock = threading.RLock()
        self._pointer = None
        self._native_edges = None
        self._absolute_dirty = False
        self._sequence = 0
        self._last_ack_at = 0.0
        self._unacked_since = 0.0

    def stop(self):
        self._is_running = False
        self._exit_requested = True
        self._activation_requested.set()

    def run(self):
        try:
            if not self._is_running:
                return
            if not self._bridge.connect():
                if self._config.require_ready_on_start:
                    raise RuntimeError("Cannot reconnect after changing iPad placement")
                print(f"[ERROR] Cannot connect to port: {self._config.port}")
                return

            print(f"[OK] Connected to: {self._config.port}")
            if self._config.require_ready_on_start:
                deadline = time.monotonic() + 1.5
                while (self._is_running and self._bridge.is_connected
                       and not self._bridge.absolute_ready and time.monotonic() < deadline):
                    time.sleep(0.02)
                if not self._is_running:
                    return
                if not self._bridge.is_connected or not self._bridge.absolute_ready:
                    raise RuntimeError("iPad absolute channel is not ready after changing placement")
                print("[OK] iPad Bluetooth HID is connected")
            elif self._bridge.wait_for_ble_status():
                print("[OK] iPad Bluetooth HID is connected")
            else:
                print("[INFO] iPad Bluetooth HID is not connected; input stays local")

            while self._is_running:
                if not self._bridge.is_connected:
                    if self._config.require_ready_on_start:
                        raise RuntimeError("ESP32 disconnected after changing iPad placement")
                    print("[INFO] Waiting for ESP32-C3; Windows input remains local")
                    if not self._bridge.connect():
                        time.sleep(self._config.reconnect_delay_seconds)
                        continue
                    print(f"[OK] Reconnected to: {self._config.port}")

                self._state.reset()
                self._sync_keyboard_state()
                if not self._bridge.is_connected:
                    continue
                activation_requested = self._wait_for_activation()

                if (
                    activation_requested
                    and self._is_running
                    and self._bridge.is_connected
                    and self._bridge.ble_connected
                ):
                    self._enter_remote_mode()
                elif activation_requested and not self._bridge.ble_connected:
                    print("[INFO] iPad is disconnected; Windows input remains local")
        except KeyboardInterrupt:
            pass
        finally:
            try:
                self._bridge.disconnect()
            finally:
                self._cursor.unlock()
            print("\n[INFO] Shutdown complete")

    def _wait_for_activation(self):
        toggle_key_name = self._config.toggle_key.name.replace("_", " ").title()
        print(
            f"\n[LOCAL] Move to the {self._config.ipad_side} edge or press '{toggle_key_name}' "
            "to enter iPad mode"
        )

        self._activation_requested.clear()
        listener = keyboard.Listener(on_press=self._handle_activation_key)
        listener.start()

        virtual_left = _user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        virtual_width = _user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        right_edge = virtual_left + virtual_width - 1
        edge_since = None
        edge_armed = False

        try:
            while self._is_running and not self._activation_requested.is_set():
                if not self._bridge.is_connected:
                    return False
                if self._config.require_ready_on_start and not self._bridge.absolute_ready:
                    raise RuntimeError("iPad absolute channel disconnected after changing placement")
                if not listener.is_alive():
                    raise RuntimeError("Activation listener stopped")
                if not self._config.edge_enabled:
                    time.sleep(0.02)
                    continue

                point = wintypes.POINT()
                at_edge = (
                    _user32.GetCursorPos(ctypes.byref(point))
                    and (point.x <= virtual_left + self._config.edge_margin_px
                         if self._config.ipad_side == "left" else
                         point.x >= right_edge - self._config.edge_margin_px)
                )

                now = time.monotonic()
                # Never transfer a Windows drag or a held modifier to iPad.
                held = any(_user32.GetAsyncKeyState(vk) & 0x8000
                           for vk in (1, 2, 4, 5, 6, 16, 17, 18, 91, 92))
                if held:
                    edge_since = None
                    time.sleep(0.01)
                    continue
                if not at_edge:
                    edge_armed = True
                    edge_since = None
                elif (
                    edge_armed
                    and now - self._last_remote_exit_at
                    >= self._config.edge_reentry_cooldown_seconds
                ):
                    if edge_since is None:
                        edge_since = now
                    elif now - edge_since >= self._config.edge_dwell_seconds:
                        self._activation_requested.set()
                else:
                    edge_since = None

                time.sleep(0.01)
        finally:
            listener.stop()
            listener.join(timeout=1.0)

        return self._activation_requested.is_set()

    def _handle_activation_key(self, key):
        if key == self._config.toggle_key:
            self._activation_requested.set()
            return False
        return None

    def _enter_remote_mode(self):
        if self._config.absolute_enabled:
            if not self._bridge.absolute_ready:
                print("[INFO] Absolute firmware is not ready; input stays local")
                time.sleep(0.3)
                return
            point = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            top = _user32.GetSystemMetrics(77)
            height = max(1, _user32.GetSystemMetrics(79) - 1)
            self._pointer = AbsolutePointer(
                x=32112 if self._config.ipad_side == "left" else 655,
                y=max(0, min(32767, (point.y - top) / height * 32767)),
                edge_enabled=self._config.mode != "locked",
                return_edge="right" if self._config.ipad_side == "left" else "left")
            self._absolute_dirty = True
            self._last_ack_at = 0.0
            self._unacked_since = 0.0
            self._native_edges = (NativeEdges(self._pointer, self._config.sensitivity,
                                             self._config.absolute_scale,
                                             self._native_sequence_history)
                                  if self._config.native_edges_enabled else None)
        self._return_chord.reset()
        self._is_active = True
        self._exit_requested = False
        self._exit_reason = ""
        toggle_key_name = self._config.toggle_key.name.replace("_", " ").title()

        print(
            f"\n[REMOTE] Control active "
            f"('{toggle_key_name}' or standalone Ctrl+left Alt = exit)"
        )

        raw_mouse = RawMouseCapture(self._handle_raw_mouse_move)
        keyboard_listener = None
        mouse_listener = None

        try:
            if not raw_mouse.start():
                detail = raw_mouse.failure or "Raw Input did not start"
                print(f"\n[WARNING] Mouse capture unavailable: {detail}")
                return
            with self._cursor.locked_context():
                keyboard_listener = keyboard.Listener(
                    on_press=self._handle_key_press,
                    on_release=self._handle_key_release,
                    suppress=True,
                )
                mouse_listener = mouse.Listener(
                    on_click=self._handle_mouse_click,
                    on_scroll=self._handle_mouse_scroll,
                    suppress=True,
                )
                keyboard_listener.start()
                mouse_listener.start()
                if self._pointer is not None:
                    self._send_mouse_report(0, 0, 0)

                while not self._exit_requested and self._is_running:
                    if not self._bridge.ble_connected:
                        print("\n[WARNING] iPad Bluetooth disconnected; returning to Windows")
                        self._exit_reason = "iPad Bluetooth disconnected"
                        self._exit_requested = True
                        break
                    if self._config.absolute_enabled and not self._bridge.absolute_ready:
                        self._exit_reason = "absolute channel disconnected"
                        break
                    if not keyboard_listener.is_alive() or not mouse_listener.is_alive():
                        self._exit_reason = "input listener stopped"
                        break
                    if not raw_mouse.is_healthy:
                        detail = raw_mouse.failure or "capture thread stopped"
                        print(f"\n[WARNING] Mouse capture failed: {detail}")
                        self._exit_reason = "mouse capture failure"
                        self._exit_requested = True
                        break
                    with self._motion_lock:
                        self._confirm_absolute_edge()
                        self._flush_mouse_movement()
                        timed_out = (self._native_edges.acknowledgment_timed_out(time.monotonic())
                                     if self._native_edges is not None else
                                     self._unacked_since and time.monotonic() - self._unacked_since > 0.8)
                        if timed_out:
                            self._exit_reason = "BLE acknowledgment timed out"
                            self._exit_requested = True
                    time.sleep(0.005)
        finally:
            self._is_active = False
            self._paste_cancel_requested = True
            if keyboard_listener is not None:
                keyboard_listener.stop()
                keyboard_listener.join(timeout=1.0)
            if mouse_listener is not None:
                mouse_listener.stop()
                mouse_listener.join(timeout=1.0)
            raw_mouse.stop()
            self._release_all_remote_inputs()
            self._pointer = None
            self._native_edges = None
            self._park_cursor_inside_windows()
            self._last_remote_exit_at = time.monotonic()
            if self._exit_reason:
                print(f"\n[INFO] Returned to Windows: {self._exit_reason}")

    def _handle_raw_mouse_move(self, delta_x, delta_y):
        if not self._is_active or self._exit_requested:
            return

        if self._pointer is not None:
            with self._motion_lock:
                self._confirm_absolute_edge()
                if self._native_edges is not None:
                    self._native_edges.move(delta_x, delta_y, time.monotonic())
                    self._flush_mouse_movement()
                    return
                scale = self._config.absolute_scale * self._config.sensitivity
                if self._pointer.move(delta_x * scale, delta_y * scale,
                                      time.monotonic(), self._state.mouse_buttons):
                    self._exit_reason = f"iPad {self._pointer.return_edge} edge"
                    self._exit_requested = True
                    return
                self._absolute_dirty = True
                self._flush_mouse_movement()
            return

        sensitivity = self._config.sensitivity
        max_delta = self._config.max_delta

        self._state.accumulated_dx += self._clamp(
            delta_x * sensitivity, -max_delta, max_delta
        )
        self._state.accumulated_dy += self._clamp(
            delta_y * sensitivity, -max_delta, max_delta
        )
        self._flush_mouse_movement()

    def _get_vk_from_key(self, key):
        if hasattr(key, 'vk') and key.vk is not None:
            return key.vk
        if hasattr(key, 'value') and hasattr(key.value, 'vk'):
            return key.value.vk
        return None

    def _handle_key_press(self, key):
        if not self._is_active:
            return

        if key == self._config.toggle_key:
            self._exit_reason = "Scroll Lock"
            self._exit_requested = True
            return

        vk = self._get_vk_from_key(key)
        if vk is None:
            return

        self._return_chord.press(vk)

        if vk in (VK_LCONTROL, VK_RCONTROL):
            self._ctrl_physical_down = True
        if vk in (VK_LMENU, VK_RMENU):
            self._alt_physical_down = True

        if vk == VK_ESCAPE and self._ctrl_physical_down and self._alt_physical_down:
            self._exit_reason = "Ctrl+Alt+Esc"
            self._exit_requested = True
            return

        if self._is_pasting:
            if vk == PASTE_TRIGGER_VK and self._ctrl_physical_down:
                self._paste_cancel_requested = True
            return

        if vk == PASTE_TRIGGER_VK and (self._state.active_modifiers & PASTE_TRIGGER_MODIFIERS):
            self._start_clipboard_paste()
            return

        modifier = VK_TO_MODIFIER.get(vk)
        if modifier is not None:
            self._state.active_modifiers |= modifier
            self._sync_keyboard_state()
            return

        hid_code = VK_TO_HID.get(vk)
        if hid_code:
            self._state.pressed_keys.add(hid_code)
            self._sync_keyboard_state()

    def _handle_key_release(self, key):
        if not self._is_active:
            return

        vk = self._get_vk_from_key(key)
        if vk is None:
            return

        if self._return_chord.release(vk) and self._config.mode != "edge":
            self._exit_reason = "Ctrl+Alt"
            self._exit_requested = True
            return

        if vk in (VK_LCONTROL, VK_RCONTROL):
            self._ctrl_physical_down = False
        if vk in (VK_LMENU, VK_RMENU):
            self._alt_physical_down = False

        if self._is_pasting:
            return

        modifier = VK_TO_MODIFIER.get(vk)
        if modifier is not None:
            self._state.active_modifiers &= ~modifier
            self._sync_keyboard_state()
            return

        hid_code = VK_TO_HID.get(vk)
        if hid_code:
            self._state.pressed_keys.discard(hid_code)
            self._sync_keyboard_state()

    def _handle_mouse_click(self, x, y, button, is_pressed):
        if not self._is_active:
            return

        button_mask = PYNPUT_TO_MOUSE_BUTTON.get(button, 0)

        with self._motion_lock:
            if is_pressed:
                self._state.mouse_buttons |= button_mask
            else:
                self._state.mouse_buttons &= ~button_mask
            if self._native_edges is not None:
                self._native_edges.button(self._state.mouse_buttons, time.monotonic())
                self._confirm_absolute_edge()
                self._flush_native_mouse(force=True)
                return
            if self._pointer is not None:
                self._pointer.move(0, 0, time.monotonic(), self._state.mouse_buttons)
            self._send_mouse_report(0, 0, 0)

    def _handle_mouse_scroll(self, x, y, dx, dy):
        if not self._is_active:
            return

        with self._motion_lock:
            if self._native_edges is not None:
                self._native_edges.scroll(dy, time.monotonic())
                self._confirm_absolute_edge()
                self._flush_mouse_movement()
                return
            self._state.accumulated_wheel += dy
            self._flush_mouse_movement()

    def _sync_keyboard_state(self):
        success = self._bridge.send_keyboard_report(
            self._state.active_modifiers,
            list(self._state.pressed_keys),
        )
        if not success:
            self._handle_connection_lost()

    def _send_mouse_report(self, dx, dy, dw):
        if self._native_edges is not None:
            with self._motion_lock:
                self._flush_native_mouse(force=True)
            return
        if self._pointer is not None:
            x, y = self._pointer.report_position
            self._sequence = (self._sequence + 1) & 255
            success = self._bridge.send_absolute_report(
                self._state.mouse_buttons, x, y, dw, self._sequence)
            if not self._unacked_since:
                self._unacked_since = time.monotonic()
            self._pointer.submitted(self._sequence, x, y, time.monotonic(),
                                    success, self._state.mouse_buttons)
            if not success:
                self._handle_connection_lost()
            self._absolute_dirty = False
            return
        success = self._bridge.send_mouse_report(
            self._state.mouse_buttons, dx, dy, dw
        )
        if not success:
            self._handle_connection_lost()

    def _flush_mouse_movement(self):
        if self._native_edges is not None:
            self._flush_native_mouse()
            return
        now = time.monotonic()
        min_interval = 1.0 / self._config.poll_rate_hz

        if (now - self._state.last_transmission_time) < min_interval:
            return

        if self._pointer is not None:
            wheel = int(self._clamp(self._state.accumulated_wheel, -127, 127))
            if self._absolute_dirty or wheel:
                self._send_mouse_report(0, 0, wheel)
                self._state.accumulated_wheel -= wheel
                self._state.last_transmission_time = now
            return

        dx = int(self._state.accumulated_dx)
        dy = int(self._state.accumulated_dy)
        dw = int(self._state.accumulated_wheel)

        if not (dx or dy or dw):
            return

        send_dx = int(self._clamp(dx, -32768, 32767))
        send_dy = int(self._clamp(dy, -32768, 32767))
        send_dw = int(self._clamp(dw, -127, 127))

        self._state.accumulated_dx -= send_dx
        self._state.accumulated_dy -= send_dy
        self._state.accumulated_wheel -= send_dw

        self._send_mouse_report(send_dx, send_dy, send_dw)
        self._state.last_transmission_time = now

    def _confirm_absolute_edge(self):
        if self._pointer is None:
            return
        status = self._bridge.status
        if status.ack_at > self._last_ack_at:
            self._last_ack_at = status.ack_at
            if self._native_edges is not None:
                self._native_edges.acknowledge(status.ack_sequence, status.ack_x,
                                               status.ack_y, status.ack_buttons,
                                               status.ack_accepted, status.ack_at)
                if self._native_edges.failure:
                    self._exit_reason = self._native_edges.failure
                    self._exit_requested = True
                return
            self._unacked_since = 0.0
            self._pointer.acknowledge(status.ack_sequence, status.ack_x, status.ack_y,
                                      status.ack_buttons, status.ack_accepted, status.ack_at)
            if not status.ack_accepted:
                self._exit_reason = "BLE report rejected"
                self._exit_requested = True

    def _flush_native_mouse(self, force=False):
        native = self._native_edges
        if native is None or self._exit_requested:
            return
        now = time.monotonic()
        if not force and now - self._state.last_transmission_time < 1.0 / self._config.poll_rate_hz:
            return
        report = native.next_report()
        if native.return_requested:
            self._exit_reason = f"iPad {self._pointer.return_edge} edge"
            self._exit_requested = True
            return
        if native.failure:
            self._exit_reason = native.failure
            self._exit_requested = True
            return
        if report is None:
            return
        sequence = None
        if report.channel == "absolute":
            sequence = native.next_sequence(now)
            if sequence is None:
                self._exit_reason = native.failure
                self._exit_requested = True
                return
            success = self._bridge.send_absolute_report(report.buttons, report.x, report.y,
                                                         report.wheel, sequence)
        else:
            success = self._bridge.send_mouse_report(report.buttons, report.x, report.y, report.wheel)
        native.submitted(report, sequence, now, success)
        self._state.last_transmission_time = now
        if not success:
            self._handle_connection_lost()

    def _handle_connection_lost(self):
        if not self._is_active:
            return

        print("\n[WARNING] ESP32 connection lost; returning control to Windows")
        self._exit_reason = "ESP32 USB disconnected"
        self._exit_requested = True

    def _release_all_remote_inputs(self):
        self._state.pressed_keys.clear()
        self._state.active_modifiers = 0
        self._state.mouse_buttons = 0
        self._ctrl_physical_down = False
        self._alt_physical_down = False
        self._bridge.send_keyboard_report(0, [])
        if self._native_edges is not None:
            native = self._native_edges
            # The other collection was released before switching. Replaying an
            # old absolute position during relative cleanup would move the iPad.
            if native.active_channel == "absolute":
                sequence = native.next_sequence(time.monotonic())
                # Cleanup never waits for ACK; exhaustion must not prevent an
                # absolute-button release in its own HID collection.
                self._bridge.send_absolute_report(0, *native.last_absolute, 0,
                                                  sequence if sequence is not None else 0)
            self._bridge.send_mouse_report(0, 0, 0, 0)
            self._state.reset()
            return
        if self._pointer is not None:
            self._send_mouse_report(0, 0, 0)
        self._bridge.send_mouse_report(0, 0, 0, 0)
        self._state.reset()

    @staticmethod
    def _clamp(value, min_val, max_val):
        return max(min_val, min(max_val, value))

    def _park_cursor_inside_windows(self):
        point = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(point)):
            return

        virtual_left = _user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        virtual_width = _user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        right_edge = virtual_left + virtual_width - 1
        if self._config.ipad_side == "left":
            safe_x = max(point.x, virtual_left + self._config.return_cursor_inset_px)
            safe_x = min(right_edge, safe_x)
        else:
            safe_x = min(point.x, right_edge - self._config.return_cursor_inset_px)
            safe_x = max(virtual_left, safe_x)
        _user32.SetCursorPos(safe_x, point.y)

    def _type_clipboard_as_keystrokes(self):
        try:
            self._release_control_modifiers()
            self._clear_keyboard_state()
            text = self._get_clipboard_text()
            if not text:
                return

            normalized = text.replace("\r\n", "\n").replace("\r", "\n")

            for ch in normalized:
                if self._paste_cancel_requested or not self._is_active:
                    break
                key_info = self._translate_char_to_hid(ch)
                if key_info is None:
                    continue
                modifiers, keycode = key_info
                self._send_keypress(modifiers, keycode)
                time.sleep(0.04)
        finally:
            self._is_pasting = False

    def _start_clipboard_paste(self):
        if self._is_pasting:
            return

        self._is_pasting = True
        self._paste_cancel_requested = False
        threading.Thread(target=self._type_clipboard_as_keystrokes, daemon=True).start()

    def _send_keypress(self, modifiers, keycode):
        self._bridge.send_keyboard_report(modifiers, [keycode])
        self._bridge.send_keyboard_report(0, [])

    def _release_control_modifiers(self):
        ctrl_mask = KeyboardModifier.CTRL_LEFT | KeyboardModifier.CTRL_RIGHT
        if self._state.active_modifiers & ctrl_mask:
            self._state.active_modifiers &= ~ctrl_mask
            self._sync_keyboard_state()

    def _clear_keyboard_state(self):
        if self._state.pressed_keys or self._state.active_modifiers:
            self._state.pressed_keys.clear()
            self._state.active_modifiers = 0
        self._bridge.send_keyboard_report(0, [])

    def _translate_char_to_hid(self, ch):
        if ch in CHAR_TO_HID:
            return CHAR_TO_HID[ch]

        if "A" <= ch <= "Z":
            return KeyboardModifier.SHIFT_LEFT, HID_KEY[ch]

        return None

    @staticmethod
    def _get_clipboard_text():
        if not _user32.OpenClipboard(None):
            return ""

        handle = None
        try:
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""

            locked = _kernel32.GlobalLock(handle)
            if not locked:
                return ""

            try:
                size = _kernel32.GlobalSize(handle)
                if not size:
                    return ""
                length = size // ctypes.sizeof(ctypes.c_wchar)
                return ctypes.wstring_at(locked, length).rstrip("\x00")
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()


def main():
    print("=" * 50)
    print("        ESP32 KVM Bridge")
    print("=" * 50)
    config = BridgeConfig()

    detected_port = SerialBridge.find_esp32_port()
    if detected_port:
        print(f"[INFO] ESP32 detected on port: {detected_port}")
        config.port = detected_port
    else:
        print("[WARNING] ESP32 port not automatically detected")

    controller = KVMController(config)
    controller.run()


if __name__ == "__main__":
    main()
