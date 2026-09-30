# A GUI and a hidden console helper share one runtime directory.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
)

root = Path(SPECPATH).parent
src = root / "open_bridge"
sys.path.insert(0, str(src))
from app_version import APP_VERSION

numbers = tuple(int(part) for part in APP_VERSION.split(".")) + (0,)

def version_info(name):
    return VSVersionInfo(ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f,
        flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=[
        StringFileInfo([StringTable("040904B0", [StringStruct("CompanyName", "MouseLink"),
            StringStruct("FileDescription", "MouseLink keyboard and mouse bridge"),
            StringStruct("FileVersion", APP_VERSION), StringStruct("ProductVersion", APP_VERSION),
            StringStruct("ProductName", "MouseLink"), StringStruct("OriginalFilename", name + ".exe")])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])])])

common_data = [(str(root / "tools/release-data/device-calibration.json"), "."), (str(src / "firmware"), "firmware")]
gui = Analysis([str(src / "desktop_app.py")], pathex=[str(src)], datas=common_data,
    hiddenimports=["pynput.keyboard._win32", "pynput.mouse._win32"], excludes=["esptool"], noarchive=False)
helper = Analysis([str(src / "flash_helper.py")], pathex=[str(src)],
    datas=collect_data_files("esptool") + copy_metadata("esptool") + copy_metadata("esp-pylib"),
    excludes=["PySide6", "pynput"], noarchive=False)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="MouseLink",
    console=False, icon=str(src / "assets/mouselink.ico"), version=version_info("MouseLink"), upx=False)
helper_exe = EXE(PYZ(helper.pure), helper.scripts, [], exclude_binaries=True, name="MouseLinkFlash",
    console=True, icon=str(src / "assets/mouselink.ico"), version=version_info("MouseLinkFlash"), upx=False)
bundle = COLLECT(gui_exe, helper_exe, gui.binaries, gui.datas, helper.binaries, helper.datas,
    strip=False, upx=False, name="MouseLink")
