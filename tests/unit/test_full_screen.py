from __future__ import annotations

from pathlib import Path

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from nexolith.cli.full_screen import run_full_screen_session
from nexolith.cli.interactive import InteractiveSession
from nexolith.cli.render_context import RenderContext

_CAPABLE = RenderContext(is_tty=True, color_enabled=True, width=200)


def run_with_keys(keys: str, *, session: InteractiveSession | None = None) -> InteractiveSession:
    """Drive a real, headless full-screen Application with synthetic keystrokes
    (prompt_toolkit's own testing utilities: a pipe-backed Input and a
    DummyOutput, no real terminal involved) and return the session used."""
    active_session = session or InteractiveSession(render_context=_CAPABLE)
    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keys)
        with create_app_session(input=pipe_input, output=DummyOutput()):
            run_full_screen_session(_CAPABLE, session=active_session)
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
