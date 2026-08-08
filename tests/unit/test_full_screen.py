from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.output import DummyOutput

from nexolith.cli.completion import NexolithCompleter
from nexolith.cli.full_screen import _DIVIDER_COLOR, _divider, run_full_screen_session
from nexolith.cli.interactive import InteractiveSession
from nexolith.cli.nexo_art import BLURPLE
from nexolith.cli.render_context import RenderContext, detect_width
from nexolith.cli.status_area import StatusAreaState
from nexolith.scheduler import default_pidfile_path, is_process_alive, write_pidfile

_CAPABLE = RenderContext(is_tty=True, color_enabled=True, width=200)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this file gets its own state directory -- never the
    real user's %LOCALAPPDATA%\\Nexolith. Needed for this story's /runs and
    /scheduler commands, which open a real StateStore/pidfile at the
    default location unless overridden.
    """
    state_dir = tmp_path / "full_screen_state"
    monkeypatch.setenv("NEXOLITH_STATE_DIR", str(state_dir))
    return state_dir


def run_with_keys(
    keys: str,
    *,
    session: InteractiveSession | None = None,
    status_state: StatusAreaState | None = None,
    render_context: RenderContext = _CAPABLE,
) -> InteractiveSession:
    """Drive a real, headless full-screen Application with synthetic keystrokes
    (prompt_toolkit's own testing utilities: a pipe-backed Input and a
    DummyOutput, no real terminal involved) and return the session used.

    `render_context` must match `session`'s own `render_context` whenever a
    custom `session` is passed with a non-default one (real usage always
    constructs both from the same value -- see `run_interactive_session()`);
    it's a separate parameter here only because `run_full_screen_session()`
    itself takes `render_context` and `session` independently, for the
    header/`FullScreenOperationPresenter` versus the session's own dispatch
    logic respectively.
    """
    active_session = session or InteractiveSession(render_context=render_context)
    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keys)
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(
                render_context, session=active_session, status_state=status_state
            )
    return active_session


def run_with_keys_capturing_output_log(
    keys: str,
    *,
    session: InteractiveSession | None = None,
    status_state: StatusAreaState | None = None,
    render_context: RenderContext = _CAPABLE,
) -> str:
    """Like `run_with_keys`, but also returns the real, final scrollable
    output log text -- what a user would actually see for commands (like
    /help, /runs, /scheduler) that write through the plain output log
    rather than the status area. `output_area` is the only read-only
    Buffer-backed window in the layout (the input field isn't read-only;
    the header/status controls have no Buffer at all), so it's identified
    that way rather than needing `run_full_screen_session` to expose it.
    """
    active_session = session or InteractiveSession(render_context=render_context)
    captured: dict[str, str] = {}
    original_dispatch = active_session.dispatch

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        for window in get_app().layout.find_all_windows():
            buffer = getattr(window.content, "buffer", None)
            if buffer is not None and bool(buffer.read_only()):
                captured["text"] = buffer.document.text
        return result

    active_session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keys)
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(
                render_context, session=active_session, status_state=status_state
            )
    return captured.get("text", "")


def write_pipeline(path: Path, source: Path, destination: Path) -> None:
    source.write_text("id,status\n1,ready\n2,done\n", encoding="utf-8")
    path.write_text(
        f"""
name: full-screen-test
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {destination.as_posix()}
""",
        encoding="utf-8",
    )


def test_slash_exit_ends_the_session_cleanly() -> None:
    session = run_with_keys("/exit\n")

    assert session.context.pipeline is None


def test_ctrl_c_ends_the_session_cleanly() -> None:
    session = run_with_keys("\x03")

    assert session.context.pipeline is None


def test_ctrl_d_eof_ends_the_session_cleanly() -> None:
    session = run_with_keys("\x04")

    assert session.context.pipeline is None


def test_open_validate_run_dispatch_identically_to_the_classic_loop(tmp_path: Path) -> None:
    """Same dispatch path as the classic loop (InteractiveSession.dispatch) --
    proves the full-screen wiring doesn't reimplement or diverge from it."""
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)

    session = run_with_keys(f"/open {pipeline}\n/validate\n/run\n/exit\n")

    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == pipeline.resolve()
    assert destination.is_file()
    assert destination.read_text(encoding="utf-8").strip().splitlines() == [
        "id,status",
        "1,ready",
        "2,done",
    ]


def test_unknown_command_keeps_the_session_open_until_explicit_exit() -> None:
    session = run_with_keys("/bogus\n/exit\n")

    assert session.context.pipeline is None


def test_session_stays_usable_after_a_failed_open(tmp_path: Path) -> None:
    """Matches the classic loop's shell-reusability-after-errors guarantee
    (NXL-38): a failed /open must not leave the full-screen session stuck --
    a later, valid /open in the same session must still succeed."""
    missing = tmp_path / "missing.yaml"
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)

    session = run_with_keys(f"/open {missing}\n/open {pipeline}\n/exit\n")

    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == pipeline.resolve()


