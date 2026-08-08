"""Dev-time, one-off converter: assets/nexo-pixel.png -> the static ANSI pixel art
embedded in src/nexolith/cli/nexo_art.py.

Not part of the shipped package and not a project dependency. Run with Pillow and
NumPy available ephemerally, e.g.:

    uv run --with pillow --with numpy python scripts/render_nexo_pixel_art.py

Prints a Python source fragment (palette + pixel grid) to paste into nexo_art.py.
Re-run only if the source asset changes.
"""

from pathlib import Path

import numpy as np
from PIL import Image

SOURCE = Path(__file__).resolve().parent.parent / "assets" / "nexo-pixel.png"
COLS = 34
ROWS = 14  # logical pixel rows; renders as ROWS // 2 terminal lines via half-blocks

# Small, hand-picked, blue-toned (Discord-like) palette. Colors were sampled from the
# source image itself, then nudged toward Discord's canonical blurple/near-black/white.
PALETTE = {
    "B": (0x58, 0x65, 0xF2),  # body (Discord blurple; source sampled ~(83,91,246))
    "S": (0x36, 0x36, 0xAC),  # body shading / belly marking (darker blue-purple)
    "W": (0xFF, 0xFF, 0xFF),  # eye highlight
}
BRIGHTNESS_FLOOR = 40  # below this, treat as background/eye-pupil (no block)


def nearest_marker(pixel: tuple[int, int, int]) -> str | None:
    r, g, b = pixel
    if r + g + b < BRIGHTNESS_FLOOR:
        return None
    best_marker, best_distance = None, float("inf")
    for marker, (pr, pg, pb) in PALETTE.items():
        distance = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if distance < best_distance:
            best_marker, best_distance = marker, distance
    return best_marker


def main() -> None:
    image = Image.open(SOURCE).convert("RGB")
    array = np.array(image)

    # Isolate the body silhouette (excludes the dark mound/debris below it, which is
    # too small to survive this grid size and was cropped out per product decision).
    body_mask = array[:, :, 2] > 200
    ys, xs = np.where(body_mask)
    pad = 3
    left, right = max(xs.min() - pad, 0), min(xs.max() + pad, array.shape[1])
    top = max(ys.min() - pad, 0)
    # The mask's bottom edge is a fuzzy anti-aliased transition into the mound at any
    # reasonable threshold; trim it rather than let it enter the grid as noise.
    bottom = ys.max() - int(0.05 * (ys.max() - ys.min()))

    cropped = image.crop((left, top, right, bottom))
    # NEAREST, not a blending filter: the source is already flat-color pixel art, so
    # blending (LANCZOS/BOX) manufactures anti-aliased edge noise that isn't there in
    # the original and pollutes the small palette with muddy in-between colors.
    small = cropped.resize((COLS, ROWS), Image.Resampling.NEAREST)

    rows = []
    for y in range(ROWS):
        row = "".join(nearest_marker(tuple(small.getpixel((x, y)))) or "." for x in range(COLS))
        rows.append(row)

    print("_PALETTE: dict[str, tuple[int, int, int]] = {")
    for marker, (r, g, b) in PALETTE.items():
        print(f'    "{marker}": (0x{r:02X}, 0x{g:02X}, 0x{b:02X}),')
    print("}")
    print()
    print("_PIXELS: tuple[str, ...] = (")
    for row in rows:
        print(f'    "{row}",')
    print(")")


if __name__ == "__main__":
    main()
