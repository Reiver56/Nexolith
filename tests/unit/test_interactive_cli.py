from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.application import PipelineApplication
from nexolith.cli import app, interactive
from nexolith.cli.context import SelectedPipeline, SessionContext
from nexolith.cli.interactive import (
    DEFAULT_PROMPT,
    GOODBYE,
    HELP,
    NO_PIPELINE,
    SPLASH,
    InteractiveApplication,
    InteractiveCommand,
    InteractiveSession,
    parse_command,
    render_prompt,
    render_splash,
)
from nexolith.cli.render_context import RenderContext
from nexolith.config.models import PipelineConfig
from nexolith.events import EventSink
from nexolith.exceptions import ConfigurationError, ConnectorError, ExecutionError
from nexolith.models import ExecutionResult
from nexolith.scheduler import default_pidfile_path, is_process_alive, write_pidfile
from nexolith.types import Scalar

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this file gets its own state directory -- never the
    real user's %LOCALAPPDATA%\\Nexolith -- matching test_scheduler_cli.py's
    own convention. Needed for this story's /runs and /scheduler commands,
    which (like their classic-CLI counterparts) open a real StateStore/
    pidfile at the default location unless overridden.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("NEXOLITH_STATE_DIR", str(state_dir))
    return state_dir


type InputStep = str | BaseException | Callable[[], str]


