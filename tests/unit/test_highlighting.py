from __future__ import annotations

from prompt_toolkit.document import Document

from nexolith.cli.highlighting import CommandKeywordLexer, OutputLogLexer
from nexolith.cli.nexo_art import BLURPLE
from nexolith.cli.render_context import RenderContext

_CAPABLE = RenderContext(is_tty=True, color_enabled=True, width=200)
_PLAIN = RenderContext(is_tty=False, color_enabled=True, width=200)
_BLUE = f"fg:#{BLURPLE[0]:02x}{BLURPLE[1]:02x}{BLURPLE[2]:02x}"
_BLUE_BOLD = f"{_BLUE} bold"


def lex_lines(lexer_output_text: str, render_context: RenderContext) -> list[list[tuple[str, str]]]:
    lexer = OutputLogLexer(render_context)
    document = Document(lexer_output_text)
    get_line = lexer.lex_document(document)
    return [get_line(i) for i in range(len(document.lines))]


# --- OutputLogLexer -------------------------------------------------------


def test_top_border_line_is_entirely_blue() -> None:
    [fragments] = lex_lines("╭───────╮", _CAPABLE)

    assert fragments == [(_BLUE, "╭───────╮")]


def test_bottom_border_line_is_entirely_blue() -> None:
    [fragments] = lex_lines("╰───────╯", _CAPABLE)

    assert fragments == [(_BLUE, "╰───────╯")]


def test_panel_content_line_colors_border_and_label_not_the_value() -> None:
    [fragments] = lex_lines("│ Status: succeeded │", _CAPABLE)

    assert fragments == [
        (_BLUE, "│ "),
        (_BLUE_BOLD, "Status:"),
        ("", " "),
        ("", "succeeded "),
        (_BLUE, "│"),
    ]


def test_multi_word_label_is_recognized() -> None:
    [fragments] = lex_lines("│ Rows written: 3 │", _CAPABLE)

    assert (_BLUE_BOLD, "Rows written:") in fragments


def test_standalone_label_line_without_panel_border_is_recognized() -> None:
    [fragments] = lex_lines("DAG opened: /tmp/dag.yaml", _CAPABLE)

    assert fragments[0] == (_BLUE_BOLD, "DAG opened:")


def test_bare_label_with_no_trailing_value_still_matches() -> None:
    [fragments] = lex_lines("Tasks:", _CAPABLE)

    assert fragments == [(_BLUE_BOLD, "Tasks:")]


def test_line_with_no_recognized_pattern_is_completely_unstyled() -> None:
    [fragments] = lex_lines("Goodbye.", _CAPABLE)

    assert fragments == [("", "Goodbye.")]


def test_task_row_marker_line_is_left_unstyled_not_guessed_at() -> None:
    """Scope check: a task row (e.g. `  ● only  succeeded  0.001s`) carries
    real per-task semantic color in its original renderer, which this lexer
    deliberately never tries to reconstruct from plain text alone (see
    module docstring) -- it simply doesn't match either pattern here."""
    [fragments] = lex_lines("  ● only     succeeded  0.001s", _CAPABLE)

    assert fragments == [("", "  ● only     succeeded  0.001s")]


def test_plain_mode_disables_all_output_log_styling() -> None:
    text = "╭───────╮\n│ Status: succeeded │\n╰───────╯"

    for fragments in lex_lines(text, _PLAIN):
        assert all(style == "" for style, _text in fragments)


def test_out_of_range_line_number_returns_empty() -> None:
    lexer = OutputLogLexer(_CAPABLE)
    get_line = lexer.lex_document(Document("one line"))

    assert get_line(5) == []


def test_relexes_fresh_after_the_document_changes() -> None:
    """Confirms the lexer itself has no stale per-document cache of its
    own -- `BufferControl`'s own caching (keyed by `document.text`, see
    `highlighting.py`'s module docstring) is what actually drives
    re-highlighting in the real widget; this only confirms this lexer's
    `lex_document()` is a pure function of whatever `Document` it's given,
    not stateful across calls.
    """
    lexer = OutputLogLexer(_CAPABLE)

    first = lexer.lex_document(Document("╭───╮"))(0)
    second = lexer.lex_document(Document("plain text"))(0)

    assert first == [(_BLUE, "╭───╮")]
    assert second == [("", "plain text")]


# --- CommandKeywordLexer ---------------------------------------------------


def lex_input_line(text: str, render_context: RenderContext) -> list[tuple[str, str]]:
    lexer = CommandKeywordLexer(render_context)
    return lexer.lex_document(Document(text))(0)


def test_recognized_command_alone_is_entirely_blue_bold() -> None:
    assert lex_input_line("/run", _CAPABLE) == [(_BLUE_BOLD, "/run")]


def test_recognized_command_with_argument_only_colors_the_keyword() -> None:
    fragments = lex_input_line("/open some/path.yaml", _CAPABLE)

    assert fragments == [(_BLUE_BOLD, "/open"), ("", " some/path.yaml")]


def test_register_is_recognized_after_nxl_103_completer_fix() -> None:
    assert lex_input_line("/register", _CAPABLE) == [(_BLUE_BOLD, "/register")]


def test_close_and_clear_are_both_recognized_after_nxl_105_rename() -> None:
    """NXL-105: `/close` (new) and `/clear` (redefined meaning, same
    keyword) are both still real, highlighted commands -- automatic via
    the shared `completion.COMMANDS` list, not a separate list here."""
    assert lex_input_line("/close", _CAPABLE) == [(_BLUE_BOLD, "/close")]
    assert lex_input_line("/clear", _CAPABLE) == [(_BLUE_BOLD, "/clear")]


def test_unrecognized_slash_command_is_left_unstyled() -> None:
    assert lex_input_line("/bogus", _CAPABLE) == [("", "/bogus")]


def test_non_command_text_is_left_unstyled() -> None:
    assert lex_input_line("just typing text", _CAPABLE) == [("", "just typing text")]


def test_empty_input_line_returns_empty() -> None:
    assert lex_input_line("", _CAPABLE) == [("", "")]


def test_plain_mode_disables_command_keyword_highlighting() -> None:
    assert lex_input_line("/run", _PLAIN) == [("", "/run")]
