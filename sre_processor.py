from __future__ import annotations

import os
import re
import sys
import unicodedata
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
import pytesseract


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_EDITED_FILENAME_BYTES = 200
_COLLISION_COUNTER_RESERVE_BYTES = 12

# Coordinates are relative to the 1200x1600 source layout.
REFERENCE_SIZE = (1200, 1600)
SUBTITLE_BOX = (288, 1368, 912, 1600)
LINE_3_BAND = (288, 1487, 912, 1545)


def resource_root() -> Path:
    """Return the root used by source and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def configure_tesseract() -> Optional[Path]:
    """Use a bundled Tesseract when present, otherwise defer to PATH."""
    executable = "tesseract.exe" if sys.platform == "win32" else "tesseract"
    root = resource_root()
    candidates = (
        root / "tesseract" / executable,
        root / "tesseract" / "bin" / executable,
    )

    for candidate in candidates:
        if candidate.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            tessdata = root / "tesseract" / "share" / "tessdata"
            if not tessdata.is_dir():
                tessdata = root / "tesseract" / "tessdata"
            if tessdata.is_dir():
                os.environ["TESSDATA_PREFIX"] = str(tessdata)
            return candidate
    return None


configure_tesseract()


def tesseract_version() -> str:
    """Execute Tesseract so packaged smoke tests cover the native runtime."""
    configure_tesseract()
    return str(pytesseract.get_tesseract_version())


def format_sre_number(value: int) -> str:
    if not 0 <= value <= 999:
        raise ValueError("O número SRE deve estar entre 000 e 999")
    return f"{value:03d}"


def scale_box(
    box: tuple[int, int, int, int], size: tuple[int, int]
) -> tuple[int, int, int, int]:
    ref_w, ref_h = REFERENCE_SIZE
    w, h = size
    x1, y1, x2, y2 = box
    return (
        round(x1 * w / ref_w),
        round(y1 * h / ref_h),
        round(x2 * w / ref_w),
        round(y2 * h / ref_h),
    )


def detect_line_3_box(img: Image.Image) -> tuple[int, int, int, int]:
    _, y1, _, y2 = scale_box(LINE_3_BAND, img.size)
    subtitle_x1, _, subtitle_x2, subtitle_y2 = scale_box(SUBTITLE_BOX, img.size)
    _, subtitle_y1, _, _ = scale_box(SUBTITLE_BOX, img.size)
    width, _ = img.size
    pixels = img.load()
    panel_segments: list[tuple[int, int]] = []

    min_segment_width = width * 0.35
    expected_center = (subtitle_x1 + subtitle_x2) / 2
    max_center_distance = width * 0.12

    for y in range(subtitle_y1, subtitle_y2):
        start: Optional[int] = None
        for x in range(width):
            r, g, b = pixels[x, y]
            is_dark = max(r, g, b) < 65

            if is_dark and start is None:
                start = x
            elif not is_dark and start is not None:
                segment = (start, x - 1)
                segment_width = segment[1] - segment[0] + 1
                segment_center = (segment[0] + segment[1]) / 2
                if (
                    segment_width >= min_segment_width
                    and abs(segment_center - expected_center) <= max_center_distance
                ):
                    panel_segments.append(segment)
                start = None

        if start is not None:
            segment = (start, width - 1)
            segment_width = segment[1] - segment[0] + 1
            segment_center = (segment[0] + segment[1]) / 2
            if (
                segment_width >= min_segment_width
                and abs(segment_center - expected_center) <= max_center_distance
            ):
                panel_segments.append(segment)

    if not panel_segments:
        return scale_box(LINE_3_BAND, img.size)

    lefts = sorted(left for left, _ in panel_segments)
    rights = sorted(right for _, right in panel_segments)
    left = lefts[len(lefts) // 2]
    right = rights[len(rights) // 2]
    return (left, y1, min(width, right + 1), y2)


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bundled_font = resource_root() / "resources" / "Roboto-Regular.ttf"
    candidates = [
        bundled_font,
        Path("/Applications/Android Studio.app/Contents/plugins/design-tools/resources/layoutlib/data/fonts/Roboto-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]

    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def normalize_numeric_suffix(text: str) -> str:
    replacements = str.maketrans(
        {
            "O": "0",
            "D": "0",
            "Q": "0",
            "C": "0",
            ")": "0",
            "I": "1",
            "L": "1",
            "|": "1",
            "S": "5",
            "B": "8",
        }
    )
    normalized = text.upper().translate(replacements)
    return "".join(char for char in normalized if char.isdigit())


def normalize_sre_line(text: str, sre_number: int = 10) -> Optional[str]:
    """Normalize an OCR line and replace both three-digit SRE values."""
    cleaned = text.upper().replace("—", "-").replace("–", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    match = re.search(
        r"MT\s*-\s*[A-Z0-9|]{1,4}\s+SRE\s+.*?EMT\s*([A-Z0-9)]+)",
        cleaned,
    )
    if not match:
        return None

    suffix = normalize_numeric_suffix(match.group(1))
    if not suffix:
        return None

    number = format_sre_number(sre_number)
    return f"MT - {number} SRE {number}EMT{suffix}"


def read_line_3(img: Image.Image, sre_number: int) -> str:
    line_box = detect_line_3_box(img)
    crop = img.crop(line_box)
    text = pytesseract.image_to_string(crop, config="--psm 7").strip()
    replacement = normalize_sre_line(text, sre_number)
    if replacement:
        return replacement

    subtitle_crop = img.crop(scale_box(SUBTITLE_BOX, img.size))
    data = pytesseract.image_to_data(
        subtitle_crop,
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )

    lines: dict[tuple[int, int, int], list[str]] = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)

    for words in lines.values():
        replacement = normalize_sre_line(" ".join(words), sre_number)
        if replacement:
            return replacement
    raise RuntimeError("Não foi possível ler a linha SRE")


def read_raw_line_3(img: Image.Image) -> str:
    """Read the visible third caption line without changing its content."""
    line_box = detect_line_3_box(img)
    crop = img.crop(line_box)
    return re.sub(
        r"\s+",
        " ",
        pytesseract.image_to_string(crop, config="--psm 7").strip(),
    )


def sample_subtitle_background(line_crop: Image.Image) -> tuple[int, int, int]:
    pixels = line_crop.load()
    width, height = line_crop.size
    dark_pixels: list[tuple[int, int, int]] = []

    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            if max(r, g, b) < 80:
                dark_pixels.append((r, g, b))

    if not dark_pixels:
        return (18, 15, 12)
    dark_pixels.sort(key=lambda color: sum(color))
    return dark_pixels[len(dark_pixels) // 2]


def erase_old_line_text(img: Image.Image, line_box: tuple[int, int, int, int]) -> None:
    fill = sample_subtitle_background(img.crop(line_box))
    x1, y1, x2, y2 = line_box
    ImageDraw.Draw(img).rectangle((x1, y1, x2 - 1, y2 - 1), fill=fill)


def measure_text_bounds(line_crop: Image.Image) -> Optional[tuple[int, int]]:
    """Measure the bright glyph area before it is erased."""
    crop = line_crop.convert("RGB")
    pixels = crop.load()
    width, height = crop.size
    minimum_row_pixels = max(8, width // 50)

    active_rows: list[int] = []
    for y in range(height):
        bright_pixels = sum(
            1
            for x in range(width)
            if min(pixels[x, y]) > 135 and max(pixels[x, y]) - min(pixels[x, y]) < 60
        )
        if bright_pixels >= minimum_row_pixels:
            active_rows.append(y)

    if not active_rows:
        return None

    top, bottom = min(active_rows), max(active_rows)
    active_columns: list[int] = []
    for x in range(width):
        bright_pixels = sum(
            1
            for y in range(top, bottom + 1)
            if min(pixels[x, y]) > 135 and max(pixels[x, y]) - min(pixels[x, y]) < 60
        )
        if bright_pixels >= 2:
            active_columns.append(x)

    if not active_columns:
        return None
    return (
        max(active_columns) - min(active_columns) + 1,
        bottom - top + 1,
    )


def fit_font_size(
    text: str,
    line_size: tuple[int, int],
    target_bounds: Optional[tuple[int, int]],
) -> int:
    """Choose the size whose rendered bounds best match the source line."""
    width, height = line_size
    scale = height / (LINE_3_BAND[3] - LINE_3_BAND[1])
    target_width, target_height = target_bounds or (
        width - max(12, round(16 * scale)),
        round(37 * scale),
    )
    horizontal_padding = max(8, round(12 * scale))
    best_size = max(12, round(50 * scale))
    best_score = float("inf")

    for size in range(max(12, round(32 * scale)), max(13, round(72 * scale)) + 1):
        font = find_font(size)
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width > width - horizontal_padding or text_height > height - 4:
            continue
        score = (
            abs(text_width - target_width) / max(1, target_width)
            + 2 * abs(text_height - target_height) / max(1, target_height)
        )
        if score < best_score:
            best_score = score
            best_size = size
    return best_size


def source_font_size(
    line_size: tuple[int, int], target_bounds: Optional[tuple[int, int]]
) -> int:
    """Estimate the original point size without adapting it to replacement width."""
    _, height = line_size
    scale = height / (LINE_3_BAND[3] - LINE_3_BAND[1])
    target_height = target_bounds[1] if target_bounds else round(37 * scale)
    reference = "MT - 010 SRE 010EMT0415"
    best_size = max(12, round(50 * scale))
    best_difference = float("inf")

    for size in range(max(12, round(32 * scale)), max(13, round(72 * scale)) + 1):
        bbox = find_font(size).getbbox(reference)
        difference = abs((bbox[3] - bbox[1]) - target_height)
        if difference < best_difference:
            best_difference = difference
            best_size = size
    return best_size


def text_fits_line(
    text: str,
    line_size: tuple[int, int],
    target_bounds: Optional[tuple[int, int]],
) -> bool:
    """Return whether full-line text fits at the source line's font size."""
    width, height = line_size
    scale = height / (LINE_3_BAND[3] - LINE_3_BAND[1])
    horizontal_padding = max(8, round(12 * scale))
    font = find_font(source_font_size(line_size, target_bounds))
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0] <= width - horizontal_padding


