# Third-party notices

## KMChris/esp32-kvm

- Source: https://github.com/KMChris/esp32-kvm
- Pinned commit: `99c52bc36128867d2a7ed85417c1043a7d06ed5c`
- License: MIT
- Copyright: Copyright (c) 2026 Krzysztof Mizgała

The pinned upstream source is available at
https://github.com/KMChris/esp32-kvm/tree/99c52bc36128867d2a7ed85417c1043a7d06ed5c . This working copy adds ESP32-C3 build
support, a project-specific BLE name, screen-edge switching, and fail-safe input
release/recovery behavior.

## Absolute HID descriptor reference

The new absolute mouse descriptor uses the standard Generic Desktop Mouse
collection, five buttons, 16-bit absolute X/Y in 0..32767 and a relative wheel.
It was cross-checked against Neradoc/CircuitPython_Absolute_Mouse:
https://github.com/Neradoc/CircuitPython_Absolute_Mouse

Reference copyright: 2017 Dan Halbert for Adafruit Industries; 2021 David Glaude;
2023 Neradoc. License: MIT (the complete MIT terms are also provided in LICENSE).
This project implements the serial and ESP-IDF BLE integration separately; the
reference's USB success does not establish BLE/iPad compatibility.

## Desktop runtime

- PySide6 / Shiboken6 6.11.2 and Qt 6.11.2, Copyright The Qt Company Ltd.
  Used under LGPL-3.0. The LGPL and GPL license texts are in `licenses/`.
  The Qt libraries remain separate DLLs in `_internal` and may be replaced with
  compatible modified versions. Application source and build instructions are
  provided with the project. No restrictions on debugging library modifications
  or reverse engineering for that purpose are imposed by this application.
  Source: https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2
  Qt source: https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.11.2
  Third-party Qt notices: https://doc.qt.io/qt-6/licenses-used-in-qt.html
- Python 3.12.10: Python Software Foundation License.
  https://www.python.org/downloads/release/python-31210/
- pynput 1.8.2: LGPL-3.0, Copyright Moses Palmér.
  https://github.com/moses-palmer/pynput
- pyserial 3.5: BSD-3-Clause, Copyright Chris Liechti.
  https://github.com/pyserial/pyserial
- six 1.17.0: MIT, Copyright Benjamin Peterson.
  https://github.com/benjaminp/six
- PyInstaller 6.22.3: GPL-2.0-or-later with the bootloader exception permitting
  distribution of bundled applications under their own terms.
  https://pyinstaller.org/en/stable/license.html

## Firmware flashing helper

- esptool 5.4.0, Copyright Espressif Systems: GPL-2.0-or-later.
  https://github.com/espressif/esptool/tree/v5.4.0
  `MouseLinkFlash.exe` and its integration source `flash_helper.py` are distributed
  under GPL-2.0-or-later. Complete esptool Python source and flash stubs are included
  in `licenses/esptool-5.4.0-source.zip`; integration source and build scripts
  are included in the matching MouseLink source archive.
- esp-pylib and other flash runtime dependencies: installed versions are listed
  in `licenses/flash-runtime.json`, with license texts alongside it.
- Inno Setup is used only to build the installer. It is not required on users'
  computers. Source: https://github.com/jrsoftware/issrc
