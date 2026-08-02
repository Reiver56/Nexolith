"""Full-screen (alternate-buffer) interactive session, via `prompt_toolkit`.

Every command still dispatches through `InteractiveSession.dispatch()` — the
exact same logic the classic line-based loop uses — so behavior is identical
between the two; only presentation differs. `/validate` and `/run` progress
and outcomes go through a `FullScreenOperationPresenter` (status_area.py)
instead of the classic loop's plain-text `ClassicOperationPresenter` — same
dispatch, same `PipelineApplication` calls, different presentation only.

Callers must check `RenderContext.plain` before calling `run_full_screen_session`
(see `nexolith.cli.interactive.run_interactive_session`) — this module always
attempts a real full-screen session and never checks terminal capability
itself.

Crash/exit safety: `prompt_toolkit.Application(full_screen=True).run()`
restores the terminal (raw mode and the alternate screen buffer) in its own
`finally` blocks regardless of how it exits — normal completion, `.exit()`,
or an unhandled exception. That guarantee comes from prompt_toolkit itself,
not from anything in this module.
"""

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.widgets import TextArea

from nexolith.cli.completion import NexolithCompleter
from nexolith.cli.interactive import GOODBYE, InteractiveSession, parse_command, render_prompt
from nexolith.cli.nexo_art import BLURPLE, render_nexo_panel
from nexolith.cli.render_context import RenderContext
from nexolith.cli.status_area import FullScreenOperationPresenter, StatusAreaState

_PANEL_HEIGHT = 9  # render_nexo_panel()'s fixed footprint: title + 7 art lines + bottom border
_DIVIDER_COLOR = f"fg:#{BLURPLE[0]:02x}{BLURPLE[1]:02x}{BLURPLE[2]:02x}"


def _divider() -> Window:
    return Window(char="─", height=1, style=_DIVIDER_COLOR)


def run_full_screen_session(
    render_context: RenderContext,
    *,
    session: InteractiveSession | None = None,
    status_state: StatusAreaState | None = None,
) -> None:
    """Run an `InteractiveSession` inside a full-screen prompt_toolkit layout:
    the bordered Nexo panel as a static header, a status area for
    `/validate`/`/run` progress and outcomes, a scrollable output log, and a
    completing input line — separated by blue Discord-toned divider lines.

    `session` and `status_state` are injectable for tests (e.g. with a fake
    `PipelineApplication`, and to inspect the status area's final state after
    a real headless run — the same pattern already used for `session`);
    default to a real session using `render_context` and a fresh state.
    """
    active_session = session or InteractiveSession(render_context=render_context)

    output_area = TextArea(read_only=True, scrollbar=True, wrap_lines=True)

    def append_output(text: str) -> None:
        current = output_area.buffer.document.text
        new_text = f"{current}\n{text}" if current else text
        output_area.buffer.set_document(
            Document(new_text, cursor_position=len(new_text)), bypass_readonly=True
        )

    active_session.set_output_writer(append_output)

    status_state = status_state or StatusAreaState()
    status_window = Window(
        content=FormattedTextControl(status_state.render),
        height=Dimension(min=0, max=8),
        wrap_lines=True,
        dont_extend_height=True,
    )
    active_session.set_presenter(
        FullScreenOperationPresenter(status_state, invalidate=lambda: application.invalidate())
    )

    input_field = TextArea(
        height=1,
        multiline=False,
        completer=NexolithCompleter(),
        complete_while_typing=True,
        history=InMemoryHistory(),
        prompt=lambda: render_prompt(active_session.context),
    )

    def on_submit(buffer: Buffer) -> bool:
        text = buffer.text
        append_output(f"{render_prompt(active_session.context)}{text}")
        if not active_session.dispatch(parse_command(text)):
            application.exit()
        return False  # clear the input line after submit

    input_field.accept_handler = on_submit

    header = Window(
        content=FormattedTextControl(ANSI(render_nexo_panel())),
        height=_PANEL_HEIGHT,
        dont_extend_height=True,
    )

    body = HSplit(
        [
            header,
            _divider(),
            status_window,
            output_area,
            _divider(),
            input_field,
        ]
    )
    root = FloatContainer(
        content=body,
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=8, scroll_offset=1),
            )
        ],
    )
    layout = Layout(root, focused_element=input_field)

    bindings = KeyBindings()

    @bindings.add("c-c")
    @bindings.add("c-d")
    def _exit(event: object) -> None:
        append_output(GOODBYE)
        application.exit()

    application: Application[None] = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=True,
        mouse_support=False,
    )

    # The panel header already carries the "Nexo/Nexolith" branding that the
    # classic splash's first line repeats in text; only the second line (the
    # actual usage hint) still earns its place in the scrollable log.
    append_output("Type /help for available commands.")
    application.run()