def draw_replacement_line(
    img: Image.Image,
    line_box: tuple[int, int, int, int],
    text: str,
    target_bounds: Optional[tuple[int, int]] = None,
    fixed_font_size: Optional[int] = None,
) -> None:
    x1, y1, x2, y2 = line_box
    line_width, line_height = x2 - x1, y2 - y1
    font_size = fixed_font_size or fit_font_size(
        text, (line_width, line_height), target_bounds
    )

    # Render large and downsample for smoother edges than direct bitmap text.
    supersample = 4
    layer = Image.new(
        "RGBA",
        (line_width * supersample, line_height * supersample),
        (0, 0, 0, 0),
    )
    layer_draw = ImageDraw.Draw(layer)
    font = find_font(font_size * supersample)
    bbox = layer_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = (layer.width - text_width) / 2 - bbox[0]
    text_y = (layer.height - text_height) / 2 - bbox[1]
    layer_draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)

    layer = layer.resize((line_width, line_height), Image.Resampling.LANCZOS)
    img.paste(layer, (x1, y1), layer)


# --- Free mode: edit any caption line -------------------------------------

Box = tuple[int, int, int, int]

# Box sides must be straight luminance steps at least this share of the height.
_MIN_BOX_HEIGHT = 0.08
# The box top must darken at least this share of the box width in one row.
_MIN_TOP_EDGE_SCORE = 0.5
_EDGE_STEP = 10
_TEXT_MIN_CHANNEL = 150
_TEXT_MAX_SATURATION = 70


