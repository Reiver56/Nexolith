from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.application import PipelineApplication
from nexolith.cli import app
from nexolith.cli.context import SelectedPipeline, SessionContext
from nexolith.cli.interactive import (
    DEFAULT_PROMPT,
    GOODBYE,
    HELP,
    NO_PIPELINE,
    SPLASH,
    InteractiveCommand,
    InteractiveSession,
    parse_command,
    render_prompt,
)
from nexolith.config.models import PipelineConfig
from nexolith.exceptions import ConfigurationError

runner = CliRunner()
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


def run_session(
    commands: list[InputStep],
    *,
    context: SessionContext | None = None,
    application: PipelineApplication | None = None,
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
    assert "/run" not in result.output
    assert "/validate" not in result.output


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

    def unreadable_loader(_: Path) -> PipelineConfig:
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
    assert parse_command("/exit").kind is InteractiveCommand.EXIT
    assert parse_command("validate").kind is InteractiveCommand.UNKNOWN
