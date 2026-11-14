"""Read physical dimensions from the monitor attached to a particular Windows output."""
import ctypes
from ctypes import wintypes
import winreg


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("name", wintypes.WCHAR * 32),
                ("description", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                ("interface_id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]


def dimensions_from_edid(edid, pixels):
    if (len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00"
            or sum(edid[:128]) % 256):
        return None
    size = None
    for offset in range(54, 126, 18):
        timing = edid[offset:offset + 18]
        if timing[:2] == b"\0\0":
            continue
        width = timing[12] | ((timing[14] & 0xf0) << 4)
        height = timing[13] | ((timing[14] & 0x0f) << 8)
        if width >= 10 and height >= 10:
            size = (width, height)
            break
    if not size and edid[21] and edid[22]:
        size = (edid[21] * 10, edid[22] * 10)
    if size and (size[0] > size[1]) != (pixels[0] > pixels[1]):
        size = size[::-1]
    return size


def active_screen_mm(device_name, pixels):
    user = ctypes.WinDLL("user32")
    enum = user.EnumDisplayDevicesW
    enum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DisplayDevice), wintypes.DWORD]
    enum.restype = wintypes.BOOL
    attached = []
    for index in range(64):
        display = _DisplayDevice()
        display.size = ctypes.sizeof(display)
        if not enum(device_name, index, ctypes.byref(display), 1):
            break
        if display.flags & 1:
            attached.append(display.interface_id)
    # In clone mode the same rectangle can be shown on different sized panels.
    if len(attached) != 1:
        return None
    parts = attached[0].split("#")
    if len(parts) != 4 or parts[0].upper() != r"\\?\DISPLAY":
        return None
    path = "\\".join(("SYSTEM", "CurrentControlSet", "Enum", "DISPLAY", parts[1],
                      parts[2], "Device Parameters"))
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            edid, kind = winreg.QueryValueEx(key, "EDID")
    except OSError:
        return None
    return dimensions_from_edid(edid, pixels) if kind == winreg.REG_BINARY else None
