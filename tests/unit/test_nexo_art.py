from nexolith.cli.nexo_art import _PALETTE, _PIXELS, render_nexo_pixel_art


def test_render_nexo_pixel_art_matches_pixel_grid_row_count() -> None:
    rendered = render_nexo_pixel_art()

    assert rendered.count("\n") == len(_PIXELS) - 1


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
