import re

from nexolith import __version__
from nexolith.cli.nexo_art import (
    _PALETTE,
    _PANEL_INTERIOR_WIDTH,
    _PIXELS,
    _ansi_256_index,
    render_nexo_panel,
    render_nexo_pixel_art,
)
from nexolith.cli.render_context import MINIMUM_WIDTH, RenderContext

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# NXL-100: every render_nexo_*() call now needs a RenderContext, since
# whether it emits real 24-bit truecolor or a nearest-256-color
# approximation depends on RenderContext.truecolor -- there's no longer a
# single, context-free "the" rendering. These two fixtures pin the two
# real tiers a non-plain render can be in.
_TRUECOLOR = RenderContext(is_tty=True, color_enabled=True, width=200, truecolor=True)
_STANDARD = RenderContext(is_tty=True, color_enabled=True, width=200, truecolor=False)


def _visual_lines(rendered: str) -> list[str]:
    return [_ANSI_RE.sub("", line) for line in rendered.split("\n")]


def test_pixel_grid_has_an_even_row_count_for_half_block_pairing() -> None:
    assert len(_PIXELS) % 2 == 0


def test_render_nexo_pixel_art_halves_the_grid_into_terminal_lines() -> None:
    rendered = render_nexo_pixel_art(_TRUECOLOR)

    assert rendered.count("\n") == len(_PIXELS) // 2 - 1


def test_render_nexo_pixel_art_stays_a_compact_header() -> None:
    """Pin the compact-header sizing: well under MINIMUM_WIDTH, single-digit lines."""
    rendered = render_nexo_pixel_art(_TRUECOLOR)
    lines = rendered.split("\n")

    assert len(lines) < 10
    for line in _visual_lines(rendered):
        assert len(line) == len(_PIXELS[0])
    assert len(_PIXELS[0]) < MINIMUM_WIDTH


def test_render_nexo_pixel_art_uses_ansi_truecolor_and_resets() -> None:
    rendered = render_nexo_pixel_art(_TRUECOLOR)

    assert "\x1b[38;2;" in rendered
    assert "\x1b[0m" in rendered


def test_render_nexo_pixel_art_uses_only_the_declared_palette() -> None:
    rendered = render_nexo_pixel_art(_TRUECOLOR)

    for red, green, blue in _PALETTE.values():
        assert f"\x1b[38;2;{red};{green};{blue}m" in rendered


def test_render_nexo_pixel_art_body_color_is_discord_blurple() -> None:
    assert _PALETTE["B"] == (0x58, 0x65, 0xF2)


def test_render_nexo_pixel_art_is_deterministic() -> None:
    assert render_nexo_pixel_art(_TRUECOLOR) == render_nexo_pixel_art(_TRUECOLOR)


def test_render_nexo_panel_wraps_the_art_with_a_fixed_margin() -> None:
    """Pin the panel footprint: art rows plus exactly one title row and one
    bottom-border row, each line the same total width (art + 1-space padding
    each side + 1 border character each side)."""
    art_lines = render_nexo_pixel_art(_TRUECOLOR).split("\n")
    panel_lines = _visual_lines(render_nexo_panel(_TRUECOLOR))

    assert len(panel_lines) == len(art_lines) + 2
    expected_width = len(_PIXELS[0]) + 4  # padding (1+1) + border chars (1+1)
    assert expected_width == _PANEL_INTERIOR_WIDTH + 2
    for line in panel_lines:
        assert len(line) == expected_width


def test_render_nexo_panel_stays_well_under_the_narrow_terminal_threshold() -> None:
    panel_lines = _visual_lines(render_nexo_panel(_TRUECOLOR))

    assert len(panel_lines[0]) < MINIMUM_WIDTH
    assert len(panel_lines) < 10


def test_render_nexo_panel_has_rounded_corners_and_straight_edges() -> None:
    panel_lines = _visual_lines(render_nexo_panel(_TRUECOLOR))

    assert panel_lines[0].startswith("╭") and panel_lines[0].endswith("╮")
    assert panel_lines[-1].startswith("╰") and panel_lines[-1].endswith("╯")
    for line in panel_lines[1:-1]:
        assert line.startswith("│") and line.endswith("│")


