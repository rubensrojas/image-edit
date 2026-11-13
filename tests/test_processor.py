from __future__ import annotations

import hashlib
import random
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

import sre_processor
from sre_processor import (
    MAX_EDITED_FILENAME_BYTES,
    CaptionLayout,
    collision_safe_path,
    detect_caption_box,
    detect_caption_layout,
    draw_replacement_line,
    edited_filename,
    encode_edited_image,
    erase_caption_line,
    edit_image,
    fit_font_size,
    fit_line_font_size,
    format_sre_number,
    measure_text_bounds,
    normalize_numeric_suffix,
    normalize_sre_line,
    render_edited_image,
    render_free_line,
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


@pytest.mark.parametrize(
    ("filename", "number", "expected"),
    [
        ("photo.PNG", 27, "photo_SRE027.png"),
        ("MyPhoto.JPG", 7, "MyPhoto_SRE007.jpg"),
        ("photo_sre010.png", 27, "photo_SRE027.png"),
        ("photo_SrE999.JPEG", 0, "photo_SRE000.jpeg"),
    ],
)
def test_edited_filename_for_numeric_edits(
    filename: str, number: int, expected: str
) -> None:
    assert edited_filename(filename, number) == expected


@pytest.mark.parametrize(
    ("filename", "text", "expected"),
    [
        ("photo.png", "ÁREA 1 / NORTE", "photo_area_1_norte.png"),
        ("photo.png", "!!!", "photo_editada.png"),
        ("photo_linha_nova.png", "OUTRA LINHA", "photo_linha_nova_outra_linha.png"),
        ("photo_SRE010.PNG", "LINHA NOVA", "photo_SRE010_linha_nova.png"),
    ],
)
def test_edited_filename_for_full_line_edits(
    filename: str, text: str, expected: str
) -> None:
    assert edited_filename(filename, 27, full_line_text=text) == expected


def test_edited_filename_limits_utf8_bytes_and_preserves_distinguishing_suffix() -> None:
    numeric = edited_filename(f"{'á' * 180}.PNG", 27)
    full_line = edited_filename(f"{'á' * 180}.PNG", 27, full_line_text="ÁREA NORTE")

    assert len(numeric.encode("utf-8")) <= MAX_EDITED_FILENAME_BYTES
    assert numeric.endswith("_SRE027.png")
    assert len(full_line.encode("utf-8")) <= MAX_EDITED_FILENAME_BYTES
    assert full_line.endswith("_a.png")


def test_collision_counter_respects_filename_byte_limit(tmp_path: Path) -> None:
    filename = edited_filename(f"{'á' * 180}.PNG", 27)
    (tmp_path / filename).touch()

    candidate = collision_safe_path(
        tmp_path,
        filename,
        max_filename_bytes=MAX_EDITED_FILENAME_BYTES,
    )

    assert candidate.name.endswith(" (2).png")
    assert len(candidate.name.encode("utf-8")) <= MAX_EDITED_FILENAME_BYTES


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


# --- Free mode -------------------------------------------------------------

FONT_PATH = Path(__file__).resolve().parents[1] / "resources" / "Roboto-Regular.ttf"
CAPTION_LINES = (
    "MT - 027 SRE 027EMT",
    "LINHA DOIS 2024",
    "SEGUNDA-FEIRA 14:30",
    "AREA NORTE / SETOR 7",
)
Box = tuple[int, int, int, int]


def synthetic_photo(size: tuple[int, int], seed: int) -> Image.Image:
    """Deterministic smooth blotchy texture standing in for a photo."""
    rng = random.Random(seed)
    small = (size[0] // 20, size[1] // 20)
    data = bytes(rng.randrange(60, 230) for _ in range(small[0] * small[1] * 3))
    return Image.frombytes("RGB", small, data).resize(size, Image.Resampling.BICUBIC)


def add_caption(
    photo: Image.Image,
    box: Box,
    *,
    alpha: int,
    first_top: int,
    pitch: int,
    lines: tuple[str, ...] = CAPTION_LINES,
) -> tuple[Image.Image, list[Box]]:
    """Composite a dark caption box with centered white lines; return glyph bounds."""
    overlay = Image.new("RGBA", photo.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(
        (box[0], box[1], box[2] - 1, box[3] - 1), fill=(12, 10, 8, alpha)
    )
    img = Image.alpha_composite(photo.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_PATH), 38)
    center_x = (box[0] + box[2]) / 2
    bounds: list[Box] = []
    for index, text in enumerate(lines):
        position = (center_x, first_top + index * pitch)
        draw.text(position, text, font=font, fill=(255, 255, 255), anchor="ma")
        bbox = draw.textbbox(position, text, font=font, anchor="ma")
        bounds.append(tuple(round(value) for value in bbox))
    return img, bounds


TRANSLUCENT_BOX: Box = (318, 1112, 762, 1440)
SOLID_BOX: Box = (288, 1368, 912, 1600)


def translucent_caption(
    photo: Image.Image | None = None, lines: tuple[str, ...] = CAPTION_LINES
) -> tuple[Image.Image, list[Box]]:
    photo = photo or synthetic_photo((1080, 1440), seed=1)
    return add_caption(
        photo, TRANSLUCENT_BOX, alpha=150, first_top=1140, pitch=72, lines=lines
    )


def solid_caption() -> tuple[Image.Image, list[Box]]:
    return add_caption(
        synthetic_photo((1200, 1600), seed=2), SOLID_BOX, alpha=255, first_top=1382, pitch=54
    )


def assert_box_close(actual: Box, expected: Box, tolerance: int) -> None:
    assert all(abs(a - e) <= tolerance for a, e in zip(actual, expected)), (actual, expected)


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b).convert("L")).mean[0]


