# MouseLink

**Use your Windows keyboard and mouse on an iPad through an ESP32-C3 USB-to-Bluetooth HID bridge.**

[简体中文](README.zh-CN.md) · [Repository](https://github.com/tukitohosi/mouselink)

MouseLink captures keyboard and mouse input on Windows, sends it over USB to an ESP32-C3, and presents that board to the iPad as a Bluetooth keyboard and mouse. It controls the iPad's own interface; no iPad app, screen streaming, or shared Wi-Fi network is required. AssistiveTouch does not need to be enabled.

```text
Windows keyboard and mouse → MouseLink → USB → ESP32-C3 → Bluetooth HID → iPad
```

## Current version and limitations

The current release is **1.0.3**: pointer-position checks are optional, iPad placement can be set to either side, and placement can be changed while running. The user accepted this version on their device; keep the 1.0.2 installer for rollback. This update uses the original v3 firmware and needs no board reflash. It combines absolute positioning inside the screen with relative movement for supported edge interactions. Normal use has no session time limit.

For source development, close the running MouseLink and open `启动MouseLink开发版.cmd` after setting up the local Python environment. It uses separate settings and logs in `test-results/placement-1.0.3/app-data`, copying existing preferences only on first launch and leaving installed data unchanged. Input capture does not start automatically.

**Known issue:** the Dock may occasionally fail to appear or may close while you try to leave an app. Move the pointer back into the screen and try the bottom-edge action again. This version does not claim to reproduce every behavior of a directly connected Bluetooth mouse.

The user accepted 1.0.3 on their device. Earlier entry, app-click and left-edge return checks used Windows 11 x64 and an iPad Pro M5 (2025) running iPadOS 26.6.1. This does not establish clean-machine compatibility, compatibility with every iPad, or complete repeated testing of every mode, orientation and system edge gesture.

## What you need

| Item | Requirement |
| --- | --- |
| Computer | Windows x64 with the keyboard and mouse you want to share; tested on Windows 11 |
| Bridge board | ESP32-C3 SuperMini with native USB and 4 MB flash |
| Cable | USB data cable between the board and the Windows computer |
| iPad | Bluetooth enabled, unlocked, and able to use a Bluetooth mouse |
| Windows software | MouseLink installer or portable folder, or a local build from this source |

The built-in flasher targets the board listed above. Boards connected through a CH340/CP210x USB-to-serial adapter, other ESP32 models, and other flash sizes are outside its supported hardware selection. Windows does not need its own Bluetooth adapter for this connection: the ESP32-C3 provides Bluetooth to the iPad.

## Install and start

This Git repository contains source code only. Download the Windows installer or portable archive from the [v1.0.3 release](https://github.com/tukitohosi/mouselink/releases/tag/v1.0.3); the bundled firmware is included in the app packages.

1. Run `MouseLink-1.0.3-Setup.exe`, or extract `MouseLink-1.0.3-Windows-x64.zip` and run `MouseLink.exe`. Keep the portable folder, including `_internal`, together.
2. Connect the ESP32-C3 to Windows with a USB data cable. For a new board, use the firmware steps below first.
3. On the iPad, open **Settings → Bluetooth** and pair with **MouseLink-iPad**. Keep the iPad unlocked.
4. In 1.0.3, choose **iPad 在电脑左侧** (iPad on the left) or **iPad 在电脑右侧** (iPad on the right). The choice is saved; the initial default is right.
5. Choose a switching mode and click **开启键鼠桥** (Start bridge) once the connection is ready. **检查光标位置** (Check pointer position) remains an optional troubleshooting tool, including on first use or after changing boards.

The packaged application includes its Python runtime and flashing tools. You do not need to install Python, Arduino, PlatformIO, or an iPad app for everyday use or built-in firmware flashing. The current application interface is in Chinese.

## Using the bridge

Move the Windows pointer to the edge on the selected side and pause briefly to enter the iPad. With multiple monitors, use the outermost edge of the entire Windows desktop. Stop the bridge before changing its mode, return-hotkey setting, or pointer speed.

| iPad placement | Enter the iPad | Return in free mode |
| --- | --- | --- |
| Right of the computer (default) | Windows right edge → iPad left edge | Reach the iPad left edge, then continue moving left |
| Left of the computer | Windows left edge → iPad right edge | Reach the iPad right edge, then continue moving right |

Placement can be changed while running: MouseLink releases input, returns control to Windows, then resumes local standby with the new direction. Input is captured only after you enter the iPad again. You can stop during the switch; repeated choices use the latest selection. Stopping, closing, opening the firmware page, or failed reconnection cancels automatic resumption and preserves the new setting. Switching sides does not extend a diagnostic session's time limit.

| Mode | Return to Windows |
| --- | --- |
| **自由切换** — Free switching | Continue outward at the iPad edge facing the computer, as shown above |
| Free switching with **快捷键返回** enabled | Either the corresponding edge or `Ctrl + Left Alt` |
| **锁定在 iPad** — Stay on iPad | `Ctrl + Left Alt`; reaching an edge does not return to Windows |

- Release both keys after pressing `Ctrl + Left Alt`; the return action occurs when they are released. The shortcut always uses the physical left Alt key, regardless of iPad placement. In free mode, its optional hotkey switch is off by default on a fresh installation.
- `Scroll Lock` and `Ctrl + Alt + Esc` remain emergency return shortcuts.
- Holding a mouse button while dragging prevents automatic edge return. Release it before moving toward the computer again.
- For bottom-edge actions, move the pointer to the bottom and continue moving downward with the mouse button released. The current mixed implementation can expose iPad system actions, subject to the Dock limitation above. After an edge action, move back into the screen before continuing normal pointer work or returning to Windows.
- The Windows key maps to iPad Command; Alt maps to Option.
- Closing MouseLink stops the bridge and releases captured input. USB or Bluetooth loss also triggers input release. The application does not start automatically at boot or when the board is inserted.

## Flash or restore the board

1. Open **一键刷机** (Firmware). Opening this page stops the bridge so the flasher can use the USB connection.
2. Select the supported board, then click **备份并刷入** (Back up and flash). If multiple boards are connected, select the intended one explicitly.
3. Wait for the backup, write verification, restart, and communication check. Keep USB connected throughout. Closing the window during writing waits for the operation to finish.
4. Pair or reconnect **MouseLink-iPad** in the iPad's Bluetooth settings. After a full initialization, you may need to forget the previous Bluetooth entry and pair again.

The flasher checks the chip, flash size, device identity, and firmware package hashes before writing. It saves and verifies a complete backup first. A recognized compatible installation updates the application area and retains pairing data. A blank board or unrecognized firmware uses full initialization, which replaces its existing contents.

**恢复备份…** (Restore backup) accepts the `backup.json` created by MouseLink and restores only to the same board. Restoration also backs up the board's current contents first. If download mode cannot be entered automatically, hold **BOOT**, briefly press **Reset**, then release **BOOT** and retry. After flashing, the bridge remains stopped until you start it yourself.

## Troubleshooting and local data

- **No board:** check that the USB cable carries data, try another USB port, and refresh the device list.
- **No iPad pointer:** unlock the iPad, check the **MouseLink-iPad** Bluetooth connection, and run the pointer-position check. AssistiveTouch is not a prerequisite.
- **No pointer after a firmware change or re-pairing:** one observed recovery was to turn Bluetooth off in iPad Settings, disconnect the board's USB power for about 10 seconds, reconnect it, and turn Bluetooth back on. This is a recovery step, not a confirmed diagnosis of the cause.
- **Dock closes or an exit action fails:** return the pointer into the screen and retry. This update does not resolve that existing intermittent issue.
- **Need to recover Windows input:** use an emergency return shortcut or close MouseLink.

Settings and bridge logs are stored in `%LOCALAPPDATA%\MouseLink`; firmware backups and flashing logs are in its `firmware-backups` and `flash-logs` folders. Upgrading and the default uninstall retain these files. Do not publish your backups, device identifiers, or local logs with a source contribution.

## Development

The desktop application is Python with PySide6. Windows input capture uses pynput, USB serial communication uses pyserial, the ESP32-C3 firmware uses ESP-IDF through PlatformIO, and the integrated flashing helper uses esptool. PyInstaller creates the Windows application; Inno Setup creates its installer.

| Tool or dependency | Version used by this project |
| --- | --- |
| Python | 3.12.10, Windows x64 |
| PySide6-Essentials / Qt | 6.11.2 |
| pynput | 1.8.2 |
| pyserial | 3.5 |
| esptool | 5.4.0 |
| PyInstaller | 6.22.3 |
| PlatformIO Core | 6.2.0 |
| PlatformIO Espressif32 platform | 7.1.3 |
| ESP-IDF | 6.1.0 |
| Inno Setup | 7.1.0 |

Direct desktop dependencies are pinned in `open_bridge/requirements-desktop.txt`; the complete build environment is recorded in `open_bridge/requirements-desktop-lock.txt`. Firmware configuration is in `open_bridge/platformio.ini` and the accompanying `sdkconfig` files.

The repository keeps MouseLink application and firmware source, tests, build scripts, documentation, and required license notices. Generated firmware and installers, runtimes, caches, device-specific calibration, logs, backups, historical research, and unrelated complete upstream repositories are excluded from Git. The public calibration template contains no private device identity.

From the repository root, use PowerShell with Python 3.12 installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\open_bridge\requirements-desktop-lock.txt platformio==6.2.0
.\tools\Build-Firmware.ps1 -Python .\.venv\Scripts\python.exe -PublishBundle .\open_bridge\firmware
.\.venv\Scripts\python.exe .\tools\Collect-Licenses.py
.\tools\Build-Desktop.ps1 -Python .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

The first firmware build downloads the pinned PlatformIO/ESP-IDF toolchain. The build script stages firmware source in an isolated ASCII-path build directory; pass `-BuildRoot C:\MouseLinkBuild` if you need to choose that directory. `-PublishBundle` copies the bootloader, partition table, and application from the same build and generates their size/SHA-256 manifest. Omitting it builds firmware without replacing the local package. Never combine components from different builds. Newly compiled firmware still needs hardware validation; successful compilation and hash checks do not establish iPad behavior.

The current source builds to `release/1.0.3/MouseLink/MouseLink.exe`. To create an installer, install Inno Setup 7.1.0 and run the included build script, adjusting the compiler path if needed:

```powershell
.\tools\Build-Installer.ps1 -CompilerPath "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
```

The installer is written under `release/1.0.3/`. Keep these generated outputs out of Git. Firmware-dependent tests require the generated package; UI previews, automated tests, user hardware acceptance and clean-machine verification are separate checks.

## Attribution and licenses

MouseLink is based on [KMChris/esp32-kvm](https://github.com/KMChris/esp32-kvm), pinned to commit `99c52bc36128867d2a7ed85417c1043a7d06ed5c`. It adds ESP32-C3 support, the MouseLink desktop interface, switching behavior, absolute-position transport, and integrated backup/flashing workflows. The absolute HID descriptor was cross-checked against [Neradoc/CircuitPython_Absolute_Mouse](https://github.com/Neradoc/CircuitPython_Absolute_Mouse).

Keep the upstream copyright and MIT license notices. The flashing helper and its esptool integration have GPL-2.0-or-later terms; Qt/PySide6, pynput, and other dependencies retain their own licenses. See [LICENSE](open_bridge/LICENSE), [THIRD_PARTY_NOTICES.md](open_bridge/THIRD_PARTY_NOTICES.md), and the license texts under `open_bridge/licenses/` for the component-specific notices.
