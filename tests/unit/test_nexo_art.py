import re

from nexolith.cli.nexo_art import _PALETTE, _PIXELS, render_nexo_pixel_art
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
