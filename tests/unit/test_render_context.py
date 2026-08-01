from nexolith.cli.render_context import MINIMUM_WIDTH, RenderContext, detect_render_context


class FakeStream:
    def __init__(self, *, is_a_tty: bool) -> None:
        self._is_a_tty = is_a_tty

    def isatty(self) -> bool:
        return self._is_a_tty


def wide_environ(**overrides: str) -> dict[str, str]:
    environ = {"COLUMNS": str(MINIMUM_WIDTH)}
    environ.update(overrides)
    return environ


def test_full_capability_terminal_is_not_plain() -> None:
    context = detect_render_context(stream=FakeStream(is_a_tty=True), environ=wide_environ())

    assert context.is_tty is True
    assert context.color_enabled is True
    assert context.width == MINIMUM_WIDTH
    assert context.forced_plain is False
    assert context.plain is False


def test_no_color_env_var_disables_color_and_forces_plain() -> None:
    context = detect_render_context(
        stream=FakeStream(is_a_tty=True), environ=wide_environ(NO_COLOR="1")
    )

    assert context.color_enabled is False
    assert context.plain is True


def test_no_color_present_with_empty_value_still_disables_color() -> None:
    """Per the NO_COLOR spec, presence alone disables color, regardless of value."""
    context = detect_render_context(
        stream=FakeStream(is_a_tty=True), environ=wide_environ(NO_COLOR="")
    )

    assert context.color_enabled is False
    assert context.plain is True


def test_non_tty_output_forces_plain() -> None:
    context = detect_render_context(stream=FakeStream(is_a_tty=False), environ=wide_environ())

    assert context.is_tty is False
    assert context.plain is True


def test_narrow_terminal_width_forces_plain() -> None:
    context = detect_render_context(
        stream=FakeStream(is_a_tty=True), environ=wide_environ(COLUMNS=str(MINIMUM_WIDTH - 1))
    )

    assert context.width == MINIMUM_WIDTH - 1
    assert context.plain is True


def test_width_exactly_at_minimum_is_not_plain() -> None:
    context = detect_render_context(
        stream=FakeStream(is_a_tty=True), environ=wide_environ(COLUMNS=str(MINIMUM_WIDTH))
    )

    assert context.plain is False


def test_forced_plain_overrides_a_fully_capable_terminal() -> None:
    context = detect_render_context(
        stream=FakeStream(is_a_tty=True), environ=wide_environ(), forced_plain=True
    )

    assert context.is_tty is True
    assert context.color_enabled is True
    assert context.plain is True


def test_invalid_columns_value_falls_back_to_real_terminal_size() -> None:
    context = detect_render_context(
        stream=FakeStream(is_a_tty=True), environ={"COLUMNS": "not-a-number"}
    )

    assert isinstance(context.width, int)
    assert context.width > 0


def test_stream_without_isatty_is_treated_as_non_tty() -> None:
    context = detect_render_context(stream=object(), environ=wide_environ())  # type: ignore[arg-type]

    assert context.is_tty is False
    assert context.plain is True


def test_render_context_is_a_frozen_value_object() -> None:
    context = RenderContext(is_tty=True, color_enabled=True, width=MINIMUM_WIDTH)

    assert context.forced_plain is False
    assert context.plain is False
