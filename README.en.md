# iPadHub

[中文](README.md) | [English](README.en.md)

iPadHub brings iPad extended display and ESP32-C3 keyboard/mouse control into one Windows x64 application. It combines the MouseLink Python bridge and the iPad互联 C++ display backend behind a shared interface. The first release runs one mode at a time and waits for resource release and process exit before switching.

**Current release: [0.1.0-preview](https://github.com/tukitohosi/ipad-hub/releases/tag/v0.1.0-preview).** This is an integration preview with real backends. Hardware endurance tests and clean Windows installation are still pending.

![iPadHub interface preview](docs/images/home-preview.png)

The screenshot uses an isolated interactive preview with simulated state. It does not show a connected personal device.

## Features and requirements

| Mode | Features | Requirements |
| --- | --- | --- |
| Extended display | USB / Wi-Fi connection, device discovery, manual address and quality presets | OpenDisplay running on the iPad; a working Windows virtual display driver; Apple device services for USB |
| Keyboard and mouse | Control native iPad apps, left / right placement, speed, free / locked mode and optional calibration | Compatible ESP32-C3 connected by USB and paired with the iPad over Bluetooth |
| Device tools | Firmware backup, flashing, recovery and diagnostics | Compatible board; explicit confirmation in the tool window before flashing / recovery |

The home page does not connect automatically. Closing the window minimizes to the tray; quitting stops the backend. A failed resource release blocks mode switching.

## Download

Use [GitHub Releases](https://github.com/tukitohosi/ipad-hub/releases) for the Windows x64 installer, complete portable ZIP, matching source ZIP, SHA-256 lists and validation reports. Extract the whole portable folder before running `iPadHub.exe`.

The installer is unsigned. It does not bundle or install Parsec drivers or change firewall rules. Configure the receiver and display driver separately.

## Data and coexistence

Compatible legacy settings and firmware backups are copied into `%LOCALAPPDATA%\iPadHub` on first launch. Existing new files are not overwritten. The new display registry namespace is `HKCU\Software\iPadHub\Display`; uninstalling keeps the user data by default.

The original MouseLink and iPad互联 applications can remain installed. Quit iPadHub before returning to an original application so that only one application uses each resource.

## Validation and building

Local regression tests, C++ builds, device-free pipe checks, frozen application startup, ZIP CRC and license checks passed. At least 50 real hardware switches, 60-minute USB / Wi-Fi / bridge sessions, disconnect and sleep recovery, firmware operations, clean Windows installation and installation / upgrade / uninstall behavior remain unverified. See the [acceptance record](docs/release-acceptance.md) and release reports.

Building requires Python 3.12, Visual Studio 2022 C++ x64 / CMake / Windows SDK and Inno Setup 6. Python versions are locked by `vendor/mouselink/open_bridge/requirements-desktop-lock.txt`. See [build instructions](docs/packaging.md). Run `python -m ipadhub.app --preview` for a hardware-free, in-memory interface preview.

The release source ZIP remains the exact source delivered with the binary. The repository additionally includes publishing documentation and a build-script alias for `frozen-engines-smoke.json`, identical in content to `engine-smoke.json`.

## License

The integrated product ships corresponding source under [GPL-3.0](LICENSE). MouseLink / esp32-kvm retains MIT terms, the opendisplay-win derivative retains GPL terms, and Qt / PySide6 and other components retain their licenses. Qt DLLs are distributed separately. See [third-party notices](THIRD_PARTY_NOTICES.md) and the bundled `licenses/` directory. This project is not a Parsec compatibility certification.