def luminance_std(img: Image.Image) -> float:
    return ImageStat.Stat(img.convert("L")).stddev[0]


@pytest.mark.parametrize(
    ("factory", "expected_box"),
    [(translucent_caption, TRANSLUCENT_BOX), (solid_caption, SOLID_BOX)],
    ids=["translucent", "solid"],
)
def test_detect_caption_box_and_layout(factory, expected_box: Box) -> None:
    img, bounds = factory()

    assert_box_close(detect_caption_box(img), expected_box, 3)

    layout = detect_caption_layout(img)
    assert len(layout.lines) == len(bounds) == 4
    for line, expected in zip(layout.lines, bounds):
        assert abs(line[1] - expected[1]) <= 2
        assert abs(line[3] - expected[3]) <= 2
        assert abs(line[0] - expected[0]) <= 4
        assert abs(line[2] - expected[2]) <= 4


@pytest.mark.parametrize(
    "pole",
    [
        (150, 300, 175, 1000),  # ends above the box
        (500, 300, 525, 1440),  # runs behind the translucent box
        (900, 400, 925, 1440),  # reaches the bottom, right of the box
        (150, 400, 175, 1440),  # reaches the bottom, left of the box
    ],
    ids=["above-box", "behind-box", "right-to-bottom", "left-to-bottom"],
)
def test_detect_caption_box_ignores_bright_vertical_pole(pole: Box) -> None:
    photo = synthetic_photo((1080, 1440), seed=1)
    ImageDraw.Draw(photo).rectangle(
        (pole[0], pole[1], pole[2] - 1, pole[3] - 1), fill=(245, 245, 240)
    )
    img, _ = translucent_caption(photo)

    assert_box_close(detect_caption_box(img), TRANSLUCENT_BOX, 3)


def test_erase_caption_line_keeps_translucent_texture() -> None:
    img, bounds = translucent_caption()
    clean, _ = translucent_caption(lines=())
    layout = detect_caption_layout(img)
    line = layout.lines[1]

    erase_caption_line(img, line, layout.box)

    area = (line[0], line[1], line[2], line[3])
    erased, background = img.crop(area), clean.crop(area)
    assert ImageStat.Stat(erased.convert("L")).extrema[0][1] <= (
        ImageStat.Stat(background.convert("L")).extrema[0][1] + 25
    )
    assert mean_abs_diff(erased, background) < 15
    # Photo texture behind the box survives instead of a flat fill.
    assert luminance_std(erased) >= 0.5 * luminance_std(background) > 3