@dataclass(frozen=True)
class CaptionLayout:
    """Dark caption box and the tight glyph bounds of each text line inside it."""

    box: Box
    lines: tuple[Box, ...]


@dataclass(frozen=True)
class CaptionReference:
    """Batch-wide caption box, stored as fractions of the image size.

    Every image in a batch carries the caption in the same place, so the box is
    voted once over several images and reused; only the text lines, which are
    trivial to find inside a known box, are located per image.
    """

    fractions: tuple[float, float, float, float]

    def box_for(self, size: tuple[int, int]) -> Box:
        width, height = size
        fx1, fy1, fx2, fy2 = self.fractions
        return (
            round(fx1 * width),
            round(fy1 * height),
            round(fx2 * width),
            round(fy2 * height),
        )


# Detecting the box in several images and taking the median per side outvotes
# the occasional photo whose own edges (grass, poles) fool the detector.
REFERENCE_SAMPLES = 9


def _threshold(img: Image.Image, minimum: int) -> Image.Image:
    return img.point(lambda value: 255 if value > minimum else 0)


def _edge_scores(mask: Image.Image, axis: str) -> list[float]:
    """Share of set pixels per column ("x") or per row ("y") of a binary mask."""
    width, height = mask.size
    data = mask.tobytes()
    if axis == "y":
        return [
            (width - data[y * width : (y + 1) * width].count(0)) / width
            for y in range(height)
        ]
    columns = mask.transpose(Image.Transpose.ROTATE_90)
    col_data = columns.tobytes()
    scores = [
        (height - col_data[i * height : (i + 1) * height].count(0)) / height
        for i in range(width)
    ]
    return scores[::-1]


