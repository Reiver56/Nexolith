import re

from nexolith import __version__
from nexolith.cli.nexo_art import (
    _PALETTE,
    _PANEL_INTERIOR_WIDTH,
    _PIXELS,
    render_nexo_panel,
    render_nexo_pixel_art,
)
from nexolith.cli.render_context import MINIMUM_WIDTH

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visual_lines(rendered: str) -> list[str]:
    return [_ANSI_RE.sub("", line) for line in rendered.split("\n")]


def test_pixel_grid_has_an_even_row_count_for_half_block_pairing() -> None:
    assert len(_PIXELS) % 2 == 0


def test_render_nexo_pixel_art_halves_the_grid_into_terminal_lines() -> None:
    rendered = render_nexo_pixel_art()

    assert rendered.count("\n") == len(_PIXELS) // 2 - 1


def test_render_nexo_pixel_art_stays_a_compact_header() -> None:
    """Pin the compact-header sizing: well under MINIMUM_WIDTH, single-digit lines."""
    rendered = render_nexo_pixel_art()
    lines = rendered.split("\n")

    assert len(lines) < 10
    for line in _visual_lines(rendered):
        assert len(line) == len(_PIXELS[0])
    assert len(_PIXELS[0]) < MINIMUM_WIDTH


def test_render_nexo_pixel_art_uses_ansi_truecolor_and_resets() -> None:
    rendered = render_nexo_pixel_art()

    assert "\x1b[38;2;" in rendered
    assert "\x1b[0m" in rendered


def test_render_nexo_pixel_art_uses_only_the_declared_palette() -> None:
    rendered = render_nexo_pixel_art()

    for red, green, blue in _PALETTE.values():
        assert f"\x1b[38;2;{red};{green};{blue}m" in rendered


def test_render_nexo_pixel_art_body_color_is_discord_blurple() -> None:
    assert _PALETTE["B"] == (0x58, 0x65, 0xF2)


def test_render_nexo_pixel_art_is_deterministic() -> None:
    assert render_nexo_pixel_art() == render_nexo_pixel_art()


def test_render_nexo_panel_wraps_the_art_with_a_fixed_margin() -> None:
    """Pin the panel footprint: art rows plus exactly one title row and one
    bottom-border row, each line the same total width (art + 1-space padding
    each side + 1 border character each side)."""
    art_lines = render_nexo_pixel_art().split("\n")
    panel_lines = _visual_lines(render_nexo_panel())

    assert len(panel_lines) == len(art_lines) + 2
    expected_width = len(_PIXELS[0]) + 4  # padding (1+1) + border chars (1+1)
    assert expected_width == _PANEL_INTERIOR_WIDTH + 2
    for line in panel_lines:
        assert len(line) == expected_width


def test_render_nexo_panel_stays_well_under_the_narrow_terminal_threshold() -> None:
    panel_lines = _visual_lines(render_nexo_panel())

    assert len(panel_lines[0]) < MINIMUM_WIDTH
    assert len(panel_lines) < 10


def test_render_nexo_panel_has_rounded_corners_and_straight_edges() -> None:
    panel_lines = _visual_lines(render_nexo_panel())

    assert panel_lines[0].startswith("╭") and panel_lines[0].endswith("╮")
    assert panel_lines[-1].startswith("╰") and panel_lines[-1].endswith("╯")
    for line in panel_lines[1:-1]:
        assert line.startswith("│") and line.endswith("│")


def test_render_nexo_panel_title_includes_the_live_version() -> None:
    panel_lines = _visual_lines(render_nexo_panel())

    assert f"Nexolith v{__version__}" in panel_lines[0]


def test_render_nexo_panel_border_uses_the_body_color() -> None:
    rendered = render_nexo_panel()
    red, green, blue = _PALETTE["B"]

    assert f"\x1b[38;2;{red};{green};{blue}m╭" in rendered
    assert f"\x1b[38;2;{red};{green};{blue}m╰" in rendered


def test_render_nexo_panel_title_uses_bold_white() -> None:
    rendered = render_nexo_panel()
    red, green, blue = _PALETTE["W"]

    assert f"\x1b[1m\x1b[38;2;{red};{green};{blue}m Nexolith" in rendered


def test_render_nexo_panel_is_deterministic() -> None:
    assert render_nexo_panel() == render_nexo_panel()