def test_render_free_line_changes_only_the_chosen_line() -> None:
    original, _ = translucent_caption()
    layout = detect_caption_layout(original)
    line = layout.lines[2]
    img = original.copy()

    render_free_line(img, layout, 3, "NOVA LINHA 99", CAPTION_LINES[2])

    changed = ImageChops.difference(original, img).getbbox()
    assert changed is not None
    margin = 8
    assert changed[1] >= line[1] - margin and changed[3] <= line[3] + margin
    assert changed[0] >= layout.box[0] and changed[2] <= layout.box[2]


@pytest.mark.parametrize("line_number", [1, 2, 3, 4])
def test_render_free_line_with_same_text_keeps_glyph_geometry(line_number: int) -> None:
    original, _ = translucent_caption()
    layout = detect_caption_layout(original)
    img = original.copy()

    render_free_line(
        img, layout, line_number, CAPTION_LINES[line_number - 1], CAPTION_LINES[line_number - 1]
    )

    before = layout.lines[line_number - 1]
    after = detect_caption_layout(img).lines[line_number - 1]
    assert abs(after[1] - before[1]) <= 1 and abs(after[3] - before[3]) <= 1
    assert abs(after[0] - before[0]) <= 6 and abs(after[2] - before[2]) <= 6


@pytest.mark.parametrize("factory", [translucent_caption, solid_caption], ids=["translucent", "solid"])
def test_fit_line_font_size_matches_drawn_size(factory) -> None:
    img, _ = factory()
    layout = detect_caption_layout(img)

    sizes = [fit_line_font_size(text, line) for text, line in zip(CAPTION_LINES, layout.lines)]

    assert sizes == [38] * 4


def test_render_free_line_rejects_long_text_and_missing_line() -> None:
    img, _ = solid_caption()
    layout = detect_caption_layout(img)

    with pytest.raises(ValueError, match="longo demais"):
        render_free_line(img.copy(), layout, 1, "W" * 60, CAPTION_LINES[0])
    for line_number in (0, 5):
        with pytest.raises(ValueError, match="não existe"):
            render_free_line(img.copy(), layout, line_number, "OK", "")
    assert isinstance(layout, CaptionLayout)


@pytest.fixture
def free_mode_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def known_text(img: Image.Image, line: Box) -> str:
        layout = detect_caption_layout(img)
        return CAPTION_LINES[layout.lines.index(line)]

    monkeypatch.setattr(sre_processor, "_ocr_line", known_text)
    img, _ = translucent_caption()
    source = tmp_path / "caption.png"
    img.save(source)
    return source


def test_free_mode_render_validates_text_and_line_number(free_mode_source: Path) -> None:
    with pytest.raises(ValueError, match="Digite o novo texto"):
        render_edited_image(free_mode_source, full_line_text="   ", line_number=1)
    with pytest.raises(ValueError, match="quebra de linha"):
        render_edited_image(free_mode_source, full_line_text="A\nB", line_number=1)
    with pytest.raises(ValueError, match="não existe"):
        render_edited_image(free_mode_source, full_line_text="OK", line_number=5)
    with pytest.raises(ValueError, match="longo demais"):
        render_edited_image(free_mode_source, full_line_text="W" * 60, line_number=1)


def test_free_mode_encoding_round_trips_through_png(
    free_mode_source: Path, tmp_path: Path
) -> None:
    rendered = render_edited_image(free_mode_source, full_line_text="LINHA NOVA", line_number=4)
    encoded = encode_edited_image(free_mode_source, full_line_text="LINHA NOVA", line_number=4)
    output = tmp_path / "out.png"
    edit_image(free_mode_source, output, full_line_text="LINHA NOVA", line_number=4)

    with Image.open(BytesIO(encoded)) as decoded:
        assert decoded.format == "PNG"
        assert ImageChops.difference(decoded.convert("RGB"), rendered).getbbox() is None
    assert output.read_bytes() == encoded
    with Image.open(free_mode_source) as source:
        assert ImageChops.difference(source.convert("RGB"), rendered).getbbox() is not None


