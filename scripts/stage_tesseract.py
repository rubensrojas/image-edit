#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def discover_source(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if sys.platform == "darwin":
        result = subprocess.run(
            ["brew", "--prefix", "tesseract"], check=True, capture_output=True, text=True
        )
        return Path(result.stdout.strip())
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR",
            Path("C:/Program Files/Tesseract-OCR"),
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
    executable = shutil.which("tesseract")
    if executable:
        return Path(executable).resolve().parent.parent
    raise RuntimeError("Could not locate Tesseract. Pass --source explicitly.")


def copy_if_present(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def stage(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    if sys.platform == "win32":
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir() and item.name.lower() == "tessdata":
                target.mkdir()
                for name in ("eng.traineddata", "osd.traineddata"):
                    copy_if_present(item / name, target / name)
            elif item.is_file() and (
                item.suffix.lower() in {".exe", ".dll"} or "license" in item.name.lower()
            ):
                shutil.copy2(item, target)
    else:
        executable = source / "bin" / "tesseract"
        if not executable.exists():
            executable = Path(shutil.which("tesseract") or "")
        copy_if_present(executable, destination / "bin" / "tesseract")
        tessdata = source / "share" / "tessdata"
        for name in ("eng.traineddata", "osd.traineddata"):
            copy_if_present(tessdata / name, destination / "share" / "tessdata" / name)
        for name in ("LICENSE", "COPYING"):
            copy_if_present(source / name, destination / name)

    executable = destination / ("tesseract.exe" if sys.platform == "win32" else "bin/tesseract")
    traineddata = destination / ("tessdata/eng.traineddata" if sys.platform == "win32" else "share/tessdata/eng.traineddata")
    if not executable.exists() or not traineddata.exists():
        raise RuntimeError(f"Incomplete Tesseract staging from {source}")
    if sys.platform != "win32":
        executable.chmod(0o755)
    print(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="Tesseract installation prefix")
    parser.add_argument("--destination", default=str(ROOT / "build" / "tesseract"))
    args = parser.parse_args()
    stage(discover_source(args.source), Path(args.destination).resolve())


if __name__ == "__main__":
    main()