def test_open_discovery_works_end_to_end_through_the_real_full_screen_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NXL-107: /open (no argument, nothing open) discovers and lists DAG
    files, and /open <number> opens one -- through the real full-screen
    machinery (dispatch is shared with the classic loop, already covered
    there, but this confirms the shared code path genuinely works here
    too, not just that it should in theory)."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    dag_path = workflows_dir / "dag.yaml"
    pipeline_path = workflows_dir / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)
    monkeypatch.chdir(tmp_path)
    session = InteractiveSession(render_context=_CAPABLE)

    output_log = run_with_keys_capturing_output_log("/open\n/open 1\n/exit\n", session=session)

    assert "Discovered DAGs:" in output_log
    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == dag_path.resolve()
    assert "DAG opened:" in output_log


def test_clear_empties_the_full_screen_output_log(tmp_path: Path) -> None:
    """NXL-105: `/clear` inside the full-screen session now clears the
    scrollable output log itself -- a real headless run inspecting the
    log's actual buffer content before and after, not just that dispatch
    doesn't crash.
    """
    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch
    snapshots: list[str] = []

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        for window in get_app().layout.find_all_windows():
            buffer = getattr(window.content, "buffer", None)
            if buffer is not None and bool(buffer.read_only()):
                snapshots.append(buffer.document.text)
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text("/help\n/clear\n/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session)

    assert snapshots[0] != ""  # after /help: real content in the log
    assert snapshots[1] == ""  # after /clear: emptied


def test_close_still_clears_the_pipeline_context_in_full_screen(tmp_path: Path) -> None:
    """NXL-105 regression: `/close` (renamed from `/clear`) still clears the
    open pipeline/DAG context exactly as the old `/clear` did, end to end
    through the real full-screen machinery, not just the classic loop
    (already covered in test_interactive_cli.py)."""
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)
    session = InteractiveSession(render_context=_CAPABLE)

    output_log = run_with_keys_capturing_output_log(
        f"/open {pipeline}\n/close\n/exit\n", session=session
    )

    assert session.context.pipeline is None
    assert "Pipeline context cleared." in output_log


def test_divider_uses_the_blue_discord_palette() -> None:
    divider = _divider()

    assert divider.char == "─"
    assert divider.style == _DIVIDER_COLOR
    assert f"fg:#{BLURPLE[0]:02x}{BLURPLE[1]:02x}{BLURPLE[2]:02x}" == _DIVIDER_COLOR


def test_a_divider_separates_the_status_area_from_the_output_log() -> None:
    """The CHANGELOG promises divider lines between the header, status,
    output, and input regions (NXL-69). That's four regions and three
    boundaries -- header|status, status|output, and output|input -- but the
    layout previously only drew two, leaving the status area (a step
    timeline or summary panel that changes height and content between
    `/run` and `/validate`) directly adjacent to the scrollable output log
    with no visual boundary. Confirmed via a real headless run inspecting
    the live layout, not just `_divider()` in isolation.
    """
    divider_count: dict[str, int] = {}

    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        windows = get_app().layout.find_all_windows()
        divider_count["n"] = sum(1 for w in windows if w.style == _DIVIDER_COLOR)
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text("/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session)

    assert divider_count.get("n") == 3


def test_run_updates_the_status_area_timeline_then_returns_to_idle(tmp_path: Path) -> None:
    """Real events, real dispatch, real status area -- not just a unit-level
    check of StatusAreaState in isolation. NXL-104: a `/run`'s own outcome no
    longer stays shown in this fixed area (the step timeline updates live
    while it runs -- confirmed here via the steps ending up "done" -- but the
    area itself returns to blank/idle once the result panel has been written
    to the output log instead; see test_run_result_panel_appears_in_the_output_log_...
    below for that part).
    """
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)
    status = StatusAreaState()

    run_with_keys(f"/open {pipeline}\n/run\n/exit\n", status_state=status)

    assert status.visible is False
    assert all(step_status.value == "done" for _, step_status in status.steps)


def test_run_result_panel_appears_in_the_output_log_with_the_rounded_style(
    tmp_path: Path,
) -> None:
    """NXL-104: a classic pipeline's `/run` outcome now goes to the
    scrollable output log, using the same rounded-border panel style as a
    DAG's own `/run` outcome (see
    test_dag_run_result_uses_the_same_rounded_panel_style_as_a_classic_pipeline
    below) -- not a separate `StyleAndTextTuples` panel in the fixed area.
    """
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)
    status = StatusAreaState()

    output_log = run_with_keys_capturing_output_log(
        f"/open {pipeline}\n/run\n/exit\n", status_state=status
    )

    assert "╭" in output_log and "╮" in output_log
    assert "╰" in output_log and "╯" in output_log
    assert "Status: succeeded" in output_log
    assert "Rows read: 2" in output_log
    assert "Rows written: 2" in output_log
    assert status.visible is False


