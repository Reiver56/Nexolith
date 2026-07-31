from __future__ import annotations

from collections.abc import Iterator

from typer.testing import CliRunner

from nexolith.cli import app
from nexolith.cli.interactive import (
    GOODBYE,
    HELP,
    PROMPT,
    SPLASH,
    InteractiveCommand,
    InteractiveSession,
    parse_command,
)

runner = CliRunner()


class ScriptedInput:
    def __init__(self, values: list[str | BaseException]) -> None:
        self._values: Iterator[str | BaseException] = iter(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        value = next(self._values)
        if isinstance(value, BaseException):
            raise value
        return value


def test_no_subcommand_starts_interactive_session() -> None:
    result = runner.invoke(app, input="/exit\n")

    assert result.exit_code == 0
    assert "Nexo" in result.output
    assert "Nexolith interactive session" in result.output
    assert PROMPT in result.output
    assert GOODBYE in result.output
    assert "Traceback" not in result.output


def test_help_lists_only_available_commands() -> None:
    result = runner.invoke(app, input="/help\n/exit\n")

    assert result.exit_code == 0
    assert "/help" in result.output
    assert "/exit" in result.output
    assert "/open" not in result.output
    assert "/run" not in result.output
    assert "/validate" not in result.output


def test_empty_and_unknown_commands_do_not_end_session() -> None:
    reader = ScriptedInput(["", "/missing", "/exit"])
    output: list[str] = []

    InteractiveSession(input_reader=reader, output_writer=output.append).run()

    assert output[0] == SPLASH
    assert output[1] == "Unknown command: /missing. Type /help for available commands."
    assert output[-1] == GOODBYE
    assert reader.prompts == [PROMPT, PROMPT, PROMPT]


def test_multiple_commands_run_in_one_session() -> None:
    reader = ScriptedInput(["/help", "/unknown", "/help", "/exit"])
    output: list[str] = []

    InteractiveSession(input_reader=reader, output_writer=output.append).run()

    assert output.count(HELP) == 2
    assert any("Unknown command" in line for line in output)
    assert output[-1] == GOODBYE


def test_eof_closes_session_cleanly() -> None:
    reader = ScriptedInput([EOFError()])
    output: list[str] = []

    InteractiveSession(input_reader=reader, output_writer=output.append).run()

    assert output == [SPLASH, GOODBYE]


def test_keyboard_interrupt_closes_session_cleanly() -> None:
    reader = ScriptedInput([KeyboardInterrupt()])
    output: list[str] = []

    InteractiveSession(input_reader=reader, output_writer=output.append).run()

    assert output == [SPLASH, GOODBYE]


def test_command_parser_has_small_explicit_contract() -> None:
    assert parse_command("  ").kind is InteractiveCommand.EMPTY
    assert parse_command(" /help ").kind is InteractiveCommand.HELP
    assert parse_command("/exit").kind is InteractiveCommand.EXIT
    assert parse_command("validate").kind is InteractiveCommand.UNKNOWN