def test_caption_reference_outvotes_a_misdetected_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = []
    for index in range(5):
        img, _ = translucent_caption(synthetic_photo((1080, 1440), seed=10 + index))
        paths.append(tmp_path / f"photo{index}.png")
        img.save(paths[-1])

    real_detect = sre_processor.detect_caption_box
    calls = {"count": 0}

    def flaky_detect(img: Image.Image) -> Box:
        calls["count"] += 1
        if calls["count"] == 2:
            return (176, 1118, 761, 1440)  # a fence post won, as seen on real photos
        if calls["count"] == 3:
            raise RuntimeError("Não foi possível localizar a caixa da legenda")
        return real_detect(img)

    monkeypatch.setattr(sre_processor, "detect_caption_box", flaky_detect)
    reference = sre_processor.detect_caption_reference(paths)

    assert_box_close(reference.box_for((1080, 1440)), TRANSLUCENT_BOX, tolerance=3)
    assert_box_close(reference.box_for((540, 720)), (159, 556, 381, 720), tolerance=2)


def test_layout_with_reference_snaps_to_each_images_own_box() -> None:
    img, expected_lines = translucent_caption()
    shifted = sre_processor.CaptionReference(
        ((318 + 12) / 1080, (1112 - 10) / 1440, (762 - 12) / 1080, 1.0)
    )

    layout = detect_caption_layout(img, shifted)

    assert_box_close(layout.box, TRANSLUCENT_BOX, tolerance=3)
    assert len(layout.lines) == len(expected_lines)
    for actual, expected in zip(layout.lines, expected_lines):
        assert abs(actual[1] - expected[1]) <= 2 and abs(actual[3] - expected[3]) <= 2


def test_free_mode_refuses_image_with_a_different_line_count(free_mode_source: Path) -> None:
    with pytest.raises(ValueError, match="tem 4 linhas, mas a da primeira imagem tem 5"):
        sre_processor.render_edited_image(
            free_mode_source, full_line_text="OK", line_number=2, expected_line_count=5
        )
    sre_processor.render_edited_image(
        free_mode_source, full_line_text="OK", line_number=2, expected_line_count=4
    )


def test_render_free_line_keeps_off_center_text_inside_the_box() -> None:
    img, _ = translucent_caption()
    layout = detect_caption_layout(img)
    box = layout.box
    # Pretend the original line hugged the left edge of the box.
    line = layout.lines[0]
    shifted = CaptionLayout(box, (
        (box[0] + 6, line[1], box[0] + 60, line[3]),
        *layout.lines[1:],
    ))
    before = img.copy()

    render_free_line(img, shifted, 1, "MUITO MAIS LONGO 1234", "")

    outside = (0, line[1], box[0], line[3])
    assert mean_abs_diff(img.crop(outside), before.crop(outside)) == 0
    bright = [
        x for x in range(box[0], box[2])
        if any(min(img.getpixel((x, y))) > 200 for y in range(line[1], line[3]))
    ]
    assert bright and bright[0] > box[0] and bright[-1] < box[2] - 1


def test_erase_caption_line_without_background_samples_still_erases() -> None:
    img = Image.new("RGB", (200, 100), (150, 150, 150))
    ImageDraw.Draw(img).rectangle((60, 40, 140, 60), fill=(255, 255, 255))
    sre_processor.erase_caption_line(img, (60, 40, 141, 61), (50, 30, 151, 71))
    assert max(min(px) for px in img.crop((60, 40, 141, 61)).getdata()) < 200


def test_layout_with_reference_on_a_tiny_image_reports_a_readable_error() -> None:
    reference = sre_processor.CaptionReference((0.3, 0.75, 0.7, 1.0))
    with pytest.raises(RuntimeError, match="caixa da legenda"):
        detect_caption_layout(Image.new("RGB", (8, 8)), reference)