def _text_mask(img: Image.Image) -> Image.Image:
    """White-ish, unsaturated pixels: caption glyphs."""
    r, g, b = img.split()
    low = ImageChops.darker(r, ImageChops.darker(g, b))
    high = ImageChops.lighter(r, ImageChops.lighter(g, b))
    bright = _threshold(low, _TEXT_MIN_CHANNEL)
    neutral = ImageChops.invert(
        _threshold(ImageChops.subtract(high, low), _TEXT_MAX_SATURATION)
    )
    return ImageChops.multiply(bright, neutral)


def _longest_runs(mask: Image.Image, max_gap: int) -> list[tuple[int, int]]:
    """Longest run of set pixels in each row of a binary mask, bridging gaps."""
    width, height = mask.size
    data = mask.tobytes()
    pattern = re.compile(rb"\xff+(?:\x00{1,%d}\xff+)*" % max_gap)
    runs: list[tuple[int, int]] = []
    for y in range(height):
        best = (0, -1)
        for match in pattern.finditer(data[y * width : (y + 1) * width]):
            if match.end() - match.start() > best[1] - best[0] + 1:
                best = (match.start(), match.end() - 1)
        runs.append(best)
    return runs


def _step(gray: Image.Image, axis: str, *, darker: bool) -> Image.Image:
    """Pixels darker (or brighter) than the ones 3 px before them."""
    w, h = gray.size
    if axis == "x":
        before, after = gray.crop((0, 0, w - 3, h)), gray.crop((3, 0, w, h))
    else:
        before, after = gray.crop((0, 0, w, h - 3)), gray.crop((0, 3, w, h))
    difference = (
        ImageChops.subtract(before, after) if darker else ImageChops.subtract(after, before)
    )
    return _threshold(difference, _EDGE_STEP)


def detect_caption_box(img: Image.Image) -> Box:
    """Find the dark caption box in the lower half of the image.

    The box darkens whatever is behind it (solid or translucent), so its left
    and right sides are long vertical luminance steps spanning the same rows,
    and its top is the row where the whole width darkens at once.
    """
    gray = img.convert("L")
    width, height = gray.size
    top_limit = height // 2
    lower = gray.crop((0, top_limit, width, height))
    lw, lh = lower.size

    # Columns become rows so each side is scanned as one byte string.
    left_runs = _longest_runs(
        _step(lower, "x", darker=True).transpose(Image.Transpose.TRANSPOSE), max_gap=3
    )
    right_runs = _longest_runs(
        _step(lower, "x", darker=False).transpose(Image.Transpose.TRANSPOSE), max_gap=3
    )
    min_run = max(20, round(height * _MIN_BOX_HEIGHT))
    half = len(left_runs) // 2

    def long_enough(run: tuple[int, int]) -> bool:
        return run[1] - run[0] + 1 >= min_run

    lefts = [(x, run) for x, run in enumerate(left_runs[:half]) if long_enough(run)]
    rights = [
        (x, run)
        for x, run in enumerate(right_runs[half:], start=half)
        if long_enough(run)
    ]
    # Both sides of the box start and end on the same rows; reward overlap and
    # penalize mismatched ends so a pole reaching the bottom cannot win.
    best: Optional[tuple[int, int]] = None
    best_score = 0
    for left_x, left_run in lefts:
        for right_x, right_run in rights:
            overlap = min(left_run[1], right_run[1]) - max(left_run[0], right_run[0]) + 1
            if overlap < min_run:
                continue
            union = max(left_run[1], right_run[1]) - min(left_run[0], right_run[0]) + 1
            score = 2 * overlap - union
            if best is None or score > best_score:
                best_score = score
                best = (left_x, right_x)
    if best is None:
        raise RuntimeError("Não foi possível localizar a caixa da legenda")
    # Step pixels sit between columns i and i + 3; the box starts at i + 2.
    x1, x2 = best[0] + 2, best[1] + 1

    inside = lower.crop((x1, 0, x2, lh))
    top_scores = _edge_scores(_step(inside, "y", darker=True), "y")
    top_index = max(range(len(top_scores)), key=top_scores.__getitem__)
    if top_scores[top_index] < _MIN_TOP_EDGE_SCORE:
        raise RuntimeError("Não foi possível localizar a caixa da legenda")

    # A box that stops above the image edge ends in a step as strong as its top.
    bottom_scores = _edge_scores(_step(inside, "y", darker=False), "y")
    y2 = height
    below = range(top_index + min_run, len(bottom_scores))
    if below:
        bottom_index = max(below, key=bottom_scores.__getitem__)
        if bottom_scores[bottom_index] >= top_scores[top_index] * 0.9:
            y2 = top_limit + bottom_index + 2
    return (x1, top_limit + top_index + 2, x2, y2)


