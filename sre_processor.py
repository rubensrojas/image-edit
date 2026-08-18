from __future__ import annotations

import os
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFont
import pytesseract


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

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


def render_edited_image(
    input_path: Path,
    sre_number: int = 10,
    *,
    full_line_text: Optional[str] = None,
) -> Image.Image:
    format_sre_number(sre_number)
    with Image.open(input_path) as source:
        img = source.convert("RGB")

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
    output_suffix: Optional[str] = None,
) -> bytes:
    """Render and encode one image for both previews and batch output."""
    img = render_edited_image(
        input_path,
        sre_number,
        full_line_text=full_line_text,
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
) -> None:
    encoded = encode_edited_image(
        input_path,
        sre_number,
        full_line_text=full_line_text,
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


def collision_safe_path(output_dir: Path, filename: str) -> Path:
    candidate = output_dir / filename
    if not candidate.exists():
        return candidate
    source = Path(filename)
    counter = 2
    while True:
        candidate = output_dir / f"{source.stem} ({counter}){source.suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
