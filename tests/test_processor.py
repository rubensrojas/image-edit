from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

import sre_processor
from sre_processor import (
    collision_safe_path,
    draw_replacement_line,
    encode_edited_image,
    edit_image,
    fit_font_size,
    format_sre_number,
    measure_text_bounds,
    normalize_numeric_suffix,
    normalize_sre_line,
    text_fits_line,
    unique_paths,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "000"), (10, "010"), (27, "027"), (999, "999")],
)
def test_format_sre_number(value: int, expected: str) -> None:
    assert format_sre_number(value) == expected


@pytest.mark.parametrize("value", [-1, 1000])
def test_format_sre_number_rejects_out_of_range(value: int) -> None:
    with pytest.raises(ValueError):
        format_sre_number(value)


def test_normalize_sre_line_replaces_both_values_and_preserves_suffix() -> None:
    assert (
        normalize_sre_line("MT - 415 SRE 0415EMT0415", 27)
        == "MT - 027 SRE 027EMT0415"
    )


def test_normalize_sre_line_handles_common_ocr_characters() -> None:
    assert normalize_sre_line("MT — O10 SRE O10EMT04I5", 10) == "MT - 010 SRE 010EMT0415"
    assert normalize_numeric_suffix("O4ISB)") == "041580"


def test_normalize_sre_line_returns_none_for_unrelated_text() -> None:
    assert normalize_sre_line("not an SRE subtitle", 10) is None


def test_collision_safe_path_preserves_name_and_adds_counter(tmp_path: Path) -> None:
    assert collision_safe_path(tmp_path, "photo.jpeg") == tmp_path / "photo.jpeg"
    (tmp_path / "photo.jpeg").touch()
    assert collision_safe_path(tmp_path, "photo.jpeg") == tmp_path / "photo (2).jpeg"
    (tmp_path / "photo (2).jpeg").touch()
    assert collision_safe_path(tmp_path, "photo.jpeg") == tmp_path / "photo (3).jpeg"


def test_unique_paths_filters_unsupported_and_duplicates(tmp_path: Path) -> None:
    image = tmp_path / "photo.JPG"
    image.touch()
    text = tmp_path / "notes.txt"
    text.touch()
    assert unique_paths([image, image, text]) == [image.resolve()]


def test_font_fitting_matches_measured_source_line() -> None:
    text = "MT - 389 SRE 389EMT0045"
    assert fit_font_size(text, (624, 58), (599, 37)) == 50

    image = Image.new("RGB", (624, 58), (18, 15, 12))
    draw_replacement_line(image, (0, 0, 624, 58), text, (599, 37))
    bounds = measure_text_bounds(image)

    assert bounds is not None
    assert abs(bounds[0] - 599) <= 4
    assert abs(bounds[1] - 37) <= 1


def test_edit_image_preserves_source_and_output_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpeg"
    Image.new("RGB", (1200, 1600), (20, 18, 16)).save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "result.jpeg"

    monkeypatch.setattr(
        sre_processor,
        "read_line_3",
        lambda image, number: f"MT - {number:03d} SRE {number:03d}EMT0415",
    )
    edit_image(source, output, 27)

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with Image.open(output) as result:
        assert result.size == (1200, 1600)
        assert result.format == "JPEG"


def test_full_line_is_literal_and_does_not_use_numeric_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), (20, 18, 16)).save(source)
    captured: dict[str, object] = {}

    def fail_numeric_ocr(*_args: object) -> str:
        raise AssertionError("numeric OCR must not run in full-line mode")

    def capture_draw(
        image: Image.Image,
        box: tuple[int, int, int, int],
        text: str,
        target_bounds: object = None,
        fixed_font_size: int | None = None,
    ) -> None:
        captured["text"] = text
        captured["font_size"] = fixed_font_size

    monkeypatch.setattr(sre_processor, "read_line_3", fail_numeric_ocr)
    monkeypatch.setattr(sre_processor, "draw_replacement_line", capture_draw)

    encode_edited_image(source, full_line_text="  LINHA ÚNICA  ")

    assert captured["text"] == "LINHA ÚNICA"
    assert isinstance(captured["font_size"], int)


def test_preview_encoding_matches_written_batch_file(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), (20, 18, 16)).save(source)
    output = tmp_path / "output.png"

    preview = encode_edited_image(source, full_line_text="LINHA COMPLETA")
    edit_image(source, output, full_line_text="LINHA COMPLETA")

    assert output.read_bytes() == preview


def test_full_line_rejects_empty_or_visually_oversized_text(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), (20, 18, 16)).save(source)

    with pytest.raises(ValueError, match="Digite o texto"):
        encode_edited_image(source, full_line_text="   ")
    with pytest.raises(ValueError, match="longo demais"):
        encode_edited_image(source, full_line_text="W" * 80)

    assert text_fits_line("LINHA CURTA", (624, 58), (599, 37))
    assert not text_fits_line("W" * 80, (624, 58), (599, 37))
