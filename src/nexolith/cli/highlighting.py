"""Presentation-only syntax highlighting for the full-screen session, via
`prompt_toolkit`'s own `Lexer` mechanism -- not embedded ANSI escape codes.

Why a lexer, not `colorize()` + ANSI text: NXL-100/NXL-104 established that
the scrollable output log's `TextArea`/`Buffer` cannot interpret raw ANSI
escape codes embedded in its text content at all -- confirmed directly, a
literal `^[[38;2;...` sequence is what appears instead of color. A `Lexer`
sidesteps that entirely: it never touches the buffer's actual text (still
exactly what `panel_lines()`/every other renderer wrote, `ansi_capable`-safe
plain text and all -- still searchable, copyable, unchanged), it only tells
`BufferControl` which *style* to paint over which span, purely at render
time. That style is a real `prompt_toolkit` style string (e.g.
`"fg:#5865f2"`), the exact mechanism `status_area.py`'s own `_fg()` helper
already uses for the header/status area -- not a call to `colorize()`,
which produces raw ANSI text meant to go *into* a string, not into a style
slot. `prompt_toolkit`'s own rendering pipeline downgrades that style string
to whatever `ColorDepth` the real terminal supports on its own (confirmed
directly in the NXL-100 investigation) -- so, unlike text-embedded ANSI,
this genuinely never needs `nexo_art`'s manual truecolor/256-color tiering
at all.

Confirmed directly (not assumed) that this actually re-highlights
dynamically, not just once at construction: `BufferControl` caches
`lexer.lex_document(document)` by `(document.text, lexer.invalidation_hash())`
(see `prompt_toolkit.layout.controls.BufferControl._get_formatted_text_for_line_func`)
-- a changed document (e.g. `append_output()`'s `buffer.set_document(...)`)
is a cache miss, so the lexer runs fresh against the new content every time.

Only structural/label coloring, not full semantic recovery: by the time a
renderer's plain text reaches `OutputLogLexer`, the original per-value
semantic color (e.g. GREEN for "succeeded", RED for "failed") is gone --
plain text carries no such signal, and reconstructing it here would mean
re-implementing every renderer's own status/severity logic a second time.
This lexer paints only the blue Discord-like structural elements the NXL-104
follow-up and NXL-106 actually ask for: panel border characters and
`Label:` prefixes (NXL-104), and recognized `/command` keywords as they're
typed (NXL-106) -- not values.
"""

import re
from collections.abc import Callable

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer

from nexolith.cli.completion import COMMANDS
from nexolith.cli.nexo_art import BLURPLE
from nexolith.cli.render_context import RenderContext


def _style(rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    style = f"fg:#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return f"{style} bold" if bold else style


_BLUE = _style(BLURPLE)
_BLUE_BOLD = _style(BLURPLE, bold=True)

# The rounded-panel border characters `panel_lines()` (nexo_art.py) draws --
# a real full-screen session's `render_context.plain` is always False (full
# screen mode never even starts otherwise, see `run_interactive_session()`),
# so the ASCII `+-|` fallback border is not reachable here in practice; not
# specifically handled, matching that reality rather than adding dead code
# for a combination that cannot occur.
_BORDER_CHARS = frozenset("╭╮╰╯─│")

# A `Label: ` or bare `Label:` prefix, optionally preceded by a `│ ` panel
# border -- matches every renderer's own established convention
# ("Status:", "DAG:", "Rows read:", "Scheduler:", "DAG opened:", "Tasks:",
# "Partial:", ...) without hardcoding that specific list here.
_LABEL_RE = re.compile(r"^(│\s*)?([A-Z][A-Za-z ]*:)(\s*)")

_COMMAND_RE = re.compile(r"^(/\S*)")


def _highlight_panel_line(line: str) -> StyleAndTextTuples:
    stripped = line.strip()
    if stripped and set(stripped) <= _BORDER_CHARS:
        return [(_BLUE, line)]

    match = _LABEL_RE.match(line)
    if match is None:
        return [("", line)]

    fragments: StyleAndTextTuples = []
    border_prefix, label, spacing = match.group(1), match.group(2), match.group(3)
    if border_prefix:
        fragments.append((_BLUE, border_prefix))
    fragments.append((_BLUE_BOLD, label))
    if spacing:
        fragments.append(("", spacing))

    remainder = line[match.end() :]
    if remainder.endswith("│"):
        if remainder[:-1]:
            fragments.append(("", remainder[:-1]))
        fragments.append((_BLUE, "│"))
    elif remainder:
        fragments.append(("", remainder))
    return fragments


def _highlight_command_keyword(line: str) -> StyleAndTextTuples:
    match = _COMMAND_RE.match(line)
    if match is None:
        return [("", line)]
    keyword = match.group(1)
    if keyword not in COMMANDS:
        return [("", line)]
    remainder = line[len(keyword) :]
    fragments: StyleAndTextTuples = [(_BLUE_BOLD, keyword)]
    if remainder:
        fragments.append(("", remainder))
    return fragments


class _PlainModeAwareLexer(Lexer):
    """Shared plumbing for both lexers below: disabled entirely (every line
    returned completely unstyled) when `render_context.plain` -- consistent
    with every other plain-mode fallback in this project (NXL-80 and every
    story since).
    """

    def __init__(
        self, render_context: RenderContext, highlight: Callable[[str], StyleAndTextTuples]
    ):
        self._enabled = not render_context.plain
        self._highlight = highlight

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        lines = document.lines
        enabled = self._enabled
        highlight = self._highlight

        def get_line(lineno: int) -> StyleAndTextTuples:
            try:
                line = lines[lineno]
            except IndexError:
                return []
            if not enabled:
                return [("", line)]
            return highlight(line)

        return get_line


class OutputLogLexer(_PlainModeAwareLexer):
    """Blue structural highlighting for the full-screen session's scrollable
    output log (NXL-104 follow-up): panel border characters and `Label:`
    prefixes. See module docstring for the full mechanism and its
    deliberate scope (structural only, not per-value semantic color).
    """

    def __init__(self, render_context: RenderContext) -> None:
        super().__init__(render_context, _highlight_panel_line)


class CommandKeywordLexer(_PlainModeAwareLexer):
    """Blue highlighting for a recognized `/command` keyword as it's typed
    in the full-screen session's input field (NXL-106), before submission.
    Only the leading keyword is ever colored -- an argument (a path, a run
    id, a scheduler subcommand) is never mistaken for a keyword, since
    `_COMMAND_RE` only ever matches at the very start of the line.
    """

    def __init__(self, render_context: RenderContext) -> None:
        super().__init__(render_context, _highlight_command_keyword)
