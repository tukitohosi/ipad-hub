"""Create portable and source archives from explicit project paths."""
import hashlib
import json
import zipfile
import sys
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open_bridge"))
from app_version import APP_VERSION
RELEASE = ROOT / "release" / APP_VERSION
APP = RELEASE / "MouseLink"


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


manifest = {"version": APP_VERSION, "files": {}}
for path in sorted(APP.rglob("*")):
    if path.is_file() and path.name != "manifest.json":
        assert path.resolve().is_relative_to(APP.resolve())
        manifest["files"][path.relative_to(APP).as_posix()] = sha256(path)
(APP / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

portable = RELEASE / f"MouseLink-{APP_VERSION}-Windows-x64.zip"
with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(APP.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(RELEASE))
with zipfile.ZipFile(portable) as archive:
    assert archive.testzip() is None

source_archive = RELEASE / f"MouseLink-{APP_VERSION}-source.zip"
source_export = runpy.run_path(str(ROOT / "tools/Export-Source.py"))
source_export["write_source_archive"](source_archive, ROOT)

items = [RELEASE / f"MouseLink-{APP_VERSION}-Setup.exe", portable, source_archive,
         APP / "MouseLink.exe", APP / "MouseLinkFlash.exe"]
assert all(path.is_file() for path in items), "Build the installer before packaging the release."
(RELEASE / "SHA256.txt").write_text(
    "\n".join(f"{sha256(p)}  {p.relative_to(RELEASE).as_posix()}" for p in items) + "\n", encoding="utf-8")
for path in items:
    print(path.name, f"{path.stat().st_size / 1024**2:.1f} MiB", sha256(path))
