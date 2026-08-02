from __future__ import annotations

import threading
from pathlib import Path

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from nexolith.cli.full_screen import _DIVIDER_COLOR, _divider, run_full_screen_session
from nexolith.cli.interactive import InteractiveSession
from nexolith.cli.nexo_art import BLURPLE
from nexolith.cli.render_context import RenderContext
from nexolith.cli.status_area import StatusAreaState

_CAPABLE = RenderContext(is_tty=True, color_enabled=True, width=200)


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