def _runs(flags: list[bool], max_gap: int) -> list[list[int]]:
    runs: list[list[int]] = []
    start: Optional[int] = None
    for index, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            if runs and start - runs[-1][1] - 1 <= max_gap:
                runs[-1][1] = index - 1
            else:
                runs.append([start, index - 1])
            start = None
    return runs


def _refine_box(img: Image.Image, guess: Box) -> Box:
    """Snap each side of a batch box to this image's own edge, searching nearby.

    Inside a small window the box edge is by far the strongest straight step,
    so the local search is reliable even where a full-image search is not.
    """
    gray = img.convert("L")
    width, height = gray.size
    reach = max(6, round(width * 0.02))
    x1, y1, x2, y2 = guess
    rows = (max(0, y1 + reach), min(height, y2))
    cols = (max(0, x1 + reach), min(width, x2 - reach))
    if rows[1] - rows[0] < 10 or cols[1] - cols[0] < 10:
        return guess
    guess_width = x2 - x1

    def best(scores: list[float], start: int, low: int, high: int) -> Optional[int]:
        window = [i for i in range(low - start, high - start + 1) if 0 <= i < len(scores)]
        if not window:
            return None
        index = max(window, key=scores.__getitem__)
        return index + start if scores[index] >= 0.3 else None

    band = gray.crop((0, rows[0], width, rows[1]))
    left = best(_edge_scores(_step(band, "x", darker=True), "x"), 0, x1 - 2 - reach, x1 - 2 + reach)
    right = best(_edge_scores(_step(band, "x", darker=False), "x"), 0, x2 - 1 - reach, x2 - 1 + reach)
    new_x1 = left + 2 if left is not None else x1
    new_x2 = right + 1 if right is not None else x2
    strip = gray.crop((max(0, new_x1 + reach), 0, min(width, new_x2 - reach), height))
    top = best(_edge_scores(_step(strip, "y", darker=True), "y"), 0, y1 - 2 - reach, y1 - 2 + reach)
    new_y1 = top + 2 if top is not None else y1
    if new_x2 - new_x1 < guess_width * 0.8 or new_y1 >= y2:
        return guess
    return (new_x1, new_y1, new_x2, y2)


