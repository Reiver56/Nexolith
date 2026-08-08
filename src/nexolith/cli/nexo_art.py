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

Also the shared color-tier module for every raw-ANSI renderer in `cli/`
(`runs_render.py`, `scheduler_render.py`, `status_area.py`) -- NXL-100: a
real terminal was found that prints an unrecognized 24-bit `38;2;r;g;b`
sequence back as literal text instead of degrading it. `colorize()` is the
one place that decides, per call, whether to actually emit truecolor or a
nearest-256-color approximation, based on the real `RenderContext.truecolor`
signal -- no renderer should ever hardcode a `38;2;...` sequence directly
again. It's also (NXL-104) the one place that decides whether to emit any
escape code at all, based on `RenderContext.ansi_capable` -- for a sink that
cannot interpret ANSI (e.g. the full-screen session's scrollable output log)
regardless of how capable the real terminal otherwise is.

`panel_lines()` is the shared rounded-bordered-panel renderer every one of
those callers builds its final result/summary/detail display from -- one
implementation, reused rather than duplicated per caller, so DAG and
pipeline results (and anything else) render with an identical visual style.
"""

import textwrap

from nexolith import __version__
from nexolith.cli.render_context import RenderContext

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
# Reserved specifically for DAG severity labeling (NXL-87, cli/runs_render.py)
# -- distinct from RED above, which already means "this run failed" on the
# same row; severity needs its own scale so "critical, failed" and "low,
# failed" don't render identically. No amber/yellow tone existed in this
# palette before this story (confirmed by reading this file, not assumed).
AMBER: tuple[int, int, int] = (0xFA, 0xA6, 0x1A)

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

# --- Color tiers (NXL-100) -----------------------------------------------
#
# The xterm 256-color palette's non-system entries: a 6x6x6 RGB cube
# (indices 16-231, each channel quantized to one of six fixed levels) plus a
# 24-step grayscale ramp (232-255). Standard, well-known approximation
# algorithm (the same shape most terminal-color libraries use) -- pick
# whichever of the two (nearest cube point, nearest gray step) is closer in
# squared Euclidean distance to the real 24-bit color.
_CUBE_LEVELS: tuple[int, ...] = (0, 95, 135, 175, 215, 255)


def _nearest_cube_index(value: int) -> int:
    return min(range(6), key=lambda i: abs(_CUBE_LEVELS[i] - value))


def _ansi_256_index(rgb: tuple[int, int, int]) -> int:
    """Nearest xterm 256-color palette index for a real 24-bit color --
    what every `colorize()` call actually emits when `RenderContext.truecolor`
    is False, instead of a raw `38;2;r;g;b` sequence a real (non-truecolor)
    terminal may not understand at all (NXL-100).
    """
    r, g, b = rgb
    ri, gi, bi = _nearest_cube_index(r), _nearest_cube_index(g), _nearest_cube_index(b)
    cube_rgb = (_CUBE_LEVELS[ri], _CUBE_LEVELS[gi], _CUBE_LEVELS[bi])
    cube_index = 16 + 36 * ri + 6 * gi + bi
    cube_distance = sum((a - b) ** 2 for a, b in zip(rgb, cube_rgb, strict=True))

    gray_step = max(0, min(23, round((sum(rgb) / 3 - 8) / 10)))
    gray_value = 8 + gray_step * 10
    gray_index = 232 + gray_step
    gray_distance = sum((channel - gray_value) ** 2 for channel in rgb)

    return gray_index if gray_distance < cube_distance else cube_index


def _fg_sgr(rgb: tuple[int, int, int], *, truecolor: bool) -> str:
    if truecolor:
        red, green, blue = rgb
        return f"\x1b[38;2;{red};{green};{blue}m"
    return f"\x1b[38;5;{_ansi_256_index(rgb)}m"


def _bg_sgr(rgb: tuple[int, int, int], *, truecolor: bool) -> str:
    if truecolor:
        red, green, blue = rgb
        return f"\x1b[48;2;{red};{green};{blue}m"
    return f"\x1b[48;5;{_ansi_256_index(rgb)}m"


def colorize(
    text: str, rgb: tuple[int, int, int], render_context: RenderContext, *, bold: bool = False
) -> str:
    """Colorize `text` at whichever tier `render_context` actually supports:
    real 24-bit truecolor only when genuinely detected
    (`render_context.truecolor`), otherwise the nearest 256-color
    approximation -- safe on effectively every terminal that supports ANSI
    color at all, unlike a raw `38;2;r;g;b` sequence a non-truecolor
    terminal may print back as literal text instead of degrading itself
    (NXL-100). The one function every renderer in this package must go
    through instead of hardcoding an SGR sequence directly.

    Callers must still consult `render_context.plain` themselves before
    calling this at all -- this always produces color codes, it never
    decides whether color should happen in the first place. The one
    exception is `render_context.ansi_capable` (NXL-104): when False, `text`
    is returned completely unchanged (no SGR codes, no bold, no reset) --
    the destination cannot interpret any escape code at all, regardless of
    what `plain` says about the real terminal's own capability.
    """
    if not render_context.ansi_capable:
        return text
    prefix = "\x1b[1m" if bold else ""
    return f"{prefix}{_fg_sgr(rgb, truecolor=render_context.truecolor)}{text}{_RESET}"


def _color(marker: str) -> tuple[int, int, int] | None:
    return _PALETTE.get(marker)


def _cell(top: str, bottom: str, render_context: RenderContext) -> str:
    top_color = _color(top)
    bottom_color = _color(bottom)
    truecolor = render_context.truecolor
    if top_color is None and bottom_color is None:
        return " "
    if top_color is not None and bottom_color is not None:
        return (
            f"{_fg_sgr(top_color, truecolor=truecolor)}"
            f"{_bg_sgr(bottom_color, truecolor=truecolor)}{_UPPER_HALF}{_RESET}"
        )
    if top_color is not None:
        return f"{_fg_sgr(top_color, truecolor=truecolor)}{_UPPER_HALF}{_RESET}"
    assert bottom_color is not None  # only remaining case, after the branches above
    return f"{_fg_sgr(bottom_color, truecolor=truecolor)}{_LOWER_HALF}{_RESET}"


def render_nexo_pixel_art(render_context: RenderContext) -> str:
    """Render Nexo as ANSI half-block characters, at whichever color tier
    `render_context` actually supports (NXL-100 -- truecolor only when
    genuinely detected, a 256-color approximation otherwise).

    Callers must consult `RenderContext.plain` before using this — it always
    produces colored output and never checks terminal capability itself.
    """
    lines = []
    for top_row, bottom_row in zip(_PIXELS[0::2], _PIXELS[1::2], strict=True):
        lines.append(
            "".join(
                _cell(top, bottom, render_context)
                for top, bottom in zip(top_row, bottom_row, strict=True)
            )
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


def _panel_title_line(render_context: RenderContext) -> str:
    title_text = f" Nexolith v{__version__} "
    remaining = _PANEL_INTERIOR_WIDTH - len(title_text)
    if remaining < 0:
        # Title too long for the panel (e.g. an unexpectedly long version string);
        # degrade to a plain titleless border rather than overflow the panel width.
        border = _CORNER_TOP_LEFT + _HORIZONTAL * _PANEL_INTERIOR_WIDTH + _CORNER_TOP_RIGHT
        return colorize(border, _BORDER_COLOR, render_context)
    left = remaining // 2
    right = remaining - left
    return (
        colorize(_CORNER_TOP_LEFT + _HORIZONTAL * left, _BORDER_COLOR, render_context)
        + colorize(title_text, _TITLE_COLOR, render_context, bold=True)
        + colorize(_HORIZONTAL * right + _CORNER_TOP_RIGHT, _BORDER_COLOR, render_context)
    )


def _panel_bottom_line(render_context: RenderContext) -> str:
    border = _CORNER_BOTTOM_LEFT + _HORIZONTAL * _PANEL_INTERIOR_WIDTH + _CORNER_BOTTOM_RIGHT
    return colorize(border, _BORDER_COLOR, render_context)


def _panel_content_line(art_line: str, render_context: RenderContext) -> str:
    vertical = colorize(_VERTICAL, _BORDER_COLOR, render_context)
    return f"{vertical}{_PADDING}{art_line}{_PADDING}{vertical}"


def render_nexo_panel(render_context: RenderContext) -> str:
    """Render Nexo's tier-2 art inside a bordered panel: rounded corners, a
    titled top border, one column of padding on each side. Same static content
    for the startup splash and the prompt's idle state — no animation.

    Callers must consult `RenderContext.plain` before using this, same as
    `render_nexo_pixel_art()` — it always produces colored output.
    """
    art_lines = render_nexo_pixel_art(render_context).split("\n")
    lines = [_panel_title_line(render_context)]
    lines.extend(_panel_content_line(line, render_context) for line in art_lines)
    lines.append(_panel_bottom_line(render_context))
    return "\n".join(lines)


# --- Generic result/summary panel (NXL-93, moved here from runs_render.py
# in NXL-104 so status_area.py's classic-pipeline result can reuse the exact
# same primitive as runs_render.py's DAG result, rather than each keeping
# its own bordered-panel implementation) -------------------------------


def _wrap_panel_row(
    label: str, value: str, color: tuple[int, int, int] | None, interior_width: int
) -> list[tuple[str, str, tuple[int, int, int] | None]]:
    """One row -> one or more `(prefix, chunk, color)` pieces. A short
    value (the common case) always produces exactly one piece with
    `prefix = "{label}: "`, identical to this function not existing at
    all. A value too long to fit `interior_width` wraps onto continuation
    pieces instead, each using a same-width blank indent in place of
    repeating the label, so every piece can be padded and bordered by the
    same uniform logic regardless of whether it's a row's first line or a
    continuation of one.
    """
    prefix = f"{label}: "
    indent = " " * len(prefix)
    budget = max(1, interior_width - len(prefix))
    chunks = textwrap.wrap(value, width=budget) or [""]
    pieces = [(prefix, chunks[0], color)]
    pieces.extend((indent, chunk, color) for chunk in chunks[1:])
    return pieces


def panel_lines(
    rows: list[tuple[str, str, tuple[int, int, int] | None]], render_context: RenderContext
) -> list[str]:
    """Render `rows` (label, value, optional value color) as a bordered
    panel: rounded Unicode corners with `colorize()`d values whenever
    `render_context` allows it, a plain ASCII `+---+` rectangle otherwise
    (NXL-80's encoding-safe fallback). `render_context.width` bounds every
    line's total visible width (NXL-93) -- a value long enough to overflow
    it wraps onto its own bordered, padded continuation line instead of
    letting the terminal's own line-wrap misalign the border.

    Border shape depends only on `render_context.plain` (real terminal
    capability); whether values actually get colored additionally depends
    on `render_context.ansi_capable` (NXL-104), via `colorize()` itself --
    so a genuinely capable terminal still gets the rounded shape even when
    the caller is writing into an ANSI-incapable sink such as the
    full-screen session's plain-text output log.
    """
    plain = render_context.plain
    interior_width = max(4, render_context.width - 4)  # "│ " + content + " │"
    pieces: list[tuple[str, str, tuple[int, int, int] | None]] = []
    for label, value, color in rows:
        pieces.extend(_wrap_panel_row(label, value, color, interior_width))

    plain_lines = [f"{prefix}{chunk}" for prefix, chunk, _color in pieces]
    width = max(len(line) for line in plain_lines)

    if plain:
        border = "+" + "-" * (width + 2) + "+"
        body = [f"| {line.ljust(width)} |" for line in plain_lines]
        return [border, *body, border]

    top = colorize("╭" + "─" * (width + 2) + "╮", BLURPLE, render_context)
    bottom = colorize("╰" + "─" * (width + 2) + "╯", BLURPLE, render_context)
    side = colorize("│", BLURPLE, render_context)
    body = []
    for (prefix, chunk, color), plain_line in zip(pieces, plain_lines, strict=True):
        pad = " " * (width - len(plain_line))
        chunk_text = (
            colorize(chunk, color, render_context, bold=True) if color is not None else chunk
        )
        body.append(f"{side} {prefix}{chunk_text}{pad} {side}")
    return [top, *body, bottom]