def test_render_nexo_panel_title_includes_the_live_version() -> None:
    panel_lines = _visual_lines(render_nexo_panel(_TRUECOLOR))

    assert f"Nexolith v{__version__}" in panel_lines[0]


def test_render_nexo_panel_border_uses_the_body_color() -> None:
    rendered = render_nexo_panel(_TRUECOLOR)
    red, green, blue = _PALETTE["B"]

    assert f"\x1b[38;2;{red};{green};{blue}m╭" in rendered
    assert f"\x1b[38;2;{red};{green};{blue}m╰" in rendered


def test_render_nexo_panel_title_uses_bold_white() -> None:
    rendered = render_nexo_panel(_TRUECOLOR)
    red, green, blue = _PALETTE["W"]

    assert f"\x1b[1m\x1b[38;2;{red};{green};{blue}m Nexolith" in rendered


def test_render_nexo_panel_is_deterministic() -> None:
    assert render_nexo_panel(_TRUECOLOR) == render_nexo_panel(_TRUECOLOR)


# -- NXL-100: standard (256-color) tier ---------------------------------
#
# Found via a real terminal that printed an unrecognized `38;2;r;g;b`
# sequence back as literal text instead of degrading it itself. When
# RenderContext.truecolor is False, every renderer here must emit
# `38;5;N`/`48;5;N` (256-color) instead -- never fall silently back to
# truecolor just because no explicit request for 256-color was made, and
# never disable color entirely either (a correctly-degraded palette beats
# no color at all).


def test_render_nexo_pixel_art_uses_256_color_not_truecolor_in_standard_tier() -> None:
    rendered = render_nexo_pixel_art(_STANDARD)

    assert "\x1b[38;2;" not in rendered
    assert "\x1b[48;2;" not in rendered
    assert "\x1b[38;5;" in rendered
    assert "\x1b[0m" in rendered


def test_render_nexo_pixel_art_standard_tier_uses_the_correct_256_indices() -> None:
    rendered = render_nexo_pixel_art(_STANDARD)

    for rgb in _PALETTE.values():
        assert f"\x1b[38;5;{_ansi_256_index(rgb)}m" in rendered


def test_render_nexo_panel_uses_256_color_not_truecolor_in_standard_tier() -> None:
    rendered = render_nexo_panel(_STANDARD)
    border_index = _ansi_256_index(_PALETTE["B"])
    title_index = _ansi_256_index(_PALETTE["W"])

    assert "\x1b[38;2;" not in rendered
    assert f"\x1b[38;5;{border_index}m╭" in rendered
    assert f"\x1b[38;5;{border_index}m╰" in rendered
    assert f"\x1b[1m\x1b[38;5;{title_index}m Nexolith" in rendered


def test_render_nexo_panel_standard_and_truecolor_tiers_produce_different_output() -> None:
    """The whole point: the two tiers must genuinely differ, not just both
    happen to "work" -- confirms the tier parameter actually changes what's
    emitted, not just accepted."""
    assert render_nexo_panel(_TRUECOLOR) != render_nexo_panel(_STANDARD)
    # But the *visible* (ANSI-stripped) content is identical -- only the
    # color encoding differs, never the actual art/text.
    assert _visual_lines(render_nexo_panel(_TRUECOLOR)) == _visual_lines(
        render_nexo_panel(_STANDARD)
    )


def test_ansi_256_index_maps_known_palette_colors_to_expected_indices() -> None:
    """Pin the actual nearest-256-color mapping for this palette against
    hand-verified values (RED independently cross-checked against
    prompt_toolkit's own truecolor-to-256-color downgrade of the exact
    same RGB triple, which also produces 203)."""
    assert _ansi_256_index((0x58, 0x65, 0xF2)) == 63  # BLURPLE
    assert _ansi_256_index((0xED, 0x42, 0x45)) == 203  # RED
    assert _ansi_256_index((0xFF, 0xFF, 0xFF)) == 231  # WHITE (cube corner)
    assert _ansi_256_index((0x00, 0x00, 0x00)) == 16  # true black (cube corner)