def detect_caption_reference(
    paths: Iterable[Path], samples: int = REFERENCE_SAMPLES
) -> CaptionReference:
    """Vote the caption box over up to ``samples`` images spread over the batch."""
    files = list(paths)
    if not files:
        raise ValueError("Nenhuma imagem para localizar a legenda")
    count = min(samples, len(files))
    indices = sorted({round(i * (len(files) - 1) / max(1, count - 1)) for i in range(count)})
    found: list[tuple[float, float, float, float]] = []
    last_error: Optional[Exception] = None
    for index in indices:
        try:
            with Image.open(files[index]) as source:
                img = source.convert("RGB")
            x1, y1, x2, y2 = detect_caption_box(img)
        except Exception as exc:
            last_error = exc
            continue
        width, height = img.size
        found.append((x1 / width, y1 / height, x2 / width, y2 / height))
    if not found:
        raise RuntimeError(
            str(last_error) if last_error else "Não foi possível localizar a caixa da legenda"
        )
    medians = tuple(sorted(side)[len(side) // 2] for side in zip(*found))
    return CaptionReference(medians)  # type: ignore[arg-type]


def detect_caption_layout(
    img: Image.Image, reference: Optional[CaptionReference] = None
) -> CaptionLayout:
    """Locate the caption box and every text line, pixel-exact.

    With a batch ``reference`` the box comes from it and only the lines are
    searched, which keeps hard backgrounds from moving the box.
    """
    img = img.convert("RGB")
    box = (
        _refine_box(img, reference.box_for(img.size))
        if reference
        else detect_caption_box(img)
    )
    x1, y1, x2, y2 = box
    inset = 3
    if x2 - x1 <= 2 * inset + 6 or y2 - y1 < 6:
        raise RuntimeError("Não foi possível localizar a caixa da legenda")
    area = (x1 + inset, y1, x2 - inset, y2)
    mask = _text_mask(img.crop(area))
    mw, mh = mask.size
    data = mask.tobytes()
    row_counts = [mw - data[y * mw : (y + 1) * mw].count(0) for y in range(mh)]
    min_height = max(6, round(img.height * 0.004))
    lines: list[Box] = []
    for top, bottom in _runs([count >= 2 for count in row_counts], max_gap=3):
        if bottom - top + 1 < min_height:
            continue
        band = mask.crop((0, top, mw, bottom + 1))
        columns = _edge_scores(band, "x")
        active = [x for x, share in enumerate(columns) if share > 0]
        if not active:
            continue
        lines.append(
            (
                area[0] + min(active),
                area[1] + top,
                area[0] + max(active) + 1,
                area[1] + bottom + 1,
            )
        )
    if not lines:
        raise RuntimeError("Nenhuma linha de texto foi encontrada na legenda")
    return CaptionLayout(box=box, lines=tuple(lines))


def _ocr_line(img: Image.Image, line: Box) -> str:
    pad = 6
    x1, y1, x2, y2 = line
    crop = img.crop((x1 - pad, y1 - pad, x2 + pad, y2 + pad)).convert("L")
    crop = ImageChops.invert(crop)
    text = pytesseract.image_to_string(crop, config="--psm 7").strip()
    return re.sub(r"\s+", " ", text)


def read_caption_lines(
    img: Image.Image, layout: Optional[CaptionLayout] = None
) -> list[str]:
    """OCR every caption line. Unreadable lines come back as empty strings."""
    img = img.convert("RGB")
    layout = layout or detect_caption_layout(img)
    texts: list[str] = []
    for line in layout.lines:
        try:
            texts.append(_ocr_line(img, line))
        except Exception:
            texts.append("")
    return texts


def erase_caption_line(img: Image.Image, line: Box, box: Box) -> None:
    """Remove glyph pixels and rebuild the (possibly translucent) background.

    Only the letters are replaced, filled from the surrounding box pixels, so
    photo texture showing through a translucent box survives.
    """
    pad = 5
    region = (
        max(box[0], line[0] - pad),
        max(box[1], line[1] - pad),
        min(box[2], line[2] + pad),
        min(box[3], line[3] + pad),
    )
    crop = img.crop(region)
    # Grow past the anti-aliased edge and the faint dark halo some caption
    # renderers add, so neither survives as a ghost of the old letters.
    mask = _threshold(crop.convert("L"), 110).filter(ImageFilter.MaxFilter(7))
    valid = ImageChops.invert(mask)
    if valid.getbbox() is None:
        # No background left to sample (a very light box): use its darkest tone.
        fill = sample_subtitle_background(crop)
        img.paste(Image.new("RGB", crop.size, fill), region[:2], mask)
        return
    layer = crop.convert("RGBA")
    layer.putalpha(valid)
    filled = crop
    remaining = mask
    # Normalized convolution: a premultiplied blur fills each hole from the
    # valid pixels around it. Small radii keep local texture; larger ones only
    # fill what the smaller ones could not reach.
    for radius in (2, 4, 8, 16):
        blurred = (
            layer.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")
        )
        reached = _threshold(blurred.getchannel("A"), 40) if radius < 16 else remaining
        take = ImageChops.multiply(remaining, reached)
        filled = Image.composite(blurred.convert("RGB"), filled, take)
        remaining = ImageChops.subtract(remaining, take)
        if remaining.getbbox() is None:
            break
    img.paste(filled, region[:2])


def _text_color(img: Image.Image, line: Box) -> tuple[int, int, int]:
    crop = img.crop(line).convert("RGB")
    bright = [pixel for pixel in crop.getdata() if min(pixel) > 200]
    if not bright:
        return (255, 255, 255)
    bright.sort(key=sum)
    return bright[len(bright) // 2]


def fit_line_font_size(reference_text: str, line: Box) -> int:
    """Font size whose rendering of the original text matches its glyph bounds."""
    width = line[2] - line[0]
    height = line[3] - line[1]
    text = reference_text or "0123456789"
    best_size = max(8, height)
    best_score = float("inf")
    for size in range(max(8, height // 2), height * 2 + 1):
        # Ink-only bounds, like the measured line (getbbox adds side bearing).
        bbox = find_font(size).getmask(text).getbbox()
        if bbox is None:
            continue
        text_height = bbox[3] - bbox[1]
        score = abs(text_height - height) / height
        if reference_text:
            score += 0.5 * abs((bbox[2] - bbox[0]) - width) / width
        if score < best_score:
            best_score = score
            best_size = size
    return best_size


def render_free_line(
    img: Image.Image,
    layout: CaptionLayout,
    line_number: int,
    text: str,
    reference_text: str = "",
) -> None:
    """Replace caption line ``line_number`` (1-based) with ``text`` in place."""
    if not 1 <= line_number <= len(layout.lines):
        raise ValueError(
            f"A legenda tem {len(layout.lines)} linhas; a linha {line_number} não existe"
        )
    line = layout.lines[line_number - 1]
    box = layout.box
    font_size = fit_line_font_size(reference_text, line)
    font = find_font(font_size)

    side_margin = max(4, round((box[2] - box[0]) * 0.01))
    new_bbox = font.getbbox(text)
    if new_bbox[2] - new_bbox[0] > box[2] - box[0] - 2 * side_margin:
        raise ValueError(
            "O texto é longo demais para esta linha. Encurte-o para continuar."
        )

    # Keep the original baseline: place the reference text's top at the line top.
    reference_bbox = font.getbbox(reference_text or "0123456789", anchor="ls")
    baseline = line[1] - reference_bbox[1]
    center_x = (line[0] + line[2]) / 2
    # An off-center line keeps its center, but never pushes text past the box.
    half_width = (new_bbox[2] - new_bbox[0]) / 2
    center_x = min(
        max(center_x, box[0] + side_margin + half_width),
        box[2] - side_margin - half_width,
    )
    color = _text_color(img, line)

    erase_caption_line(img, line, box)

    supersample = 4
    left, top, right, bottom = box
    layer = Image.new(
        "RGBA",
        ((right - left) * supersample, (bottom - top) * supersample),
        (0, 0, 0, 0),
    )
    # Center the ink (not the advance box) on the original line's ink center.
    big_font = find_font(font_size * supersample)
    ink = big_font.getmask(text).getbbox()
    origin = big_font.getbbox(text, anchor="ls")[0]
    ink_center = origin + ((ink[0] + ink[2]) / 2 if ink else 0)
    ImageDraw.Draw(layer).text(
        ((center_x - left) * supersample - ink_center, (baseline - top) * supersample),
        text,
        fill=(*color, 255),
        font=big_font,
        anchor="ls",
    )
    layer = layer.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
    img.paste(layer, (left, top), layer)


def render_edited_image(
    input_path: Path,
    sre_number: int = 10,
    *,
    full_line_text: Optional[str] = None,
    line_number: Optional[int] = None,
    caption_reference: Optional[CaptionReference] = None,
    expected_line_count: Optional[int] = None,
) -> Image.Image:
    """Edit one image.

    With ``line_number`` set (free mode), caption line ``line_number`` (1-based,
    counted top to bottom) is replaced by ``full_line_text``. Otherwise the
    legacy third-line edit runs.
    """
    format_sre_number(sre_number)
    with Image.open(input_path) as source:
        img = source.convert("RGB")

    if line_number is not None:
        replacement = (full_line_text or "").strip()
        if not replacement:
            raise ValueError("Digite o novo texto da linha")
        if "\n" in replacement or "\r" in replacement:
            raise ValueError("A linha não pode conter uma quebra de linha")
        layout = detect_caption_layout(img, caption_reference)
        if expected_line_count is not None and len(layout.lines) != expected_line_count:
            # Lines are picked by position, so a different count could mean
            # replacing the wrong line; refuse instead.
            raise ValueError(
                f"A legenda desta imagem tem {len(layout.lines)} linhas, "
                f"mas a da primeira imagem tem {expected_line_count}"
            )
        if not 1 <= line_number <= len(layout.lines):
            raise ValueError(
                f"A legenda tem {len(layout.lines)} linhas; "
                f"a linha {line_number} não existe"
            )
        try:
            reference = _ocr_line(img, layout.lines[line_number - 1])
        except Exception:
            reference = ""
        render_free_line(img, layout, line_number, replacement, reference)
        return img

    if full_line_text is None:
        replacement = read_line_3(img, sre_number)
    else:
        replacement = full_line_text.strip()
        if not replacement:
            raise ValueError("Digite o texto da terceira linha")
        if "\n" in replacement or "\r" in replacement:
            raise ValueError("A terceira linha não pode conter uma quebra de linha")

    line_box = detect_line_3_box(img)
    target_bounds = measure_text_bounds(img.crop(line_box))
    line_size = (line_box[2] - line_box[0], line_box[3] - line_box[1])
    fixed_font_size: Optional[int] = None
    if full_line_text is not None:
        if not text_fits_line(replacement, line_size, target_bounds):
            raise ValueError(
                "O texto é longo demais para a terceira linha. Encurte-o para continuar."
            )
        fixed_font_size = source_font_size(line_size, target_bounds)

    erase_old_line_text(img, line_box)
    draw_replacement_line(
        img,
        line_box,
        replacement,
        target_bounds,
        fixed_font_size=fixed_font_size,
    )
    return img


def encode_edited_image(
    input_path: Path,
    sre_number: int = 10,
    *,
    full_line_text: Optional[str] = None,
    line_number: Optional[int] = None,
    caption_reference: Optional[CaptionReference] = None,
    expected_line_count: Optional[int] = None,
    output_suffix: Optional[str] = None,
) -> bytes:
    """Render and encode one image for both previews and batch output."""
    img = render_edited_image(
        input_path,
        sre_number,
        full_line_text=full_line_text,
        line_number=line_number,
        caption_reference=caption_reference,
        expected_line_count=expected_line_count,
    )
    suffix = (output_suffix or input_path.suffix).lower()
    formats = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
    image_format = formats.get(suffix)
    if image_format is None:
        raise ValueError(f"Formato de saída incompatível: {suffix}")
    save_options = {"quality": 95} if image_format != "PNG" else {}
    buffer = BytesIO()
    img.save(buffer, format=image_format, **save_options)
    return buffer.getvalue()


def edit_image(
    input_path: Path,
    output_path: Path,
    sre_number: int = 10,
    *,
    full_line_text: Optional[str] = None,
    line_number: Optional[int] = None,
    caption_reference: Optional[CaptionReference] = None,
    expected_line_count: Optional[int] = None,
) -> None:
    encoded = encode_edited_image(
        input_path,
        sre_number,
        full_line_text=full_line_text,
        line_number=line_number,
        caption_reference=caption_reference,
        expected_line_count=expected_line_count,
        output_suffix=output_path.suffix,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encoded)


def is_supported_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def collect_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if is_supported_file(input_path) else []
    if not input_path.is_dir():
        return []
    return sorted(path for path in input_path.iterdir() if is_supported_file(path))


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.expanduser().resolve()).casefold()
        if resolved not in seen and is_supported_file(path):
            seen.add(resolved)
            unique.append(path.expanduser().resolve())
    return unique


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _limit_filename_bytes(filename: str, max_bytes: int) -> str:
    if len(filename.encode("utf-8")) <= max_bytes:
        return filename
    source = Path(filename)
    extension = source.suffix
    stem_budget = max_bytes - len(extension.encode("utf-8"))
    return f"{_truncate_utf8(source.stem, stem_budget)}{extension}"


def _full_line_slug(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", errors="ignore")
        .decode("ascii")
        .lower()
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_") or "editada"


def edited_filename(
    filename: str,
    sre_number: int,
    *,
    full_line_text: Optional[str] = None,
) -> str:
    """Build a distinct, portable filename for a desktop-app result."""
    source = Path(filename).name
    path = Path(source)
    extension = path.suffix.lower()
    stem = path.stem
    filename_budget = MAX_EDITED_FILENAME_BYTES - _COLLISION_COUNTER_RESERVE_BYTES
    content_budget = filename_budget - len(extension.encode("utf-8"))

    if full_line_text is None:
        number = format_sre_number(sre_number)
        base_stem = re.sub(r"(?i)_SRE\d{3}$", "", stem) or stem
        generated_suffix = f"_SRE{number}"
        stem_budget = content_budget - len(generated_suffix.encode("utf-8"))
        fitted_stem = _truncate_utf8(base_stem, stem_budget)
        return f"{fitted_stem}{generated_suffix}{extension}"

    slug = _full_line_slug(full_line_text)
    separator_bytes = len("_".encode("utf-8"))
    slug_budget = content_budget - len(stem.encode("utf-8")) - separator_bytes
    if slug_budget >= 1:
        fitted_stem = stem
        fitted_slug = _truncate_utf8(slug, slug_budget).rstrip("_")
    else:
        fitted_slug = slug[0]
        stem_budget = content_budget - separator_bytes - len(
            fitted_slug.encode("utf-8")
        )
        fitted_stem = _truncate_utf8(stem, stem_budget)
    return f"{fitted_stem}_{fitted_slug}{extension}"


def collision_safe_path(
    output_dir: Path,
    filename: str,
    *,
    max_filename_bytes: Optional[int] = None,
) -> Path:
    safe_filename = (
        _limit_filename_bytes(filename, max_filename_bytes)
        if max_filename_bytes is not None
        else filename
    )
    candidate = output_dir / safe_filename
    if not candidate.exists():
        return candidate
    source = Path(safe_filename)
    counter = 2
    while True:
        counter_suffix = f" ({counter})"
        stem = source.stem
        if max_filename_bytes is not None:
            stem_budget = (
                max_filename_bytes
                - len(counter_suffix.encode("utf-8"))
                - len(source.suffix.encode("utf-8"))
            )
            stem = _truncate_utf8(stem, stem_budget)
        candidate = output_dir / f"{stem}{counter_suffix}{source.suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
