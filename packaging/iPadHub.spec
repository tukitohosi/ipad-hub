"""One-folder distribution: replaceable Qt DLLs and a separate flash helper."""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

root = Path(SPECPATH).parent
bridge = root / "vendor/mouselink/open_bridge"
version = os.environ.get("IPADHUB_BUILD_VERSION", "0.1.0-preview")
numbers = tuple(int(x) for x in version.split("-")[0].split("+")[0].split(".")) + (0,)
display = root / "build/ipadhub-display/Release/iPadHubDisplay.exe"
if not display.is_file():
    raise RuntimeError("Build iPadHubDisplay.exe before packaging the unified application")


def version_info(name):
    return VSVersionInfo(ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f,
        flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=[
        StringFileInfo([StringTable("040904B0", [StringStruct("CompanyName", "iPadHub"),
            StringStruct("FileDescription", "iPadHub companion application"),
            StringStruct("FileVersion", version), StringStruct("ProductVersion", version),
            StringStruct("ProductName", "iPadHub"), StringStruct("OriginalFilename", name + ".exe")])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])])])


common_data = [(str(root / "ipadhub/assets"), "ipadhub/assets"),
               (str(bridge / "firmware"), "firmware"),
               (str(bridge / "firmware"), "bridge/firmware")]
# Also retain the bridge's Python modules as replaceable files. The runtime
# imports this dedicated directory; it never loads an old installed program.
common_data += [(str(path), "bridge") for path in sorted(bridge.glob("*.py"))]
gui = Analysis([str(root / "packaging/entry.py")], pathex=[str(root), str(bridge)],
    datas=common_data, binaries=[(str(display), "engines")],
    hiddenimports=["ipadhub.engines.bridge", "desktop_app", "flash_dialog", "firmware_bundle",
                   "bridge", "pynput.keyboard._win32", "pynput.mouse._win32", "PySide6.QtSvg"],
    excludes=["esptool"], noarchive=False)
helper = Analysis([str(bridge / "flash_helper.py")], pathex=[str(bridge)],
    datas=collect_data_files("esptool") + copy_metadata("esptool") + copy_metadata("esp-pylib"),
    excludes=["PySide6", "pynput"], noarchive=False)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="iPadHub",
    console=False, icon=str(root / "packaging/iPadHub.ico"), version=version_info("iPadHub"), upx=False)
helper_exe = EXE(PYZ(helper.pure), helper.scripts, [], exclude_binaries=True, name="MouseLinkFlash",
    console=True, icon=str(root / "packaging/iPadHub.ico"), version=version_info("MouseLinkFlash"), upx=False)
COLLECT(gui_exe, helper_exe, gui.binaries, gui.datas, helper.binaries, helper.datas,
    strip=False, upx=False, name="iPadHub")
