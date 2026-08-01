"""Colored pixel-art rendering of Nexo, degrading to plain text via `RenderContext`.

The grid below was converted from `assets/nexo-pixel.png` by
`scripts/render_nexo_pixel_art.py` (a dev-time, one-off tool; not a runtime
dependency). It is a small 34x14 flattening of that source image, cropped to the
body silhouette (the dark mound/debris beneath it does not survive at this size and
was intentionally left out), rendered here as 34x7 terminal cells using Unicode
half-block characters to double the effective vertical resolution.
"""

# Discord-like blue palette, sampled from the source image and nudged toward
# Discord's canonical blurple/white. Dark tones are reserved for shading the body
# silhouette itself, not a separate feature, matching the source's own limited palette.
_PALETTE: dict[str, tuple[int, int, int]] = {
    "B": (0x58, 0x65, 0xF2),  # body (Discord blurple)
    "S": (0x36, 0x36, 0xAC),  # body shading (darker blue-purple)
    "W": (0xFF, 0xFF, 0xFF),  # eye highlight
}

_PIXELS: tuple[str, ...] = (
    "..............BBBBBBB.............",
    ".............BBBBBBBBB............",
    "......BBBBBBBBBBBBBBBB.........SSS",
    "....BBBBBBBBBBBBBBBBBBB...BBBBBBB.",
    "...BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB.",
    "..BBBBBBBBBB....BBBBBBBBBBBBBBBB..",
    ".BBBBBBBBBB.WW.W.BBBBBBBBBBBBBB...",
    "BBBBBBBBBBBBB....BBBBBBBBBBBBS....",
    "BBBBBBBBBBBBBBBBBBBBBBBBBBBB......",
    "BBBBBBBBBBBBBBBBBBBBSSSBBBBB......",
    "SBBBBBBBBBBBBBBBBBBSSSSSBB........",
    "SSBBBBBBBBBBBBBBBBBBSSSBBB........",
    "..BBBBBBBBBBBBBBBBBBBBBBBB........",
    "..SSBBBBBBBBBBBBBBBBBBBBBB........",
)

_UPPER_HALF = "▀"
_LOWER_HALF = "▄"
_RESET = "\x1b[0m"


def _color(marker: str) -> tuple[int, int, int] | None:
    return _PALETTE.get(marker)


def _cell(top: str, bottom: str) -> str:
    top_color = _color(top)
    bottom_color = _color(bottom)
    if top_color is None and bottom_color is None:
        return " "
    if top_color is not None and bottom_color is not None:
        tr, tg, tb = top_color
        br, bg, bb = bottom_color
        return f"\x1b[38;2;{tr};{tg};{tb}m\x1b[48;2;{br};{bg};{bb}m{_UPPER_HALF}{_RESET}"
    if top_color is not None:
        tr, tg, tb = top_color
        return f"\x1b[38;2;{tr};{tg};{tb}m{_UPPER_HALF}{_RESET}"
    assert bottom_color is not None  # only remaining case, after the branches above
    br, bg, bb = bottom_color
    return f"\x1b[38;2;{br};{bg};{bb}m{_LOWER_HALF}{_RESET}"


def render_nexo_pixel_art() -> str:
    """Render Nexo as ANSI truecolor half-block characters.

    Callers must consult `RenderContext.plain` before using this — it always
    produces colored output and never checks terminal capability itself.
    """
    lines = []
    for top_row, bottom_row in zip(_PIXELS[0::2], _PIXELS[1::2], strict=True):
        lines.append(
            "".join(_cell(top, bottom) for top, bottom in zip(top_row, bottom_row, strict=True))
        )
    return "\n".join(lines)
