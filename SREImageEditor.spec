from __future__ import annotations

import os
import sys
from pathlib import Path


project_root = Path(SPEC).resolve().parent
tesseract_root_value = os.environ.get("TESSERACT_BUNDLE_DIR", "")
tesseract_root = Path(tesseract_root_value).resolve() if tesseract_root_value else None

datas = [
    (str(project_root / "resources" / "app_icon.svg"), "resources"),
    (str(project_root / "resources" / "Roboto-Regular.ttf"), "resources"),
]
binaries = []

if tesseract_root and tesseract_root.is_dir():
    for source in tesseract_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(tesseract_root)
        destination = str(Path("tesseract") / relative.parent)
        is_native = (
            source.name.lower() in {"tesseract", "tesseract.exe"}
            or source.suffix.lower() in {".dll", ".dylib", ".so"}
        )
        (binaries if is_native else datas).append((str(source), destination))
else:
    print("WARNING: TESSERACT_BUNDLE_DIR is not set; the build will require Tesseract on PATH.")

icon_suffix = ".icns" if sys.platform == "darwin" else ".ico"
icon_candidate = project_root / "build" / f"app_icon{icon_suffix}"
icon = str(icon_candidate) if icon_candidate.exists() else None

a = Analysis(
    [str(project_root / "app.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SRE Image Editor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)
collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SRE Image Editor",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="SRE Image Editor.app",
        icon=icon,
        bundle_identifier="com.sretools.imageeditor",
        info_plist={
            "CFBundleDisplayName": "SRE Image Editor",
            "NSHighResolutionCapable": True,
        },
    )
