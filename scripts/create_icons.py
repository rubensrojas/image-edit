#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def make_icon(size: int = 1024) -> Image.Image:
    scale = size / 512
    image = Image.new("RGBA", (size, size), "#171717")
    draw = ImageDraw.Draw(image)
    radius = round(112 * scale)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill="#171717")
    draw.rounded_rectangle(
        tuple(round(value * scale) for value in (94, 112, 418, 400)),
        radius=round(42 * scale),
        fill="#232323",
        outline="#D4AF37",
        width=round(18 * scale),
    )
    draw.polygon(
        [(round(x * scale), round(y * scale)) for x, y in ((144, 326), (220, 250), (276, 306), (319, 263), (368, 312), (368, 350), (144, 350))],
        fill="#D4AF37",
    )
    draw.ellipse(
        tuple(round(value * scale) for value in (295, 163, 365, 233)),
        fill="#F5F3EC",
    )
    return image


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    icon = make_icon()
    icon.save(BUILD / "app_icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(BUILD / "app_icon.icns")


if __name__ == "__main__":
    main()
