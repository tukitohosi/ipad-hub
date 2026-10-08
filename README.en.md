# iPadHub

[简体中文](README.md) | English

**iPad extended display and keyboard/mouse control in one Windows app.**

Use an iPad as a second screen over USB or Wi-Fi, or control its native apps with your PC's keyboard and mouse through an ESP32-C3 bridge.

Current release: **0.1.0-preview**. [Download](https://github.com/tukitohosi/ipad-hub/releases/tag/v0.1.0-preview)

![iPadHub interface preview](docs/images/home-preview.png)

## Features

| Mode | Features | Requirements |
| --- | --- | --- |
| Extended display | USB and Wi-Fi, device discovery, quality presets | OpenDisplay on the iPad and a Windows virtual display driver; Apple device services for USB |
| Keyboard and mouse | Native iPad app control, left/right placement, free/locked switching and speed adjustment | A compatible ESP32-C3 connected to the PC by USB and paired with the iPad |
| Device tools | Firmware backup, flashing and restoration | A compatible board and USB data cable |

The current release runs one mode at a time. Switching stops the current mode and releases its devices before starting the next.

## Download and use

Get the Windows x64 installer or portable ZIP from the [release page](https://github.com/tukitohosi/ipad-hub/releases/tag/v0.1.0-preview). Extract the entire portable folder before opening iPadHub.exe. The release also includes the matching source ZIP and checksum lists.

Install the display driver and iPad receiver separately; the installer does not change firewall rules.

The home page does not connect automatically or capture input. Closing the window minimizes to the tray; quitting stops the backend. Press **Ctrl + Alt + Esc** to return keyboard and mouse control to Windows in an emergency.

## Data and limitations

Compatible legacy settings and firmware backups are copied into the current user's LocalAppData/iPadHub folder. MouseLink and iPad互联 can remain installed; quit iPadHub before using them with the same devices. Uninstalling keeps user data by default.

- This integration preview cannot run both modes at once.
- OpenDisplay must stay in the foreground for extended display.
- Keyboard/mouse control requires the compatible ESP32-C3; the PC's own Bluetooth is not a substitute.
- Keep USB connected during firmware operations.

## Development and license

See the [build instructions](docs/packaging.md). The combined product provides source under [GPL-3.0](LICENSE); components retain their original licenses. See [third-party notices](THIRD_PARTY_NOTICES.md).
