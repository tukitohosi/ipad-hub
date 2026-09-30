"""Export only the current MouseLink source, with no device-specific settings."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

# Deliberately list files rather than copying whole working directories.
SOURCE_FILES = (
    ".gitattributes", ".gitignore", "CHANGELOG.md", "LICENSE",
    "README.md", "README.zh-CN.md", "启动MouseLink.cmd", "启动MouseLink开发版.cmd",
    "open_bridge/absolute_diagnostic.py", "open_bridge/absolute_protocol.py",
    "open_bridge/app_settings.py", "open_bridge/app_version.py", "open_bridge/bridge.py",
    "open_bridge/desktop_app.py", "open_bridge/device-calibration.json",
    "open_bridge/firmware_bundle.py", "open_bridge/flash_dialog.py", "open_bridge/flash_helper.py",
    "open_bridge/hid_diagnostic.py", "open_bridge/native_edges.py",
    "open_bridge/pointer_boundary.py", "open_bridge/return_hotkey.py", "open_bridge/trial_bridge.py",
    "open_bridge/CMakeLists.txt", "open_bridge/platformio.ini", "open_bridge/sdkconfig.defaults",
    "open_bridge/sdkconfig.defaults.esp32c3", "open_bridge/sdkconfig.esp32-c3-supermini",
    "open_bridge/requirements-desktop.txt", "open_bridge/requirements-desktop-lock.txt",
    "open_bridge/README.md", "open_bridge/使用说明.txt", "open_bridge/LICENSE",
    "open_bridge/THIRD_PARTY_NOTICES.md", "open_bridge/assets/mouselink.ico",
    "open_bridge/firmware/manifest.json",
    "open_bridge/main/CMakeLists.txt", "open_bridge/main/Kconfig.projbuild",
    "open_bridge/main/include/app_config.h", "open_bridge/main/include/ble_hid_api.h",
    "open_bridge/main/include/ble_hid_internal.h", "open_bridge/main/include/hid_reports.h",
    "open_bridge/main/include/uart_proto.h", "open_bridge/main/src/app_main.c",
    "open_bridge/main/src/ble_hid_api.c", "open_bridge/main/src/ble_hid_gatt.c",
    "open_bridge/main/src/hid_report_transport.c", "open_bridge/main/src/uart_proto.c",
    "tests/test_bridge_protocol.py", "tests/test_firmware_build.py", "tests/test_firmware_flash.py",
    "tests/test_native_desktop.py", "tests/test_native_edges.py", "tests/test_native_trial.py",
    "tests/test_placement_controller.py", "tests/test_placement_desktop.py", "tests/test_side_switch_worker.py",
    "tests/test_pointer_boundary.py", "tests/test_serial_reader.py", "tests/test_source_export.py",
    "tests/test_unified_modes.py", "tests/test_uart_proto.c",
    "tools/Build-Desktop.ps1", "tools/Build-Firmware.ps1", "tools/Build-Installer.ps1",
    "tools/build_firmware_bundle.py", "tools/Collect-Licenses.py", "tools/Export-Source.py",
    "tools/MouseLink.spec", "tools/MouseLink.iss", "tools/Package-Release.py",
    "tools/check_desktop_101.py",
    "tools/render_desktop_101.py", "tools/render_placement.py", "tools/Test-UartProtocol.ps1",
    "tools/release-data/device-calibration.json", "open_bridge/licenses/flash-runtime.json",
)

LICENSE_TEXTS = (
    "bitarray-LICENSE.txt", "bitstring-LICENSE.txt", "cffi-LICENSE.txt",
    "click-LICENSE.txt.txt", "colorama-LICENSE.txt.txt", "cryptography-LICENSE.APACHE.txt",
    "cryptography-LICENSE.BSD.txt", "cryptography-LICENSE.txt", "esp-pylib-LICENSE.txt",
    "esptool-LICENSE-APACHE.txt", "esptool-LICENSE-MIT.txt", "esptool-LICENSE.txt",
    "GPL-3.0-only.txt", "intelhex-LICENSE.txt.txt", "LGPL-3.0-only.txt",
    "markdown-it-py-LICENSE.markdown-it.txt", "markdown-it-py-LICENSE.txt", "mdurl-LICENSE.txt",
    "pycparser-LICENSE.txt", "Pygments-LICENSE.txt", "pyinstaller-COPYING.txt", "pynput-COPYING.LGPL",
    "pyserial-LICENSE.txt", "Python-LICENSE.txt", "PyYAML-LICENSE.txt", "reedsolo-LICENSE.txt",
    "rich-click-LICENSE.txt", "rich-LICENSE.txt", "six-LICENSE", "websockets-LICENSE.txt",
)
SOURCE_FILES += tuple("open_bridge/licenses/" + name for name in LICENSE_TEXTS)
EMPTY_SETTINGS = {"open_bridge/device-calibration.json", "tools/release-data/device-calibration.json"}


def source_entries(root=ROOT):
    """Read a complete source snapshot before writing any export output."""
    root = Path(root).resolve()
    entries = []
    for name in sorted(SOURCE_FILES):
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"Source must be a regular file inside the project: {name}")
        if not path.is_file():
            raise FileNotFoundError(f"Required source file is missing: {name}")
        data = b"{}\n" if name in EMPTY_SETTINGS else path.read_bytes()
        if name != "open_bridge/assets/mouselink.ico":
            data.decode("utf-8-sig")  # Refuse accidentally selected binary files.
        entries.append((name, data))
    return entries


def export_source(output, root=ROOT):
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("The source export destination must be a new or empty directory.")
    entries = source_entries(root)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in entries:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    return len(entries)


def write_source_archive(output, root=ROOT):
    entries = source_entries(root)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("The source archive failed its integrity check.")
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New or empty directory for the public source tree")
    args = parser.parse_args()
    count = export_source(args.output)
    print(f"Exported {count} source files to {args.output.resolve()}")


if __name__ == "__main__":
    main()
