"""Minimal, testable interactive CLI session."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

PROMPT = "nexolith> "
SPLASH = "Nexo - Nexolith interactive session\nType /help for available commands."
HELP = (
    "Available commands:\n"
    "  /help  Show available commands.\n"
    "  /exit  Exit the interactive session.\n"
    "Additional interactive commands are not available yet."
)
GOODBYE = "Goodbye."

InputReader = Callable[[str], str]
OutputWriter = Callable[[str], None]


class InteractiveCommand(StrEnum):
    EMPTY = "empty"
    HELP = "help"
    EXIT = "exit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    kind: InteractiveCommand
    text: str = ""


def parse_command(value: str) -> ParsedCommand:
    """Parse one interactive input without performing presentation or I/O."""
    command = value.strip()
    if not command:
        return ParsedCommand(InteractiveCommand.EMPTY)
    if command == "/help":
        return ParsedCommand(InteractiveCommand.HELP)
    if command == "/exit":
        return ParsedCommand(InteractiveCommand.EXIT)
    return ParsedCommand(InteractiveCommand.UNKNOWN, command)


def render_splash() -> str:
    return SPLASH


def render_help() -> str:
    return HELP


def render_unknown(command: str) -> str:
    return f"Unknown command: {command}. Type /help for available commands."


class InteractiveSession:
    """Read and dispatch the intentionally small NXL-36 command set."""

    def __init__(
        self,
        *,
        input_reader: InputReader | None = None,
        output_writer: OutputWriter | None = None,
    ) -> None:
        self._read = input_reader or input
        self._write = output_writer or print

    def run(self) -> None:
        """Run until explicit exit, EOF, or an expected keyboard interruption."""
        self._write(render_splash())
        while True:
            try:
                command = parse_command(self._read(PROMPT))
            except (EOFError, KeyboardInterrupt):
                self._write(GOODBYE)
                return

            if command.kind is InteractiveCommand.EMPTY:
                continue
            if command.kind is InteractiveCommand.HELP:
                self._write(render_help())
                continue
            if command.kind is InteractiveCommand.EXIT:
                self._write(GOODBYE)
                return
            self._write(render_unknown(command.text))


def run_interactive_session() -> None:
    """Launch the default terminal-backed session."""
    InteractiveSession().run()
