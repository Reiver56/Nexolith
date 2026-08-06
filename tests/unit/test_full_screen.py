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
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.output import DummyOutput

from nexolith.cli.completion import NexolithCompleter
from nexolith.cli.full_screen import _DIVIDER_COLOR, _divider, run_full_screen_session
from nexolith.cli.interactive import InteractiveSession
from nexolith.cli.nexo_art import BLURPLE
from nexolith.cli.render_context import RenderContext
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
) -> InteractiveSession:
    """Drive a real, headless full-screen Application with synthetic keystrokes
    (prompt_toolkit's own testing utilities: a pipe-backed Input and a
    DummyOutput, no real terminal involved) and return the session used."""
    active_session = session or InteractiveSession(render_context=_CAPABLE)
    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keys)
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=active_session, status_state=status_state)
    return active_session


def run_with_keys_capturing_output_log(
    keys: str,
    *,
    session: InteractiveSession | None = None,
    status_state: StatusAreaState | None = None,
) -> str:
    """Like `run_with_keys`, but also returns the real, final scrollable
    output log text -- what a user would actually see for commands (like
    /help, /runs, /scheduler) that write through the plain output log
    rather than the status area. `output_area` is the only read-only
    Buffer-backed window in the layout (the input field isn't read-only;
    the header/status controls have no Buffer at all), so it's identified
    that way rather than needing `run_full_screen_session` to expose it.
    """
    active_session = session or InteractiveSession(render_context=_CAPABLE)
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
            run_full_screen_session(_CAPABLE, session=active_session, status_state=status_state)
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


def test_run_updates_the_status_area_timeline_and_summary_end_to_end(tmp_path: Path) -> None:
    """Real events, real dispatch, real status area -- not just a unit-level
    check of StatusAreaState in isolation."""
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_pipeline(pipeline, source, destination)
    status = StatusAreaState()

    run_with_keys(f"/open {pipeline}\n/run\n/exit\n", status_state=status)

    assert status.visible is True
    assert status.result is not None
    assert status.result.rows_read == 2
    assert status.result.rows_written == 2
    assert all(step_status.value == "done" for _, step_status in status.steps)


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
    assert status.result is None
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
        assert "stopped" in output_log.lower()
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