class ScriptedInput:
    def __init__(self, values: list[InputStep]) -> None:
        self._values: Iterator[InputStep] = iter(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        value = next(self._values)
        if isinstance(value, BaseException):
            raise value
        return value() if callable(value) else value


def write_pipeline(path: Path, name: str = "interactive") -> None:
    path.write_text(
        f"""
name: {name}
source:
  type: csv
  path: input.csv
destination:
  type: csv
  path: output.csv
""",
        encoding="utf-8",
    )


def write_executable_pipeline(path: Path, source: Path, destination: Path) -> None:
    source.write_text("id,status\n1,ready\n2,done\n", encoding="utf-8")
    path.write_text(
        f"""
name: interactive-run
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


def run_session(
    commands: list[InputStep],
    *,
    context: SessionContext | None = None,
    application: InteractiveApplication | None = None,
) -> tuple[InteractiveSession, ScriptedInput, list[str]]:
    reader = ScriptedInput(commands)
    output: list[str] = []
    session = InteractiveSession(
        input_reader=reader,
        output_writer=output.append,
        context=context,
        application=application,
    )
    session.run()
    return session, reader, output


def test_session_detects_render_context_by_default() -> None:
    session = InteractiveSession(
        input_reader=iter(["/exit"]).__next__, output_writer=lambda _: None
    )

    assert isinstance(session.render_context, RenderContext)


def test_session_accepts_injected_render_context() -> None:
    forced = RenderContext(is_tty=True, color_enabled=True, width=200)

    session = InteractiveSession(
        input_reader=iter(["/exit"]).__next__,
        output_writer=lambda _: None,
        render_context=forced,
    )

    assert session.render_context is forced


def test_render_splash_is_byte_identical_to_current_text_when_no_context() -> None:
    assert render_splash() == SPLASH
    assert render_splash(None) == SPLASH


def test_render_splash_falls_back_to_plain_text_in_degraded_conditions() -> None:
    non_tty = RenderContext(is_tty=False, color_enabled=True, width=200)
    no_color = RenderContext(is_tty=True, color_enabled=False, width=200)
    narrow = RenderContext(is_tty=True, color_enabled=True, width=40)
    forced = RenderContext(is_tty=True, color_enabled=True, width=200, forced_plain=True)

    for degraded in (non_tty, no_color, narrow, forced):
        assert render_splash(degraded) == SPLASH


def test_render_splash_shows_bordered_panel_on_a_capable_non_kitty_terminal() -> None:
    capable = RenderContext(is_tty=True, color_enabled=True, width=200)

    rendered = render_splash(capable)

    assert rendered != SPLASH
    assert rendered.endswith(SPLASH)
    assert "\x1b[38;2;" in rendered
    assert "\x1b_G" not in rendered
    assert "╭" in rendered and "╮" in rendered
    assert "╰" in rendered and "╯" in rendered
    assert "Nexolith" in rendered


def test_render_splash_uses_kitty_protocol_when_detected() -> None:
    kitty_capable = RenderContext(is_tty=True, color_enabled=True, width=200, kitty_graphics=True)

    rendered = render_splash(kitty_capable)

    assert rendered != SPLASH
    assert rendered.endswith(SPLASH)
    assert "\x1b_G" in rendered


def test_render_splash_falls_back_to_ansi_tier_when_kitty_rendering_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kitty_capable = RenderContext(is_tty=True, color_enabled=True, width=200, kitty_graphics=True)

    def broken_kitty_renderer() -> str:
        raise RuntimeError("simulated tier-1 failure")

    monkeypatch.setattr(interactive, "render_nexo_kitty_protocol", broken_kitty_renderer)

    rendered = render_splash(kitty_capable)

    assert rendered != SPLASH
    assert rendered.endswith(SPLASH)
    assert "\x1b_G" not in rendered
    assert "\x1b[38;2;" in rendered


def test_render_splash_never_attempts_kitty_or_ansi_rendering_when_plain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_be_called() -> str:
        raise AssertionError("plain mode must not invoke colored rendering")

    monkeypatch.setattr(interactive, "render_nexo_kitty_protocol", must_not_be_called)
    monkeypatch.setattr(interactive, "render_nexo_panel", must_not_be_called)

    narrow_but_kitty = RenderContext(is_tty=True, color_enabled=True, width=40, kitty_graphics=True)

    assert render_splash(narrow_but_kitty) == SPLASH


class _RecordingSession:
    """Stand-in for InteractiveSession that records .run() without blocking
    on real IO, so run_interactive_session()'s branch logic is testable in
    isolation from both the classic loop's and the full-screen session's
    actual behavior (each is tested separately, in their own test files)."""

    def __init__(self, *, render_context: RenderContext) -> None:
        self.render_context = render_context
        self.ran = False

    def run(self) -> None:
        self.ran = True


def _recording_session_factory(
    recorded: list[_RecordingSession],
) -> Callable[..., _RecordingSession]:
    def factory(*, render_context: RenderContext) -> _RecordingSession:
        session = _RecordingSession(render_context=render_context)
        recorded.append(session)
        return session

    return factory


def test_run_interactive_session_uses_classic_loop_when_plain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deterministic, tested fallback gate: RenderContext.plain routes to
    the classic loop and never even attempts the full-screen session."""
    plain_context = RenderContext(is_tty=False, color_enabled=True, width=200)
    monkeypatch.setattr(interactive, "detect_render_context", lambda: plain_context)

    def full_screen_must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("full-screen must not be attempted when render_context.plain")

    monkeypatch.setattr(
        "nexolith.cli.full_screen.run_full_screen_session", full_screen_must_not_be_called
    )
    recorded: list[_RecordingSession] = []
    monkeypatch.setattr(interactive, "InteractiveSession", _recording_session_factory(recorded))

    interactive.run_interactive_session()

    assert len(recorded) == 1
    assert recorded[0].render_context is plain_context
    assert recorded[0].ran is True


def test_run_interactive_session_attempts_full_screen_when_capable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capable_context = RenderContext(is_tty=True, color_enabled=True, width=200)
    monkeypatch.setattr(interactive, "detect_render_context", lambda: capable_context)

    calls: list[RenderContext] = []
    monkeypatch.setattr(
        "nexolith.cli.full_screen.run_full_screen_session", lambda ctx: calls.append(ctx)
    )

    def classic_must_not_be_called(**kwargs: object) -> None:
        raise AssertionError("classic loop must not run when full-screen succeeds")

    monkeypatch.setattr(interactive, "InteractiveSession", classic_must_not_be_called)

    interactive.run_interactive_session()

    assert calls == [capable_context]


def test_run_interactive_session_falls_back_to_classic_loop_if_full_screen_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive backstop: if prompt_toolkit can't acquire a real terminal
    for full-screen mode despite is_tty being true, fall back cleanly rather
    than crash -- this only covers setup-time failure, before any output has
    been drawn (see run_interactive_session's docstring)."""
    capable_context = RenderContext(is_tty=True, color_enabled=True, width=200)
    monkeypatch.setattr(interactive, "detect_render_context", lambda: capable_context)

    def broken_full_screen(ctx: RenderContext) -> None:
        raise RuntimeError("simulated: prompt_toolkit couldn't acquire a real terminal")

    monkeypatch.setattr("nexolith.cli.full_screen.run_full_screen_session", broken_full_screen)
    recorded: list[_RecordingSession] = []
    monkeypatch.setattr(interactive, "InteractiveSession", _recording_session_factory(recorded))

    interactive.run_interactive_session()

    assert len(recorded) == 1
    assert recorded[0].render_context is capable_context
    assert recorded[0].ran is True


def test_session_shows_plain_splash_when_render_context_is_degraded() -> None:
    # run_session doesn't inject render_context; default detection under pytest
    # (non-TTY output) must degrade to plain, matching current behavior exactly.
    _, _, output = run_session(["/exit"])

    assert output[0] == SPLASH


def test_session_shows_colored_splash_when_render_context_is_capable() -> None:
    capable = RenderContext(is_tty=True, color_enabled=True, width=200)
    reader = ScriptedInput(["/exit"])
    output: list[str] = []
    session = InteractiveSession(
        input_reader=reader, output_writer=output.append, render_context=capable
    )

    session.run()

    assert output[0] != SPLASH
    assert output[0].endswith(SPLASH)


def test_no_subcommand_starts_interactive_session() -> None:
    result = runner.invoke(app, input="/exit\n")

    assert result.exit_code == 0
    assert "Nexo" in result.output
    assert "Nexolith interactive session" in result.output
    assert DEFAULT_PROMPT in result.output
    assert GOODBYE in result.output
    assert "Traceback" not in result.output


def test_help_lists_only_available_commands() -> None:
    result = runner.invoke(app, input="/help\n/exit\n")

    assert result.exit_code == 0
    assert "/help" in result.output
    assert "/open <path>" in result.output
    assert "/open" in result.output
    assert "/clear" in result.output
    assert "/exit" in result.output
    assert "/run" in result.output
    assert "/validate" in result.output


def test_open_valid_pipeline_and_show_context(tmp_path: Path) -> None:
    pipeline = tmp_path / "pipeline with spaces.yaml"
    write_pipeline(pipeline)

    session, reader, output = run_session([f"/open {pipeline}", "/open", "/exit"])

    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == pipeline.resolve()
    assert any(line.startswith("Pipeline opened:") for line in output)
    assert any(line.startswith("Current pipeline:") for line in output)
    assert reader.prompts == [
        DEFAULT_PROMPT,
        "nexolith [pipeline with spaces.yaml]> ",
        "nexolith [pipeline with spaces.yaml]> ",
    ]


def test_open_without_context_and_clear_without_context_are_safe() -> None:
    session, _, output = run_session(["/open", "/clear", "/exit"])

    assert session.context.pipeline is None
    assert output.count(NO_PIPELINE) == 2


def test_open_replaces_pipeline_and_clear_removes_context(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    write_pipeline(first, "first")
    write_pipeline(second, "second")

    session, reader, output = run_session([f"/open {first}", f"/open {second}", "/clear", "/exit"])

    assert session.context.pipeline is None
    assert "Pipeline context cleared." in output
    assert reader.prompts[-2] == "nexolith [second.yaml]> "
    assert reader.prompts[-1] == DEFAULT_PROMPT


@pytest.mark.parametrize("invalid_kind", ["missing", "directory", "invalid"])
def test_failed_open_preserves_previous_context(tmp_path: Path, invalid_kind: str) -> None:
    current = tmp_path / "current.yaml"
    write_pipeline(current)
    candidate = tmp_path / "candidate.yaml"
    if invalid_kind == "directory":
        candidate.mkdir()
    elif invalid_kind == "invalid":
        candidate.write_text("name: [", encoding="utf-8")

    session, _, output = run_session([f"/open {current}", f"/open {candidate}", "/exit"])

    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == current.resolve()
    assert any(line.startswith("Could not open pipeline:") for line in output)
    assert "Traceback" not in "\n".join(output)


def test_unreadable_pipeline_error_is_recoverable(tmp_path: Path) -> None:
    expected = ConfigurationError("Could not read pipeline file. Check file permissions.")

    def unreadable_loader(
        _path: Path, _overrides: Mapping[str, Scalar] | None = None
    ) -> PipelineConfig:
        raise expected

    application = PipelineApplication(loader=unreadable_loader)
    session, _, output = run_session(
        [f"/open {tmp_path / 'unreadable.yaml'}", "/exit"],
        application=application,
    )

    assert session.context.pipeline is None
    assert output[1] == f"Could not open pipeline: {expected}"


def test_relative_missing_path_does_not_add_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    _, _, output = run_session(["/open missing.yaml", "/exit"])

    diagnostic = output[1]
    assert "missing.yaml" in diagnostic
    assert str(tmp_path) not in diagnostic


def test_deleted_active_pipeline_is_reported_unavailable(tmp_path: Path) -> None:
    pipeline = tmp_path / "deleted.yaml"
    write_pipeline(pipeline)

    def delete_and_show() -> str:
        pipeline.unlink()
        return "/open"

    session, _, output = run_session([f"/open {pipeline}", delete_and_show, "/exit"])

    assert session.context.pipeline is not None
    assert output[-2].endswith("(unavailable)")


def test_modified_pipeline_is_reloaded_without_replacing_context(tmp_path: Path) -> None:
    pipeline = tmp_path / "modified.yaml"
    write_pipeline(pipeline)

    def invalidate_and_reopen() -> str:
        pipeline.write_text("name: [", encoding="utf-8")
        return f"/open {pipeline}"

    session, _, output = run_session([f"/open {pipeline}", invalidate_and_reopen, "/exit"])

    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == pipeline.resolve()
    assert any("Invalid YAML" in line for line in output)


def test_empty_unknown_and_multiple_commands_do_not_end_session() -> None:
    session, reader, output = run_session(["", "/missing", "/help", "/exit"])

    assert session.context.pipeline is None
    assert output[0] == SPLASH
    assert "Unknown command: /missing. Type /help for available commands." in output
    assert HELP in output
    assert output[-1] == GOODBYE
    assert reader.prompts == [DEFAULT_PROMPT] * 4


def test_eof_and_keyboard_interrupt_close_cleanly() -> None:
    for interruption in (EOFError(), KeyboardInterrupt()):
        _, _, output = run_session([interruption])
        assert output == [SPLASH, GOODBYE]


def test_prompt_sanitizes_and_truncates_unusual_filename() -> None:
    context = SessionContext(
        SelectedPipeline(
            Path("requested.yaml"),
            Path("a[very]long-pipeline-name-that-needs-truncation.yaml"),
        )
    )

    prompt = render_prompt(context)

    assert prompt.startswith("nexolith [a?very?long-pipeline-name-")
    assert len(prompt) <= len("nexolith []> ") + 32


def test_command_parser_has_small_explicit_contract() -> None:
    assert parse_command("  ").kind is InteractiveCommand.EMPTY
    assert parse_command(" /help ").kind is InteractiveCommand.HELP
    assert parse_command("/open").kind is InteractiveCommand.OPEN
    assert parse_command("/open pipeline with spaces.yaml").text == "pipeline with spaces.yaml"
    assert parse_command("/clear").kind is InteractiveCommand.CLEAR
    assert parse_command("/validate").kind is InteractiveCommand.VALIDATE
    assert parse_command("/run").kind is InteractiveCommand.RUN
    assert parse_command("/runs").kind is InteractiveCommand.RUNS
    assert parse_command("/runs 42").text == "42"
    assert parse_command("/scheduler").kind is InteractiveCommand.SCHEDULER
    assert parse_command("/scheduler status").text == "status"
    assert parse_command("/exit").kind is InteractiveCommand.EXIT
    assert parse_command("validate").kind is InteractiveCommand.UNKNOWN


class RecordingApplication:
    def __init__(self) -> None:
        self.validated_paths: list[Path] = []
        self.run_paths: list[Path] = []
        self.validation_error: BaseException | None = None
        self.run_error: BaseException | None = None

    def validate_pipeline(
        self, path: Path, *, event_sink: EventSink | None = None
    ) -> PipelineConfig:
        self.validated_paths.append(path)
        if self.validation_error is not None:
            raise self.validation_error
        return PipelineConfig.model_validate(
            {
                "name": "recorded",
                "source": {"type": "csv", "path": "input.csv"},
                "destination": {"type": "csv", "path": "output.csv"},
            }
        )

    def run_pipeline(self, path: Path, *, event_sink: EventSink | None = None) -> ExecutionResult:
        self.run_paths.append(path)
        if self.run_error is not None:
            raise self.run_error
        result = ExecutionResult(pipeline_name="recorded")
        result.start()
        result.rows_read = 1
        result.succeed(1)
        return result


def selected_context(path: Path) -> SessionContext:
    return SessionContext(SelectedPipeline(Path(path.name), path.resolve()))


def test_validate_and_run_require_active_pipeline() -> None:
    _, _, output = run_session(["/validate", "/run", "/exit"])

    assert output.count("No pipeline is currently open. Use /open <path> first.") == 2
    assert output[-1] == GOODBYE


def test_validate_and_run_use_resolved_path(tmp_path: Path) -> None:
    pipeline = tmp_path / "selected.yaml"
    application = RecordingApplication()
    context = selected_context(pipeline)

    _, _, output = run_session(
        ["/validate", "/run", "/exit"],
        context=context,
        application=application,
    )

    assert application.validated_paths == [pipeline.resolve()]
    assert application.run_paths == [pipeline.resolve()]
    assert any(line.startswith("Status: succeeded") for line in output)


@pytest.mark.parametrize("operation", ["/validate", "/run"])
def test_keyboard_interrupt_returns_to_prompt(tmp_path: Path, operation: str) -> None:
    pipeline = tmp_path / "selected.yaml"
    application = RecordingApplication()
    if operation == "/validate":
        application.validation_error = KeyboardInterrupt()
        expected = "Validation interrupted."
    else:
        application.run_error = KeyboardInterrupt()
        expected = "Execution interrupted."

    _, reader, output = run_session(
        [operation, "/help", "/exit"],
        context=selected_context(pipeline),
        application=application,
    )

    assert expected in output
    assert HELP in output
    assert output[-1] == GOODBYE
    assert len(reader.prompts) == 3
    assert "Traceback" not in "\n".join(output)


def test_execution_error_is_redacted_and_session_remains_usable(tmp_path: Path) -> None:
    secret = "nxl38-recognizable-secret"
    application = RecordingApplication()
    error = ExecutionError(f"failed with {secret}")
    error.__cause__ = ConnectorError(f"connector leaked {secret}")
    application.run_error = error

    session, _, output = run_session(
        ["/run", "/open", "/exit"],
        context=selected_context(tmp_path / "selected.yaml"),
        application=application,
    )

    rendered = "\n".join(output)
    assert session.context.pipeline is not None
    assert "Error [connector]" in rendered
    assert secret not in rendered
    assert "Traceback" not in rendered
    assert "Current pipeline:" in rendered


def test_real_validate_and_run_render_ordered_events_and_metrics(tmp_path: Path) -> None:
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_executable_pipeline(pipeline, source, destination)

    _, _, output = run_session([f"/open {pipeline}", "/validate", "/run", "/exit"])

    expected = [
        "Loading pipeline...",
        "Pipeline valid.",
        "Loading pipeline...",
        "Pipeline loaded.",
        "Starting execution...",
        "Extracting...",
        "Extraction completed: 2 rows read.",
        "Transforming...",
        "Transformations completed: 2 rows ready.",
        "Writing...",
        "Write completed: 2 rows written.",
        "Pipeline completed.",
    ]
    cursor = 0
    for line in expected:
        cursor = output.index(line, cursor) + 1
    rendered = "\n".join(output)
    assert "Status: succeeded" in rendered
    assert "Rows read: 2" in rendered
    assert "Rows written: 2" in rendered
    assert "Duration: " in rendered
    assert destination.is_file()


@pytest.mark.parametrize("operation", ["/validate", "/run"])
@pytest.mark.parametrize("change", ["invalid", "deleted"])
def test_operation_reloads_changed_pipeline_and_preserves_context(
    tmp_path: Path, operation: str, change: str
) -> None:
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(pipeline)

    def change_pipeline() -> str:
        if change == "invalid":
            pipeline.write_text("name: [", encoding="utf-8")
        else:
            pipeline.unlink()
        return operation

    session, _, output = run_session([f"/open {pipeline}", change_pipeline, "/open", "/exit"])

    rendered = "\n".join(output)
    assert session.context.pipeline is not None
    assert "Error [configuration]" in rendered
    assert "Current pipeline:" in rendered
    assert "Traceback" not in rendered


def test_five_essential_commands_are_discoverable_and_invocable_as_a_set(tmp_path: Path) -> None:
    """Pin NXL-39: /help, /open, /validate, /run, /exit are each discoverable via /help
    and each actually invocable in a single session."""
    pipeline = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_executable_pipeline(pipeline, source, destination)

    session, reader, output = run_session(
        ["/help", f"/open {pipeline}", "/validate", "/run", "/exit"]
    )

    help_text = "\n".join(output)
    for command in ("/help", "/open", "/validate", "/run", "/exit"):
        assert command in help_text

    assert any(line.startswith("Pipeline opened:") for line in output)
    assert "Pipeline valid." in output
    assert any(line.startswith("Status: succeeded") for line in output)
    assert output[-1] == GOODBYE
    assert session.context.pipeline is not None
    assert len(reader.prompts) == 5


def test_help_does_not_expose_deferred_v031_or_v050_features() -> None:
    """Pin NXL-39's explicit deferrals: completion, persistent history, and /logs
    must not appear in the discoverable command surface for v0.3.0."""
    _, _, output = run_session(["/help", "/exit"])

    help_text = "\n".join(output).lower()
    assert "/logs" not in help_text
    assert "completion" not in help_text
    assert "history" not in help_text


# -- NXL-99: full-screen/classic-shared session parity (scheduler, runs, DAG) -


def write_dag(dag_path: Path, pipeline_path: Path, source: Path, destination: Path) -> None:
    source.write_text("id,status\n1,ready\n2,done\n", encoding="utf-8")
    pipeline_path.write_text(
        f"""
name: dag_task_pipeline
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
name: interactive_dag
tasks:
  - name: only
    pipeline: {pipeline_path.name}
    depends_on: []
""",
        encoding="utf-8",
    )


def write_failing_dag(dag_path: Path, pipeline_path: Path) -> None:
    """A DAG that's structurally valid (so /open succeeds -- load_dag()
    validates every task's pipeline reference exists) but genuinely fails
    when actually run: its one task's pipeline points at a source file
    that doesn't exist."""
    pipeline_path.write_text(
        f"""
name: failing_task_pipeline
source:
  type: csv
  path: {(pipeline_path.parent / "does_not_exist.csv").as_posix()}
transformations: []
destination:
  type: csv
  path: {(pipeline_path.parent / "unreachable_output.csv").as_posix()}
""",
        encoding="utf-8",
    )
    dag_path.write_text(
        f"""
name: interactive_failing_dag
tasks:
  - name: only
    pipeline: {pipeline_path.name}
    depends_on: []
""",
        encoding="utf-8",
    )


def test_help_lists_the_new_v033_parity_commands() -> None:
    """NXL-99: /runs and /scheduler must be discoverable via /help, alongside
    the original five commands (unchanged)."""
    _, _, output = run_session(["/help", "/exit"])

    help_text = "\n".join(output)
    assert "/runs" in help_text
    assert "/runs <id>" in help_text
    assert "/scheduler status" in help_text
    assert "/scheduler stop" in help_text
    # /scheduler start is deliberately not offered as a real interactive
    # command -- see test_scheduler_start_is_rejected_with_a_helpful_hint.
    assert "scheduler start" in help_text.lower()  # still mentioned, as a hint


def test_open_validate_run_a_dag_end_to_end(tmp_path: Path) -> None:
    """/open, /validate, /run all extended to recognize a DAG file the same
    way the classic CLI's validate/run commands do (detect_document_kind()),
    reusing execute_dag()/render_run_detail() -- not a parallel
    implementation. Real state recorded, real file written by the DAG's
    own task.
    """
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)

    session, _, output = run_session([f"/open {dag_path}", "/validate", "/run", "/exit"])

    rendered = "\n".join(output)
    assert any(line.startswith("DAG opened:") for line in output)
    assert session.context.pipeline is not None
    assert session.context.pipeline.resolved_path == dag_path.resolve()
    assert "DAG 'interactive_dag' is valid (1 task)." in output
    assert "Status: succeeded" in rendered
    assert "DAG: interactive_dag" in rendered
    assert "[OK] only" in rendered
    assert destination.is_file()
    assert destination.read_text(encoding="utf-8").strip().splitlines() == [
        "id,status",
        "1,ready",
        "2,done",
    ]


def test_run_a_failing_dag_reports_failure_and_session_stays_usable(tmp_path: Path) -> None:
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    write_failing_dag(dag_path, pipeline_path)

    session, _, output = run_session([f"/open {dag_path}", "/run", "/help", "/exit"])

    rendered = "\n".join(output)
    assert "Status: failed" in rendered
    assert HELP in output  # session still usable after a DAG execution failure
    assert session.context.pipeline is not None


def test_open_invalid_dag_reports_error_via_the_shared_error_renderer(tmp_path: Path) -> None:
    dag_path = tmp_path / "bad_dag.yaml"
    dag_path.write_text("name: x\ntasks: []\n", encoding="utf-8")  # tasks must be non-empty

    _, _, output = run_session([f"/open {dag_path}", "/exit"])

    assert any(line.startswith("Could not open pipeline:") for line in output)


def test_runs_list_with_no_runs_recorded_yet() -> None:
    _, _, output = run_session(["/runs", "/exit"])

    assert "No DAG runs recorded yet." in output


def test_runs_list_and_show_reuse_real_rendering_after_a_dag_run(tmp_path: Path) -> None:
    """Confirms /runs (list) and /runs <id> (show) reuse runs_render.py's
    real rendering functions -- the exact bordered-panel/table output
    `nexolith runs list`/`runs show` produce -- rather than a parallel
    implementation for the interactive session.
    """
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    source = tmp_path / "input.csv"
    destination = tmp_path / "output.csv"
    write_dag(dag_path, pipeline_path, source, destination)

    _, _, output = run_session([f"/open {dag_path}", "/run", "/runs", "/runs 1", "/exit"])

    rendered = "\n".join(output)
    # /runs (list): the real table header from render_runs_list.
    assert (
        "ID" in rendered and "DAG" in rendered and "STATUS" in rendered and "SEVERITY" in rendered
    )
    # /runs 1 (show): the real bordered panel from render_run_detail.
    assert rendered.count("Status: succeeded") >= 1
    assert "Trigger: manual" in rendered
    assert "Severity: medium" in rendered


def test_runs_show_rejects_a_non_numeric_id() -> None:
    _, _, output = run_session(["/runs abc", "/exit"])

    assert any("Usage: /runs [id]" in line for line in output)


def test_runs_show_reports_not_found_for_a_nonexistent_run() -> None:
    _, _, output = run_session(["/runs 999", "/exit"])

    assert "No DAG run found with id 999." in output


def test_scheduler_status_reports_not_running_when_no_marker_file_exists() -> None:
    _, _, output = run_session(["/scheduler status", "/exit"])

    assert any("Scheduler:" in line and "not running" in line for line in output)


def test_scheduler_status_reports_running_for_a_real_live_process() -> None:
    """Not just trusting a marker file's existence -- a real separate
    process is spawned and its actual PID written to the marker, matching
    test_scheduler_cli.py's own convention for this exact check."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        write_pidfile(default_pidfile_path(), proc.pid, "2026-01-01T00:00:00+00:00")

        _, _, output = run_session(["/scheduler status", "/exit"])

        rendered = "\n".join(output)
        assert "Scheduler:" in rendered
        assert "running" in rendered
        assert str(proc.pid) in rendered
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_scheduler_stop_reports_not_running_when_no_marker_exists() -> None:
    _, _, output = run_session(["/scheduler stop", "/exit"])

    assert "Scheduler is not running." in output


def test_scheduler_stop_genuinely_terminates_a_real_running_scheduler() -> None:
    """Real process, real termination -- mirrors
    test_scheduler_cli.py's test_stop_genuinely_terminates_a_real_running_scheduler
    exactly, but driven through the interactive session's /scheduler stop
    instead of calling stop_process()/the classic CLI command directly.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        write_pidfile(default_pidfile_path(), proc.pid, "2026-01-01T00:00:00+00:00")
        assert is_process_alive(proc.pid) is True

        _, _, output = run_session(["/scheduler stop", "/exit"])

        proc.wait(timeout=5)
        assert is_process_alive(proc.pid) is False
        assert any("stopped" in line.lower() for line in output)
        assert not default_pidfile_path().exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_scheduler_start_is_rejected_with_a_helpful_hint() -> None:
    """Deliberate design choice (NXL-99): /scheduler start is not wired to
    actually run the daemon in-session -- Scheduler.run() blocks forever by
    design (it's meant to own a dedicated foreground process), and this
    session has no background thread to run it on instead (a new one would
    violate the story's own constraint). Confirm it's rejected cleanly with
    a helpful pointer to the real command, not silently ignored as
    'unknown', and that the session keeps working afterward.
    """
    session, _, output = run_session(["/scheduler start", "/help", "/exit"])

    rendered = "\n".join(output)
    assert "nexolith scheduler start" in rendered
    assert "Unknown command" not in rendered
    assert HELP in output
    assert session.context.pipeline is None  # nothing hung or crashed


def test_scheduler_unknown_subcommand_is_reported_clearly() -> None:
    _, _, output = run_session(["/scheduler bogus", "/exit"])

    assert any("Unknown scheduler command: bogus" in line for line in output)


def test_bare_scheduler_shows_usage() -> None:
    _, _, output = run_session(["/scheduler", "/exit"])

    assert "Usage: /scheduler status|stop" in output


def test_real_run_failure_is_redacted_and_session_remains_usable(tmp_path: Path) -> None:
    pipeline = tmp_path / "pipeline.yaml"
    missing_source = tmp_path / "private-user-password.csv"
    destination = tmp_path / "output.csv"
    write_executable_pipeline(pipeline, missing_source, destination)
    missing_source.unlink()

    session, _, output = run_session([f"/open {pipeline}", "/run", "/help", "/exit"])

    rendered = "\n".join(output)
    assert session.context.pipeline is not None
    assert "Pipeline failed during extraction." in rendered
    assert "Error [connector]" in rendered
    assert "private-user-password" not in rendered
    assert HELP in output
    assert "Traceback" not in rendered