def test_validate_updates_the_status_area_to_a_terminal_validation_panel(tmp_path: Path) -> None:
    """Mirrors test_run_updates_the_status_area_timeline_and_summary_end_to_end:
    `/validate` produces a narrower event sequence than `/run` (no extraction,
    transformation, or write events), and previously the status area had no
    call at all on a successful validate -- it stayed on the in-progress
    timeline forever. Confirm `/validate` now reaches a real terminal visual
    state of its own (a validation panel, not `/run`'s summary panel, since
    `validate_pipeline()` never produces row counts or a duration)."""
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)
    status = StatusAreaState()

    run_with_keys(f"/open {pipeline}\n/validate\n/exit\n", status_state=status)

    assert status.visible is True
    assert status.validated_config is not None
    assert status.validated_config.name == "full-screen-test"
    assert status.error_text is None


def test_validate_error_populates_the_status_area_with_an_excerpt(tmp_path: Path) -> None:
    """Open a valid pipeline (so it's already selected), corrupt it on disk,
    then /validate -- matching NXL-37's own reload-on-change convention. The
    reload's ConfigurationError should reach the status area's excerpt path
    (operation=VALIDATE), not just the plain error text.

    The corruption is hooked into dispatch() itself (not just queued as
    keystrokes) because prompt_toolkit's pipe input has no built-in way to
    pause between commands -- the same problem the classic loop's
    ScriptedInput callable steps solve for the blocking-`input()` loop.
    """
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)
    status = StatusAreaState()

    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch

    def corrupting_dispatch(command: object) -> bool:
        if getattr(command, "kind", None) is not None and command.kind.value == "validate":  # type: ignore[attr-defined]
            pipeline.write_text("name: x\nsource:\n\ttype: csv\n", encoding="utf-8")
        return original_dispatch(command)  # type: ignore[arg-type]

    session.dispatch = corrupting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(f"/open {pipeline}\n/validate\n/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session, status_state=status)

    assert status.error_text is not None
    assert "Error [configuration]" in status.error_text
    assert status.error_excerpt is not None
    assert any(is_target for is_target, _, _ in status.error_excerpt)
    assert any("type: csv" in line for _, _, line in status.error_excerpt)


def test_run_spawns_no_background_threads(tmp_path: Path) -> None:
    """The dot/timeline/summary/excerpt are all event-driven; confirm the
    real session -- not just StatusAreaState in isolation -- introduces no
    thread of its own. prompt_toolkit's own asyncio event loop runs in the
    current thread while the Application is active; it is not an extra
    thread alongside a separate main one."""
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)

    before = {t.ident for t in threading.enumerate()}
    run_with_keys(f"/open {pipeline}\n/validate\n/run\n/exit\n")
    after = {t.ident for t in threading.enumerate()}

    assert after == before


