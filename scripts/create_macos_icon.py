"""Generate the PNG sizes required for a macOS .icns file."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


SIZES = {
    "16": 16,
    "16@2x": 32,
    "32": 32,
    "32@2x": 64,
    "128": 128,
    "128@2x": 256,
    "256": 256,
    "256@2x": 512,
    "512": 512,
    "512@2x": 1024,
}


def make_icon(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (24, 33, 38, 255))
    draw = ImageDraw.Draw(canvas)
    radius = max(2, round(size * 0.22))
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(24, 33, 38, 255))

    # A clean vector-style paw mark remains legible at small Dock sizes.
    mint = (126, 231, 190, 255)
    cx = size * 0.50
    cy = size * 0.59
    pad_w = size * 0.27
    pad_h = size * 0.22
    draw.ellipse((cx - pad_w, cy - pad_h, cx + pad_w, cy + pad_h), fill=mint)
    toe_r = size * 0.085
    toes = [
        (size * 0.28, size * 0.37),
        (size * 0.42, size * 0.27),
        (size * 0.58, size * 0.27),
        (size * 0.72, size * 0.37),
    ]
    for x, y in toes:
        draw.ellipse((x - toe_r, y - toe_r, x + toe_r, y + toe_r), fill=mint)
    return canvas


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: create_macos_icon.py ICONSET_DIR", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    source = make_icon(1024)
    for name, size in SIZES.items():
        image = source if size == 1024 else source.resize((size, size), Image.Resampling.LANCZOS)
        image.save(target / f"icon_{name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
