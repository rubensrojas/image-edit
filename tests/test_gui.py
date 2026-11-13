from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

import app
from sre_processor import CaptionReference
from app import (
    STYLESHEET,
    AnimatedProgressBar,
    BatchWorker,
    ExportWorker,
    MainWindow,
    PaddedSpinBox,
    PreviewImage,
)
from sre_processor import CaptionLayout


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


def test_selection_step_requires_files_and_full_line_defaults_off(qtbot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.pages.currentIndex() == 0
    assert not window.continue_button.isEnabled()
    assert not window.full_line_toggle.isChecked()

    window.add_files([make_image(tmp_path / "photo.png")])
    assert window.continue_button.isEnabled()


def test_window_uses_large_layout_without_global_step_header(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.size().width() == 1280
    assert window.size().height() == 960
    assert window.minimumWidth() == 1040
    assert window.minimumHeight() == 800
    assert window.findChildren(QLabel, "stepLabel") == []
    assert "QLabel#stepLabel" not in STYLESHEET
    assert "QLabel#previewImage { background: transparent; border: 0" in STYLESHEET


def test_preview_preserves_aspect_ratio_and_overlay_matches_image(
    qtbot, tmp_path: Path
) -> None:
    path = tmp_path / "portrait.png"
    Image.new("RGB", (1200, 1600), "black").save(path)
    preview = PreviewImage("Prévia", Qt.AlignmentFlag.AlignRight)
    qtbot.addWidget(preview)
    preview.resize(600, 600)
    preview.show()
    preview.set_source(QPixmap(str(path)))
    preview.set_loading(True)

    rect = preview.image_rect
    assert rect.width() / rect.height() == 0.75
    assert rect.right() == preview.contentsRect().right()
    assert preview.loading_overlay.geometry() == rect
    assert preview.is_loading


def test_edit_mode_preserves_values_across_all_modes(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.number_mode_button.isChecked()
    window.sre_number.setValue(27)
    window.full_line_toggle.setChecked(True)
    window.full_line_input.setText("LINHA PERSONALIZADA")
    window.free_mode_button.setChecked(True)
    assert window.edit_inputs.currentIndex() == app.FREE_MODE
    window.free_line_input.setText("LINHA LIVRE")
    window.number_mode_button.setChecked(True)
    assert window.edit_inputs.currentIndex() == app.NUMBER_MODE
    window.full_line_toggle.setChecked(True)

    assert window.sre_number.value() == 27
    assert window.full_line_input.text() == "LINHA PERSONALIZADA"
    assert window.free_line_input.text() == "LINHA LIVRE"


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
    assert (worker.output_dir / "two_SRE027.png").exists()


def test_worker_assigns_distinct_final_name_and_preserves_source(
    monkeypatch, tmp_path: Path
) -> None:
    source = make_image(tmp_path / "photo.PNG")
    source_bytes = source.read_bytes()
    output = tmp_path / "output"
    output.mkdir()

    def fake_edit(
        input_path: Path,
        output_path: Path,
        number: int,
        *,
        full_line_text: str | None = None,
    ) -> None:
        output_path.write_bytes(input_path.read_bytes())

    monkeypatch.setattr(app, "edit_image", fake_edit)
    BatchWorker([source], output, 27).run()

    assert [path.name for path in output.iterdir()] == ["photo_SRE027.png"]
    assert source.read_bytes() == source_bytes


def test_worker_counters_duplicate_generated_names(monkeypatch, tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    files = [make_image(first_dir / "photo.png"), make_image(second_dir / "photo.png")]
    output = tmp_path / "output"
    output.mkdir()

    def fake_edit(input_path: Path, output_path: Path, number: int) -> None:
        output_path.write_bytes(input_path.read_bytes())

    monkeypatch.setattr(app, "edit_image", fake_edit)
    BatchWorker(files, output, 27).run()

    assert sorted(path.name for path in output.iterdir()) == [
        "photo_SRE027 (2).png",
        "photo_SRE027.png",
    ]


def test_worker_can_cancel_before_processing(tmp_path: Path) -> None:
    image = make_image(tmp_path / "one.png")
    output = tmp_path / "output"
    output.mkdir()
    cancel_event = threading.Event()
    cancel_event.set()
    worker = BatchWorker([image], output, 27, cancel_event=cancel_event)
    cancelled = []
    worker.signals.cancelled.connect(lambda: cancelled.append(True))

    worker.run()

    assert cancelled == [True]
    assert list(output.iterdir()) == []


def test_worker_passes_full_line_only_in_full_line_mode(
    monkeypatch, tmp_path: Path
) -> None:
    image = make_image(tmp_path / "one.png")
    output = tmp_path / "output"
    output.mkdir()
    received = []

    def fake_edit(
        input_path: Path,
        output_path: Path,
        number: int,
        *,
        full_line_text: str | None = None,
    ) -> None:
        received.append((input_path, number, full_line_text))
        output_path.touch()

    monkeypatch.setattr(app, "edit_image", fake_edit)
    BatchWorker([image], output, 27, full_line_text="LINHA ÚNICA").run()

    assert received == [(image, 27, "LINHA ÚNICA")]


def test_stale_preview_result_is_ignored(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.preview_request_id = 4
    window.preview_valid = False

    window.preview_ready(3, b"not-an-image", "old OCR text")

    assert not window.preview_valid
    assert not window.raw_line_loaded


def test_preview_overlay_survives_stale_result_and_stops_on_active_error(
    qtbot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    image = make_image(tmp_path / "photo.png")
    window.files = [image]
    window.show_page(1)
    window.after_preview.set_source(QPixmap(str(image)))
    window.schedule_preview()
    window.preview_timer.stop()
    current_request = window.preview_request_id

    window.preview_ready(current_request - 1, b"stale", "")
    assert window.after_preview.is_loading

    window.preview_failed(current_request, "OCR failed", "")
    assert not window.after_preview.is_loading


def test_animated_progress_eases_and_supports_reduced_motion(qtbot) -> None:
    progress = AnimatedProgressBar()
    qtbot.addWidget(progress)
    progress.setRange(0, 100)
    progress.motion_enabled_override = True
    progress.set_immediate_value(0)

    progress.set_animated_value(75)
    assert progress.value() < 75
    qtbot.waitUntil(lambda: progress.value() == 75, timeout=1000)

    progress.motion_enabled_override = False
    reached = []
    progress.target_reached.connect(reached.append)
    progress.set_animated_value(40)
    assert progress.value() == 40
    assert reached[-1] == 40


def test_result_waits_for_progress_to_reach_100(qtbot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.files = [make_image(tmp_path / "photo.png")]
    window.temp_dir = tmp_path / "temporary"
    window.temp_dir.mkdir()
    (window.temp_dir / "photo.png").touch()
    window.processing = True
    window.progress.motion_enabled_override = True
    window.progress.set_immediate_value(0)
    window.show_page(2)

    window.processing_finished(1, [])

    assert window.pages.currentIndex() == 2
    qtbot.waitUntil(lambda: window.pages.currentIndex() == 3, timeout=1000)
    assert window.progress.value() == 100
    assert "photo.png → photo_SRE010.png" in window.result_summary.text()
    window._cleanup_temp_dir()


def test_batch_failure_is_transactional_and_selects_problem_files(
    qtbot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    good = make_image(tmp_path / "good.png")
    bad = make_image(tmp_path / "bad.png")
    window.add_files([good, bad])
    window.temp_dir = tmp_path / "temporary"
    window.temp_dir.mkdir()
    (window.temp_dir / "good.png").touch()
    window.processing = True
    window.progress.motion_enabled_override = False

    window.processing_finished(1, [("bad.png", "OCR failed")])

    assert window.pages.currentIndex() == 3
    assert window.temp_dir is None
    assert window.export_actions.isHidden()
    assert not window.review_button.isHidden()
    window.review_failed_images()
    assert window.file_list.item(1).isSelected()


def test_export_worker_supports_folder_collisions_and_zip(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    source = make_image(prepared / "photo_SRE027.png")
    destination = tmp_path / "destination"
    destination.mkdir()
    make_image(destination / "photo_SRE027.png")

    folder_worker = ExportWorker([source], "folder", destination)
    folder_result = []
    folder_worker.signals.finished.connect(
        lambda succeeded, location, error: folder_result.append((succeeded, location, error))
    )
    folder_worker.run()

    assert folder_result[0][0]
    assert (destination / "photo_SRE027 (2).png").exists()

    archive_path = tmp_path / "images.zip"
    zip_worker = ExportWorker([source], "zip", archive_path)
    zip_result = []
    zip_worker.signals.finished.connect(
        lambda succeeded, location, error: zip_result.append((succeeded, location, error))
    )
    zip_worker.run()

    assert zip_result[0][0]
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["photo_SRE027.png"]


def fake_free_mode(monkeypatch) -> list[dict]:
    layout = CaptionLayout(
        box=(40, 200, 160, 290),
        lines=((60, 210, 140, 222), (55, 232, 145, 244), (50, 254, 150, 266)),
    )
    previews: list[dict] = []

    def fake_encode(input_path: Path, number: int, **kwargs) -> bytes:
        previews.append(kwargs)
        buffer = io.BytesIO()
        Image.new("RGB", (200, 300), "gray").save(buffer, format="PNG")
        return buffer.getvalue()

    reference = CaptionReference((0.0, 0.5, 1.0, 1.0))
    monkeypatch.setattr(app, "detect_caption_reference", lambda files: reference)
    monkeypatch.setattr(app, "detect_caption_layout", lambda img, ref=None: layout)
    monkeypatch.setattr(
        app, "read_caption_lines", lambda img, layout=None: ["UM", "DOIS", "LINHA TRÊS"]
    )
    monkeypatch.setattr(app, "read_raw_line_3", lambda img: "")
    monkeypatch.setattr(app, "encode_edited_image", fake_encode)
    return previews


def test_free_mode_click_selects_line_and_enables_apply(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    previews = fake_free_mode(monkeypatch)
    path = tmp_path / "photo.png"
    Image.new("RGB", (200, 300), "black").save(path)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.add_files([path])
    window.continue_to_edit()

    window.free_mode_button.click()
    qtbot.waitUntil(lambda: window.free_layout is not None, timeout=2000)
    qtbot.waitUntil(lambda: not window.preview_loading, timeout=2000)
    assert window.free_line is None
    assert not window.apply_button.isEnabled()
    assert len(window.line_option_group.buttons()) == 3
    assert "Linha 3 · LINHA TRÊS" in window.line_option_group.button(2).toolTip()

    target = window.before_preview.line_region_rect(2)
    assert not target.isEmpty()
    qtbot.mouseClick(
        window.before_preview, Qt.MouseButton.LeftButton, pos=target.center()
    )

    assert window.free_line == 3
    assert window.line_option_group.button(2).isChecked()
    assert window.free_line_input.text() == "LINHA TRÊS"
    assert window.free_line_label.text() == "Novo texto da linha 3"
    qtbot.waitUntil(lambda: window.apply_button.isEnabled(), timeout=2000)
    assert previews[-1] == {
        "full_line_text": "LINHA TRÊS",
        "line_number": 3,
        "caption_reference": CaptionReference((0.0, 0.5, 1.0, 1.0)),
    }

    window.line_option_group.button(0).click()
    assert window.free_line == 1
    assert window.free_line_input.text() == "UM"

    window.free_line_input.clear()
    window.free_line_edited("")
    window.preview_timer.stop()
    window.start_preview()
    assert not window.apply_button.isEnabled()


def test_free_mode_detection_failure_keeps_apply_disabled(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    fake_free_mode(monkeypatch)

    def fail(img, ref=None):
        raise RuntimeError("Nenhuma legenda encontrada")

    monkeypatch.setattr(app, "detect_caption_layout", fail)
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_files([make_image(tmp_path / "photo.png")])
    window.continue_to_edit()
    window.free_mode_button.click()

    qtbot.waitUntil(lambda: bool(window.free_error), timeout=2000)
    qtbot.waitUntil(lambda: not window.preview_loading, timeout=2000)
    assert "Nenhuma legenda encontrada" in window.free_line_message.text()
    assert not window.apply_button.isEnabled()


def test_new_batch_resets_free_mode(qtbot, monkeypatch, tmp_path: Path) -> None:
    fake_free_mode(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_files([make_image(tmp_path / "photo.png")])
    window.continue_to_edit()
    window.free_mode_button.click()
    qtbot.waitUntil(lambda: window.free_layout is not None, timeout=2000)
    window.select_free_line(1)

    window.start_new_batch()

    assert window.number_mode_button.isChecked()
    assert window.free_layout is None
    assert window.free_line is None
    assert window.free_line_input.text() == ""
    assert window.line_option_group.buttons() == []


def test_worker_passes_line_number_in_free_mode(monkeypatch, tmp_path: Path) -> None:
    image = make_image(tmp_path / "one.png")
    output = tmp_path / "output"
    output.mkdir()
    received = []

    def fake_edit(
        input_path: Path,
        output_path: Path,
        number: int,
        *,
        full_line_text: str | None = None,
        line_number: int | None = None,
        caption_reference: CaptionReference | None = None,
        expected_line_count: int | None = None,
    ) -> None:
        received.append((full_line_text, line_number, caption_reference))
        output_path.touch()

    monkeypatch.setattr(app, "edit_image", fake_edit)
    reference = CaptionReference((0.2, 0.7, 0.8, 1.0))
    BatchWorker(
        [image],
        output,
        27,
        full_line_text="NOVA LINHA",
        line_number=2,
        caption_reference=reference,
    ).run()

    assert received == [("NOVA LINHA", 2, reference)]
    assert [path.name for path in output.iterdir()] == ["one_nova_linha.png"]
