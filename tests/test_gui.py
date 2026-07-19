from __future__ import annotations

from pathlib import Path

from PIL import Image

import app
from app import BatchWorker, MainWindow, PaddedSpinBox


def make_image(path: Path) -> Path:
    Image.new("RGB", (32, 32), "black").save(path)
    return path


def test_padded_spin_box(qtbot) -> None:
    control = PaddedSpinBox()
    qtbot.addWidget(control)
    control.setRange(0, 999)
    for value, expected in ((0, "000"), (10, "010"), (27, "027"), (999, "999")):
        control.setValue(value)
        assert control.text() == expected


def test_window_adds_supported_files_and_ignores_duplicates(qtbot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    image = make_image(tmp_path / "photo.png")
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not an image")

    window.add_files([image, image, unsupported])

    assert window.files == [image.resolve()]
    assert window.file_list.count() == 1
    assert "1 imagem selecionada" == window.file_count.text()
    assert "duplicada" in window.feedback.text()
    assert "incompatível" in window.feedback.text()


def test_process_requires_files_and_output(qtbot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.output_dir = None
    window.output_field.clear()
    window.refresh_state()
    assert not window.process_button.isEnabled()

    window.add_files([make_image(tmp_path / "photo.png")])
    assert not window.process_button.isEnabled()
    window.set_output_folder(tmp_path / "edited")
    window.refresh_state()
    assert window.process_button.isEnabled()


def test_worker_continues_after_an_image_fails(monkeypatch, tmp_path: Path) -> None:
    files = [make_image(tmp_path / "one.png"), make_image(tmp_path / "two.png")]

    def fake_edit(input_path: Path, output_path: Path, number: int) -> None:
        if input_path.name == "one.png":
            raise RuntimeError("OCR failed")
        output_path.touch()

    monkeypatch.setattr(app, "edit_image", fake_edit)
    worker = BatchWorker(files, tmp_path / "output", 27)
    result = []
    worker.signals.finished.connect(lambda completed, errors: result.append((completed, errors)))

    worker.output_dir.mkdir()
    worker.run()

    assert result[0][0] == 1
    assert result[0][1] == [("one.png", "OCR failed")]
    assert (worker.output_dir / "two.png").exists()
