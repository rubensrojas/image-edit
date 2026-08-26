from __future__ import annotations

import sys
from pathlib import Path

import edit_sre


def test_cli_keeps_existing_output_naming(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    source.touch()
    output = tmp_path / "output"
    written: list[Path] = []

    monkeypatch.setattr(edit_sre, "collect_files", lambda _path: [source])
    monkeypatch.setattr(
        edit_sre,
        "edit_image",
        lambda _input, output_path, _number: written.append(output_path),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["edit_sre.py", str(source), "--output", str(output), "--sre-number", "27"],
    )

    edit_sre.main()

    assert written == [output / "photo.png"]
