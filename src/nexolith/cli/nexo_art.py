"""Colored pixel-art rendering of Nexo, degrading to plain text via `RenderContext`.

The grid below was converted from `assets/nexo-pixel.png` by
`scripts/render_nexo_pixel_art.py` (a dev-time, one-off tool; not a runtime
dependency). It is a small 34x14 flattening of that source image, cropped to the
body silhouette (the dark mound/debris beneath it does not survive at this size and
was intentionally left out), rendered here as 34x7 terminal cells using Unicode
half-block characters to double the effective vertical resolution.

`render_nexo_panel()` wraps that art in a bordered panel (rounded corners, a
titled top border) for the splash/idle display; `render_nexo_pixel_art()` stays
available on its own for anything that wants the bare art.
"""

from nexolith import __version__

# Discord-like blue palette, sampled from the source image and nudged toward
# Discord's canonical blurple/white. Dark tones are reserved for shading the body
# silhouette itself, not a separate feature, matching the source's own limited palette.
BLURPLE: tuple[int, int, int] = (0x58, 0x65, 0xF2)  # Discord blurple
WHITE: tuple[int, int, int] = (0xFF, 0xFF, 0xFF)

# Semantic status colors, reserved specifically for success/error/neutral
# signaling (never used for the art itself) -- shared by every CLI renderer
# that needs them (the full-screen status area, and the scheduler/runs CLI
# commands). Defined here rather than in status_area.py, which has its own
# top-level prompt_toolkit import: classic, non-interactive commands must
# never pull that in just to get these three color tuples.
GREEN: tuple[int, int, int] = (0x57, 0xF2, 0x87)  # Discord green
RED: tuple[int, int, int] = (0xED, 0x42, 0x45)  # Discord red
DIM: tuple[int, int, int] = (0x8A, 0x8F, 0xA3)

_PALETTE: dict[str, tuple[int, int, int]] = {
    "B": BLURPLE,  # body
    "S": (0x36, 0x36, 0xAC),  # body shading (darker blue-purple)
    "W": WHITE,  # eye highlight
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


# --- Bordered panel -----------------------------------------------------

_PADDING = " "
_ART_WIDTH = len(_PIXELS[0])
_PANEL_INTERIOR_WIDTH = _ART_WIDTH + 2 * len(_PADDING)
_CORNER_TOP_LEFT = "╭"
_CORNER_TOP_RIGHT = "╮"
_CORNER_BOTTOM_LEFT = "╰"
_CORNER_BOTTOM_RIGHT = "╯"
_HORIZONTAL = "─"
_VERTICAL = "│"

_BORDER_COLOR = _PALETTE["B"]  # same blurple as the art, so the panel reads as one piece
_TITLE_COLOR = _PALETTE["W"]  # white, so the title stands out against the border


def _colorize(text: str, rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    red, green, blue = rgb
    prefix = "\x1b[1m" if bold else ""
    return f"{prefix}\x1b[38;2;{red};{green};{blue}m{text}{_RESET}"


def _panel_title_line() -> str:
    title_text = f" Nexolith v{__version__} "
    remaining = _PANEL_INTERIOR_WIDTH - len(title_text)
    if remaining < 0:
        # Title too long for the panel (e.g. an unexpectedly long version string);
        # degrade to a plain titleless border rather than overflow the panel width.
        border = _CORNER_TOP_LEFT + _HORIZONTAL * _PANEL_INTERIOR_WIDTH + _CORNER_TOP_RIGHT
        return _colorize(border, _BORDER_COLOR)
    left = remaining // 2
    right = remaining - left
    return (
        _colorize(_CORNER_TOP_LEFT + _HORIZONTAL * left, _BORDER_COLOR)
        + _colorize(title_text, _TITLE_COLOR, bold=True)
        + _colorize(_HORIZONTAL * right + _CORNER_TOP_RIGHT, _BORDER_COLOR)
    )


def _panel_bottom_line() -> str:
    border = _CORNER_BOTTOM_LEFT + _HORIZONTAL * _PANEL_INTERIOR_WIDTH + _CORNER_BOTTOM_RIGHT
    return _colorize(border, _BORDER_COLOR)


def _panel_content_line(art_line: str) -> str:
    vertical = _colorize(_VERTICAL, _BORDER_COLOR)
    return f"{vertical}{_PADDING}{art_line}{_PADDING}{vertical}"


def render_nexo_panel() -> str:
    """Render Nexo's tier-2 art inside a bordered panel: rounded corners, a
    titled top border, one column of padding on each side. Same static content
    for the startup splash and the prompt's idle state — no animation.

    Callers must consult `RenderContext.plain` before using this, same as
    `render_nexo_pixel_art()` — it always produces colored output.
    """
    art_lines = render_nexo_pixel_art().split("\n")
    lines = [_panel_title_line()]
    lines.extend(_panel_content_line(line) for line in art_lines)
    lines.append(_panel_bottom_line())
    return "\n".join(lines)
