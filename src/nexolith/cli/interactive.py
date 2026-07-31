"""Minimal, testable interactive CLI session."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from nexolith.application import PipelineApplication
from nexolith.cli.context import SelectedPipeline, SessionContext
from nexolith.exceptions import ConfigurationError

DEFAULT_PROMPT = "nexolith> "
SPLASH = "Nexo - Nexolith interactive session\nType /help for available commands."
HELP = (
    "Available commands:\n"
    "  /help  Show available commands.\n"
    "  /open <path>  Open or replace the current pipeline.\n"
    "  /open  Show the current pipeline.\n"
    "  /clear  Clear the current pipeline.\n"
    "  /exit  Exit the interactive session.\n"
    "Interactive validation and execution are not available yet."
)
GOODBYE = "Goodbye."
NO_PIPELINE = "No pipeline is currently open."

InputReader = Callable[[str], str]
OutputWriter = Callable[[str], None]


class InteractiveCommand(StrEnum):
    EMPTY = "empty"
    HELP = "help"
    OPEN = "open"
    CLEAR = "clear"
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
    if command == "/open":
        return ParsedCommand(InteractiveCommand.OPEN)
    if command.startswith("/open"):
        parts = command.split(maxsplit=1)
        if parts[0] == "/open" and len(parts) == 2:
            return ParsedCommand(InteractiveCommand.OPEN, parts[1])
    if command == "/clear":
        return ParsedCommand(InteractiveCommand.CLEAR)
    if command == "/exit":
        return ParsedCommand(InteractiveCommand.EXIT)
    return ParsedCommand(InteractiveCommand.UNKNOWN, command)


def render_splash() -> str:
    return SPLASH


def render_help() -> str:
    return HELP


def render_unknown(command: str) -> str:
    return f"Unknown command: {command}. Type /help for available commands."


def render_prompt(context: SessionContext) -> str:
    if context.pipeline is None:
        return DEFAULT_PROMPT
    label = _safe_prompt_label(context.pipeline.resolved_path.name)
    return f"nexolith [{label}]> "


def render_current_pipeline(pipeline: SelectedPipeline | None) -> str:
    if pipeline is None:
        return NO_PIPELINE
    availability = "" if pipeline.resolved_path.is_file() else " (unavailable)"
    return f"Current pipeline: {pipeline.requested_path}{availability}"


def _safe_prompt_label(filename: str) -> str:
    safe = "".join(
        character if character.isprintable() and character not in "[]" else "?"
        for character in filename
    )
    safe = safe or "pipeline"
    return safe if len(safe) <= 32 else f"{safe[:29]}..."


class InteractiveSession:
    """Read and dispatch the intentionally small NXL-36 command set."""

    def __init__(
        self,
        *,
        input_reader: InputReader | None = None,
        output_writer: OutputWriter | None = None,
        context: SessionContext | None = None,
        application: PipelineApplication | None = None,
    ) -> None:
        self._read = input_reader or input
        self._write = output_writer or print
        self.context = context or SessionContext()
        self._application = application or PipelineApplication()

    def run(self) -> None:
        """Run until explicit exit, EOF, or an expected keyboard interruption."""
        self._write(render_splash())
        while True:
            try:
                command = parse_command(self._read(render_prompt(self.context)))
            except (EOFError, KeyboardInterrupt):
                self._write(GOODBYE)
                return

            if command.kind is InteractiveCommand.EMPTY:
                continue
            if command.kind is InteractiveCommand.HELP:
                self._write(render_help())
                continue
            if command.kind is InteractiveCommand.OPEN:
                self._open_pipeline(command.text)
                continue
            if command.kind is InteractiveCommand.CLEAR:
                self._clear_pipeline()
                continue
            if command.kind is InteractiveCommand.EXIT:
                self._write(GOODBYE)
                return
            self._write(render_unknown(command.text))

    def _open_pipeline(self, value: str) -> None:
        if not value:
            self._write(render_current_pipeline(self.context.pipeline))
            return

        requested_path = Path(value)
        try:
            resolved_path = requested_path.resolve()
            self._application.validate_pipeline(requested_path)
        except ConfigurationError as error:
            self._write(f"Could not open pipeline: {error}")
            return
        except (OSError, RuntimeError):
            self._write("Could not open pipeline: the path could not be resolved.")
            return

        self.context.select(requested_path, resolved_path)
        self._write(f"Pipeline opened: {requested_path}")

    def _clear_pipeline(self) -> None:
        if self.context.clear():
            self._write("Pipeline context cleared.")
        else:
            self._write(NO_PIPELINE)


def run_interactive_session() -> None:
    """Launch the default terminal-backed session."""
    InteractiveSession().run()