def test_mouse_support_is_enabled() -> None:
    """Without `mouse_support=True`, the terminal is never asked to report
    real scroll-wheel/trackpad events at all, so it falls back (a common
    alt-screen-buffer convention, for compatibility with programs that don't
    support the mouse) to emulating Up/Down arrow key presses for scroll
    gestures -- which always land on `input_field` (the permanently-focused
    control) and trigger its history navigation instead of scrolling
    `output_area`.

    That terminal-level fallback decision is made by the real terminal
    emulator, outside anything a headless pipe-fed Input can reproduce --
    confirmed directly: injecting a synthetic mouse-scroll escape sequence
    through `create_pipe_input()` routes correctly to `output_area` (see
    `test_scroll_events_route_to_the_output_log_not_input_history` below)
    regardless of this flag's value, because it bypasses the exact layer
    the flag controls. So this test only pins the one thing our own code
    controls: the flag is actually set. The rest requires a real terminal.
    """
    captured: dict[str, bool] = {}

    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        captured["mouse_support"] = bool(get_app().mouse_support())
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text("/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session)

    assert captured.get("mouse_support") is True


def test_min_redraw_interval_is_set_to_throttle_rapid_redraws() -> None:
    """A real, live-reported visual-corruption bug in VS Code's integrated
    terminal (xterm.js) could not be reproduced via prompt_toolkit's own
    internal Screen model in this project's headless tests -- it was
    provably correct at every step of the exact reported sequence -- but
    independently-documented evidence (VS Code's own Terminal-Issues wiki;
    a similar corruption class reported for another Python TUI app;
    an ncurses-specific case VS Code's own maintainers confirmed as
    "upstream") points to a known class of GPU-renderer corruption under
    full-screen TUI redraw stress. `min_redraw_interval` is prompt_toolkit's
    own documented mechanism for exactly this ("some terminals are not able
    to process" high-frequency redraw output) -- this only pins that this
    session actually sets it; it's a real, low-risk mitigation for a
    documented class of issue, not a claim that the specific bug is fixed
    (which could not be confirmed in this environment)."""
    captured: dict[str, float | int | None] = {}

    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        captured["min_redraw_interval"] = get_app().min_redraw_interval
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text("/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session)

    assert captured.get("min_redraw_interval") == 0.05


def test_scroll_events_route_to_the_output_log_not_input_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirms the routing mechanism the mouse_support fix relies on: once
    prompt_toolkit decodes a real mouse scroll event, it dispatches by
    screen position (`Window._mouse_handler`, called via the handler grid
    populated at `renderer.mouse_handlers`), landing on whichever Window the
    coordinates fall inside -- not on whichever control has keyboard focus.
    `BufferControl.mouse_handler` explicitly does not handle scroll events
    itself (falls through to the Window), so this holds for both
    `output_area` and `input_field`.

    This does NOT by itself prove `mouse_support=True` is what fixes Issue
    B: injecting a raw SGR mouse-scroll escape sequence through a headless
    pipe Input bypasses the exact layer that flag controls (whether a real
    terminal is asked to report mouse events at all, versus falling back to
    emulating arrow-key presses for scroll gestures). Confirmed directly --
    this exact test was run unchanged against both `mouse_support=True` and
    `mouse_support=False` and produced identical results either way. That
    terminal-level behavior genuinely cannot be reproduced headlessly; only
    manual verification in a real terminal confirms the fix end to end.
    """
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)

    scroll_calls: list[tuple[str, int]] = []
    history_calls: list[tuple[str, int]] = []
    window_ids_by_ypos: dict[int, tuple[int, int]] = {}

    original_scroll_up = Window._scroll_up
    original_scroll_down = Window._scroll_down
    original_auto_up = Buffer.auto_up
    original_auto_down = Buffer.auto_down

    def patched_scroll_up(self: Window) -> None:
        scroll_calls.append(("up", id(self)))
        original_scroll_up(self)

    def patched_scroll_down(self: Window) -> None:
        scroll_calls.append(("down", id(self)))
        original_scroll_down(self)

    def patched_auto_up(self: Buffer, count: int = 1) -> None:
        history_calls.append(("up", id(self)))
        original_auto_up(self, count=count)

    def patched_auto_down(self: Buffer, count: int = 1) -> None:
        history_calls.append(("down", id(self)))
        original_auto_down(self, count=count)

    monkeypatch.setattr(Window, "_scroll_up", patched_scroll_up)
    monkeypatch.setattr(Window, "_scroll_down", patched_scroll_down)
    monkeypatch.setattr(Buffer, "auto_up", patched_auto_up)
    monkeypatch.setattr(Buffer, "auto_down", patched_auto_down)

    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        app = get_app()
        app._redraw()
        screen = app.renderer._last_screen
        assert screen is not None
        for window, wp in screen.visible_windows_to_write_positions.items():
            buffer = getattr(window.content, "buffer", None)
            if buffer is not None:
                window_ids_by_ypos[wp.ypos] = (id(window), id(buffer))
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    # A real xterm SGR mouse-scroll-up escape sequence (`64` = scroll up, no
    # modifiers) targeting screen row 13 (1-indexed), column 10. Row 13
    # lands inside output_area's rendered rectangle: header (9 rows) +
    # divider (1) + an invisible/1-line status area (1, since `/open` alone
    # never makes it visible) + divider (1) = output_area starts at
    # (0-indexed) row 12, i.e. 1-indexed row 13.
    scroll_up = "\x1b[<64;10;13M"

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(f"/open {pipeline}\n{scroll_up}/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session)

    output_area_window_id, _output_buffer_id = window_ids_by_ypos[12]

    assert scroll_calls == [("up", output_area_window_id)]
    assert history_calls == []


# -- NXL-99: full-screen parity (scheduler, runs, DAG) ----------------------


def write_dag(dag_path: Path, pipeline_path: Path, source: Path, destination: Path) -> None:
    source.write_text("id,status\n1,ready\n2,done\n", encoding="utf-8")
    pipeline_path.write_text(
        f"""
name: full_screen_dag_task
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {destination.as_posix()}
""",
        encoding="utf-8",
    )
    dag_path.write_text(
        f"""
name: full_screen_dag
tasks:
  - name: only
    pipeline: {pipeline_path.name}
    depends_on: []
""",
        encoding="utf-8",
    )


def test_open_validate_run_a_dag_end_to_end_in_full_screen(tmp_path: Path) -> None:
    """DAG support end-to-end through the real full-screen machinery, not
    just the classic loop: /open detects the DAG (detect_document_kind()),
    /validate calls load_dag() directly (no event stream, so no status-area
    timeline for this), /run reuses execute_dag()/render_run_detail() --
    real state recorded, real file written by the DAG's own task.
    """
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)

    output_log = run_with_keys_capturing_output_log(f"/open {dag_path}\n/validate\n/run\n/exit\n")

    plain_output = _ANSI_RE.sub("", output_log)
    assert "DAG opened:" in output_log
    assert "DAG 'full_screen_dag' is valid (1 task)." in output_log
    assert "Status: succeeded" in plain_output  # styled mode colors just the value
    assert "DAG: full_screen_dag" in output_log
    assert "only" in plain_output and "succeeded" in plain_output
    assert destination.is_file()
    assert destination.read_text(encoding="utf-8").strip().splitlines() == [
        "id,status",
        "1,ready",
        "2,done",
    ]
    # NXL-104: a genuinely capable terminal (is_tty, wide, encoding-safe --
    # `_CAPABLE`, used by default here) still gets the rounded border shape
    # in the output log, even though the log itself can't render color.
    assert "╭" in output_log and "╮" in output_log
    assert "+---" not in output_log


def test_dag_run_result_uses_the_same_rounded_panel_style_as_a_classic_pipeline(
    tmp_path: Path,
) -> None:
    """The core NXL-104 fix: before it, a DAG `/run` result rendered as a
    plain ASCII `+---+` rectangle (`_output_render_context()` forced the
    whole render context to `plain`), while a classic pipeline `/run` result
    rendered rounded and colored in a completely separate fixed area. Both
    now go through `panel_lines()` and land in the same scrollable log with
    an identical rounded-border shape -- confirmed here by running both
    kinds back to back in one session and comparing their actual border
    characters, not just asserting each in isolation.
    """
    dag_path = tmp_path / "dag.yaml"
    dag_pipeline_path = tmp_path / "dag_pipeline.yaml"
    dag_source = tmp_path / "dag_input.csv"
    dag_destination = tmp_path / "dag_output.csv"
    write_dag(dag_path, dag_pipeline_path, dag_source, dag_destination)

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_source = tmp_path / "pipeline_input.csv"
    pipeline_destination = tmp_path / "pipeline_output.csv"
    write_pipeline(pipeline_path, pipeline_source, pipeline_destination)

    output_log = run_with_keys_capturing_output_log(
        f"/open {dag_path}\n/run\n/open {pipeline_path}\n/run\n/exit\n"
    )

    rounded_borders = [line for line in output_log.split("\n") if line.startswith(("╭", "╰"))]
    assert (
        len(rounded_borders) == 4
    )  # top+bottom for the DAG panel, top+bottom for the pipeline one
    assert "+---" not in output_log and "+-" not in output_log  # no ASCII fallback border at all
    assert "Status: succeeded" in output_log  # DAG panel
    assert "Rows read: 2" in output_log and "Rows written: 2" in output_log  # pipeline panel


def test_output_log_panel_border_renders_with_real_blue_style_end_to_end(tmp_path: Path) -> None:
    """This story's real feasibility proof, not just structured lexer output
    in isolation (see test_highlighting.py for that): drives a genuine
    headless `Application` all the way through rendering and inspects the
    actual `Screen` cell styles `OutputLogLexer` produced, the same
    technique `test_scroll_events_route_to_the_output_log_not_input_history`
    already uses to inspect real render output elsewhere in this file.
    """
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "dag_pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)

    blue = f"fg:#{BLURPLE[0]:02x}{BLURPLE[1]:02x}{BLURPLE[2]:02x}"
    found: dict[str, bool] = {"border_styled": False, "label_styled": False}

    session = InteractiveSession(render_context=_CAPABLE)
    original_dispatch = session.dispatch

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        app = get_app()
        app._redraw()
        screen = app.renderer._last_screen
        assert screen is not None
        for row in screen.data_buffer.values():
            for cell in row.values():
                if cell.char in "╭╮╰╯─│" and blue in cell.style:
                    found["border_styled"] = True
                if cell.char == "S" and blue in cell.style:  # start of "Status:"
                    found["label_styled"] = True
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(f"/open {dag_path}\n/run\n/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=session)

    assert found["border_styled"] is True
    assert found["label_styled"] is True


def write_failing_sql_dag(dag_path: Path, pipeline_path: Path) -> None:
    """A DAG whose one task fails with a real `ConnectorError` --
    `_create_sql_engine()`'s own "Could not configure SQL connector" path,
    triggered by a malformed connection URL `create_engine()` itself
    rejects (`sqlalchemy.exc.ArgumentError`, a `SQLAlchemyError` subclass)
    -- the exact real error the corruption bug report was about, not a
    synthetic placeholder message.
    """
    pipeline_path.write_text(
        """
name: failing_sql_pipeline
source:
  type: sqlite
  connection_url: "not-a-valid-connection-url"
  table: whatever
destination:
  type: csv
  path: output.csv
""",
        encoding="utf-8",
    )
    dag_path.write_text(
        f"""
name: corruption_repro_dag
tasks:
  - name: only
    pipeline: {pipeline_path.name}
    depends_on: []
""",
        encoding="utf-8",
    )


def test_dag_connector_failure_panel_renders_without_corruption_at_a_realistic_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for a real visual-corruption bug found by live testing:
    a DAG task failing with a real `ConnectorError`, then `/clear`.

    Root cause (confirmed via this exact real headless-render technique
    before writing this test): `panel_lines()` (nexo_art.py) budgets a
    panel's border/padding against the *full* `render_context.width`, but
    the full-screen output log's `TextArea` is built with `scrollbar=True`
    (full_screen.py), which reserves one real column of the `Window`'s
    width for its `ScrollbarMargin` -- confirmed by reading
    `prompt_toolkit.widgets.base.TextArea.__init__` and
    `prompt_toolkit.layout.margins.ScrollbarMargin` directly. A panel
    reaching the full declared width (which NXL-93's own width-capping
    logic deliberately maximizes for a long value like a real connector
    error message) overflowed the real available column by exactly one,
    and `wrap_lines=True` wrapped the orphaned closing border character
    onto its own line -- misaligned columns, fragments appearing to
    "duplicate" at different rows, exactly the reported symptom. Fixed in
    `InteractiveSession._output_render_context()` by reducing the width
    used for this specific sink by one.

    `DummyOutput.get_size()` is monkeypatched to a specific, realistic
    column count that genuinely matches the declared `RenderContext.width`
    -- the correctly-functioning case this bug broke, not an artificially
    mismatched one (a separate, pre-existing concern: `RenderContext.width`
    is detected once at session start and never refreshed on a real
    terminal resize).
    """
    # width=90 (not just any realistic value): confirmed directly that this
    # DAG's exact real error message word-wraps such that its longest panel
    # line reaches the full declared width at 90 -- the precondition for
    # the off-by-one to actually manifest (it does not at every width; a
    # panel's real rendered width depends on where `textwrap.wrap()` breaks
    # the value, not just the declared budget). Also comfortably above
    # `MINIMUM_WIDTH` even after the fix's -1, so this exercises the
    # rounded-panel path, not the plain-mode ASCII fallback.
    monkeypatch.setattr(DummyOutput, "get_size", lambda self: Size(rows=40, columns=90))
    render_context = RenderContext(is_tty=True, color_enabled=True, width=90)

    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    write_failing_sql_dag(dag_path, pipeline_path)

    session = InteractiveSession(render_context=render_context)
    original_dispatch = session.dispatch
    screens: list[list[str]] = []

    def snapshotting_dispatch(command: object) -> bool:
        result = original_dispatch(command)  # type: ignore[arg-type]
        app = get_app()
        app._redraw()
        screen = app.renderer._last_screen
        assert screen is not None
        rows = []
        for y in sorted(screen.data_buffer.keys()):
            row = screen.data_buffer[y]
            rows.append("".join(row[x].char for x in sorted(row.keys())))
        screens.append(rows)
        return result

    session.dispatch = snapshotting_dispatch  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(f"/open {dag_path}\n/run\n/clear\n/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(render_context, session=session)

    run_screen = screens[1]  # after /run
    bordered_rows = [row for row in run_screen if row.startswith("│")]
    assert bordered_rows, "expected the real failure panel to appear in the log"
    for row in bordered_rows:
        stripped = row.rstrip()
        assert stripped.endswith("│"), f"panel row's closing border wrapped away: {row!r}"
    # The exact artifact this bug produced: a lone orphaned closing border
    # on its own physical row, with nothing else on it.
    assert not any(row.strip() == "│" for row in run_screen)
    assert any("Status: failed" in row for row in run_screen)

    clear_screen = screens[2]  # after /clear
    # Rows 0-8 are the static header panel (_PANEL_HEIGHT), which is never
    # touched by /clear and legitimately contains its own "│" border --
    # excluded so this checks the log area specifically, not the header.
    clear_text = "\n".join(clear_screen[9:])
    assert "Status: failed" not in clear_text
    assert "│" not in clear_text
    assert "Tasks:" not in clear_text
    assert "connector" not in clear_text.lower()


def test_stale_width_is_refreshed_after_a_real_terminal_resize_mid_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RenderContext.width` used to be detected once at session start and
    never refreshed on a real terminal resize -- a limitation explicitly
    flagged as out of scope in
    test_dag_connector_failure_panel_renders_without_corruption_at_a_realistic_width
    above. A real physical resize narrows the terminal; prompt_toolkit
    itself notices (its own resize-polling on Windows, SIGWINCH on POSIX)
    and redraws against the new real width -- but before this fix,
    Nexolith's own panel/border budgeting kept using the width detected at
    session start, reproducing the exact same "orphaned closing border"
    pattern the scrollbar-margin fix addressed, but now caused by a stale
    width instead of an unaccounted-for column, and considerably worse (a
    ~20-column mismatch here, not an off-by-one).

    `COLUMNS`/`DummyOutput.get_size()` are monkeypatched together between
    the DAG's two `/run`s -- two independent knobs in this headless
    harness that a real resize moves together via one real OS terminal-size
    query. This proves `refresh_render_context_width()` genuinely picks up
    a live width change before the second panel renders; it does not (and
    cannot) prove anything about a real terminal's own resize-delivery
    mechanism, which remains something only manual verification in a real
    terminal can confirm.
    """
    monkeypatch.setenv("COLUMNS", "90")
    monkeypatch.setattr(DummyOutput, "get_size", lambda self: Size(rows=40, columns=90))
    render_context = RenderContext(is_tty=True, color_enabled=True, width=90)

    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    write_failing_sql_dag(dag_path, pipeline_path)

    # live_width=detect_width: opts this session into the live-refresh
    # behavior under test. Every other test in this file omits it, so
    # `refresh_render_context_width()` stays a no-op there and each
    # fixed-width `RenderContext` used elsewhere keeps behaving exactly as
    # declared, unaffected by this file's ambient ``COLUMNS``/real terminal
    # size.
    session = InteractiveSession(render_context=render_context, live_width=detect_width)
    original_dispatch = session.dispatch
    run_count = {"n": 0}
    screens: list[list[str]] = []

    def resize_after_first_run_then_snapshot(command: object) -> bool:
        if getattr(command, "kind", None) is not None and command.kind.value == "run":  # type: ignore[attr-defined]
            run_count["n"] += 1
            if run_count["n"] == 2:
                # Simulate a real physical resize narrowing the terminal.
                monkeypatch.setenv("COLUMNS", "70")
                monkeypatch.setattr(DummyOutput, "get_size", lambda self: Size(rows=40, columns=70))
        result = original_dispatch(command)  # type: ignore[arg-type]
        app = get_app()
        app._redraw()
        screen = app.renderer._last_screen
        assert screen is not None
        rows = []
        for y in sorted(screen.data_buffer.keys()):
            row = screen.data_buffer[y]
            rows.append("".join(row[x].char for x in sorted(row.keys())))
        screens.append(rows)
        return result

    session.dispatch = resize_after_first_run_then_snapshot  # type: ignore[method-assign]

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(f"/open {dag_path}\n/run\n/run\n/exit\n")
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(render_context, session=session)

    post_resize_screen = screens[2]  # after the second /run, post-resize
    bordered_rows = [row for row in post_resize_screen if row.startswith("│")]
    assert bordered_rows, "expected the post-resize failure panel to appear in the log"
    for row in bordered_rows:
        stripped = row.rstrip()
        assert stripped.endswith("│"), f"panel row's closing border wrapped away: {row!r}"
    assert not any(row.strip() == "│" for row in post_resize_screen)
    assert session.render_context.width == 70


def test_run_result_falls_back_to_plain_ascii_border_when_render_context_is_plain(
    tmp_path: Path,
) -> None:
    """Step 3/4: `RenderContext.plain` fallback still works after
    unification, for both a DAG and a classic pipeline result -- confirmed
    directly rather than assumed, since `panel_lines()`'s ASCII branch is
    reached via a completely different condition (`render_context.plain`)
    than the one this story actually changed (`ansi_capable`).
    """
    dag_path = tmp_path / "dag.yaml"
    dag_pipeline_path = tmp_path / "dag_pipeline.yaml"
    dag_source = tmp_path / "dag_input.csv"
    dag_destination = tmp_path / "dag_output.csv"
    write_dag(dag_path, dag_pipeline_path, dag_source, dag_destination)

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_source = tmp_path / "pipeline_input.csv"
    pipeline_destination = tmp_path / "pipeline_output.csv"
    write_pipeline(pipeline_path, pipeline_source, pipeline_destination)

    narrow = RenderContext(is_tty=True, color_enabled=True, width=200, forced_plain=True)

    output_log = run_with_keys_capturing_output_log(
        f"/open {dag_path}\n/run\n/open {pipeline_path}\n/run\n/exit\n", render_context=narrow
    )

    assert "╭" not in output_log and "╮" not in output_log
    ascii_borders = [
        line
        for line in output_log.split("\n")
        if line.startswith("+") and line.endswith("+") and set(line) == {"+", "-"}
    ]
    assert len(ascii_borders) == 4  # top+bottom for each of the two panels
    assert "Status: succeeded" in output_log
    assert "Rows read: 2" in output_log and "Rows written: 2" in output_log


def test_runs_list_and_show_render_correctly_inside_full_screen(tmp_path: Path) -> None:
    """/runs and /runs <id> reuse the real runs_render.py output inside the
    full-screen session's own scrollable log."""
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)

    output_log = run_with_keys_capturing_output_log(
        f"/open {dag_path}\n/run\n/runs\n/runs 1\n/exit\n"
    )

    plain_output = _ANSI_RE.sub("", output_log)
    assert "ID" in output_log and "DAG" in output_log and "SEVERITY" in output_log
    assert "Status: succeeded" in plain_output  # styled mode colors just the value
    assert "Trigger: manual" in output_log
    assert "Severity: medium" in output_log


def test_output_log_commands_never_contain_raw_ansi_even_on_a_truecolor_session(
    tmp_path: Path,
) -> None:
    """NXL-100 regression, distinct from the truecolor-vs-256-color bug:
    found while fixing it, this is a second, architecturally separate real
    bug -- `output_area` is a plain prompt_toolkit `TextArea`/`Buffer` with
    zero ANSI interpretation at ANY color depth (confirmed directly: even a
    correctly-tiered 256-color code would still show up as literal text
    there, unlike the header/status area, which render through
    prompt_toolkit's own style system and degrade safely on their own).
    So /runs, /scheduler status, and a DAG /run must write plain text to
    the output log regardless of how capable the session's own
    render_context otherwise is -- confirmed here against a genuinely
    truecolor-signaling session specifically, the case most likely to leak
    a raw escape code if this regression ever reappears.
    """
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)

    truecolor_session = InteractiveSession(
        render_context=RenderContext(is_tty=True, color_enabled=True, width=200, truecolor=True)
    )

    output_log = run_with_keys_capturing_output_log(
        f"/open {dag_path}\n/run\n/runs\n/runs 1\n/scheduler status\n/exit\n",
        session=truecolor_session,
    )

    assert "\x1b[" not in output_log
    # Confirm this isn't just an empty/failed capture -- the real content
    # is there, just genuinely unstyled.
    assert "Status: succeeded" in output_log
    assert "ID" in output_log and "SEVERITY" in output_log
    assert "Scheduler:" in output_log and "not running" in output_log


def test_runs_list_with_no_runs_recorded_yet_in_full_screen() -> None:
    output_log = run_with_keys_capturing_output_log("/runs\n/exit\n")

    assert "No DAG runs recorded yet." in output_log


def test_scheduler_status_not_running_renders_correctly_in_full_screen() -> None:
    output_log = run_with_keys_capturing_output_log("/scheduler status\n/exit\n")

    assert "Scheduler:" in output_log
    assert "not running" in output_log


def test_scheduler_status_running_for_a_real_process_in_full_screen() -> None:
    """Real separate process, real PID in the marker file -- matching the
    classic-loop and classic-CLI equivalents of this exact check."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        write_pidfile(default_pidfile_path(), proc.pid, "2026-01-01T00:00:00+00:00")

        output_log = run_with_keys_capturing_output_log("/scheduler status\n/exit\n")

        assert "running" in output_log
        assert str(proc.pid) in output_log
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_scheduler_stop_genuinely_terminates_a_real_process_in_full_screen() -> None:
    """`_scheduler_stop()` itself already polls `is_process_alive()` for a
    bounded ~2s (interactive.py) before choosing between "stopped" and the
    honest "may still be shutting down" wording (NXL-78: stop is best-effort
    on Windows, never guaranteed) -- so which exact wording comes out is
    itself a real race against how long *this* machine takes to actually
    tear the process down, independent of anything this test does. Asserting
    the confirmed-only wording flaked under real machine load (the OS-level
    `proc.wait(timeout=5)` below succeeded either way, well past that ~2s
    budget). Only the genuinely deterministic guarantee is checked here --
    real termination -- matching test_scheduler_cli.py's own
    test_stop_genuinely_terminates_a_real_running_scheduler_and_leaves_its_marker,
    which never asserted this wording either.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        write_pidfile(default_pidfile_path(), proc.pid, "2026-01-01T00:00:00+00:00")
        assert is_process_alive(proc.pid) is True

        output_log = run_with_keys_capturing_output_log("/scheduler stop\n/exit\n")

        proc.wait(timeout=5)
        assert is_process_alive(proc.pid) is False
        lowered = output_log.lower()
        assert "stopped" in lowered or "shutting down" in lowered
    finally:
        if proc.poll() is None:
            proc.kill()


def test_scheduler_start_is_rejected_in_full_screen_without_hanging_the_session() -> None:
    """The real point of this test: /scheduler start must not hang the
    full-screen event loop (Scheduler.run() blocks forever by design) --
    confirmed by the session actually reaching /exit normally afterward,
    not just that the hint text appears."""
    session = InteractiveSession(render_context=_CAPABLE)

    output_log = run_with_keys_capturing_output_log("/scheduler start\n/exit\n", session=session)

    assert "nexolith scheduler start" in output_log
    assert session.context.pipeline is None  # session ended cleanly, nothing hung


def test_completion_menu_offers_the_new_v033_parity_commands() -> None:
    """/help text and the completion menu both surface /runs and
    /scheduler -- confirmed via the real NexolithCompleter the full-screen
    input field actually uses, not a duplicated command list."""
    completer = NexolithCompleter()
    document = Document("/", cursor_position=1)
    completions = [c.text for c in completer.get_completions(document, CompleteEvent())]

    assert "/runs" in completions
    assert "/scheduler" in completions


def test_completion_menu_offers_close_nxl_105() -> None:
    completer = NexolithCompleter()
    document = Document("/", cursor_position=1)
    completions = [c.text for c in completer.get_completions(document, CompleteEvent())]

    assert "/close" in completions
    assert "/clear" in completions
