"""Minimal, testable interactive CLI session."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from nexolith.application import PipelineApplication
from nexolith.cli.context import SelectedPipeline, SessionContext
from nexolith.cli.errors import error_category
from nexolith.cli.event_renderer import InteractiveEventRenderer
from nexolith.cli.interactive_types import InputReader, OutputWriter
from nexolith.cli.nexo_art import render_nexo_pixel_art
from nexolith.cli.nexo_kitty import render_nexo_kitty_protocol
from nexolith.cli.render_context import RenderContext, detect_render_context
from nexolith.config import PipelineConfig
from nexolith.events import EventSink
from nexolith.exceptions import ConfigurationError, ExecutionError, NexolithError
from nexolith.models import ExecutionResult

DEFAULT_PROMPT = "nexolith> "
SPLASH = "Nexo - Nexolith interactive session\nType /help for available commands."
HELP = (
    "Available commands:\n"
    "  /help  Show available commands.\n"
    "  /open <path>  Open or replace the current pipeline.\n"
    "  /open  Show the current pipeline.\n"
    "  /clear  Clear the current pipeline.\n"
    "  /validate  Validate the current pipeline.\n"
    "  /run  Run the current pipeline.\n"
    "  /exit  Exit the interactive session.\n"
    "Operations use the pipeline currently shown in the prompt."
)
GOODBYE = "Goodbye."
NO_PIPELINE = "No pipeline is currently open."


class InteractiveCommand(StrEnum):
    EMPTY = "empty"
    HELP = "help"
    OPEN = "open"
    CLEAR = "clear"
    VALIDATE = "validate"
    RUN = "run"
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
    if command == "/validate":
        return ParsedCommand(InteractiveCommand.VALIDATE)
    if command == "/run":
        return ParsedCommand(InteractiveCommand.RUN)
    if command == "/exit":
        return ParsedCommand(InteractiveCommand.EXIT)
    return ParsedCommand(InteractiveCommand.UNKNOWN, command)


def render_splash(render_context: RenderContext | None = None) -> str:
    """Render the startup splash, which also stands in for the prompt's idle state:
    it is the only thing shown before the first (and every subsequent, unchanged)
    prompt in this synchronous, single-shot REPL.

    Tiered: (1) the Kitty graphics protocol at full fidelity, when heuristically
    detected; (2) ANSI truecolor block art, for any other color-capable terminal;
    (3) exactly today's plain text, when `render_context` is absent or `plain`.
    Tier 1 has no synchronous acknowledgement from the terminal, so a build/write
    failure is the only detectable failure mode; any exception there falls back
    to tier 2 rather than risk crashing the session.
    """
    if render_context is None or render_context.plain:
        return SPLASH
    if render_context.use_kitty:
        try:
            return f"{render_nexo_kitty_protocol()}\n{SPLASH}"
        except Exception:
            pass
    return f"{render_nexo_pixel_art()}\n{SPLASH}"


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


class InteractiveApplication(Protocol):
    def validate_pipeline(
        self, path: Path, *, event_sink: EventSink | None = None
    ) -> PipelineConfig: ...

    def run_pipeline(
        self, path: Path, *, event_sink: EventSink | None = None
    ) -> ExecutionResult: ...


class InteractiveSession:
    """Read and dispatch the intentionally small interactive command set."""

    def __init__(
        self,
        *,
        input_reader: InputReader | None = None,
        output_writer: OutputWriter | None = None,
        context: SessionContext | None = None,
        application: InteractiveApplication | None = None,
        render_context: RenderContext | None = None,
    ) -> None:
        self._read = input_reader or input
        self._write = output_writer or print
        self.context = context or SessionContext()
        self._application = application or PipelineApplication()
        # Detected once per session so future colorized/panel renderers (splash,
        # timeline, summary, /validate highlighting) share one consistent capability
        # check instead of re-detecting per render call. See src/nexolith/cli/README.md.
        self.render_context = render_context or detect_render_context()

    def run(self) -> None:
        """Run until explicit exit, EOF, or an expected keyboard interruption."""
        self._write(render_splash(self.render_context))
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
            if command.kind is InteractiveCommand.VALIDATE:
                self._validate_pipeline()
                continue
            if command.kind is InteractiveCommand.RUN:
                self._run_pipeline()
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

    def _validate_pipeline(self) -> None:
        pipeline = self._require_pipeline()
        if pipeline is None:
            return
        renderer = InteractiveEventRenderer(self._write)
        try:
            self._application.validate_pipeline(pipeline.resolved_path, event_sink=renderer)
        except KeyboardInterrupt:
            self._write("Validation interrupted.")
        except ConfigurationError as error:
            self._write(_render_operation_error(error, pipeline))

    def _run_pipeline(self) -> None:
        pipeline = self._require_pipeline()
        if pipeline is None:
            return
        renderer = InteractiveEventRenderer(self._write)
        try:
            result = self._application.run_pipeline(pipeline.resolved_path, event_sink=renderer)
        except KeyboardInterrupt:
            self._write("Execution interrupted.")
        except (ConfigurationError, ExecutionError) as error:
            self._write(_render_operation_error(error, pipeline))
        else:
            self._write(_render_execution_result(result))

    def _require_pipeline(self) -> SelectedPipeline | None:
        if self.context.pipeline is None:
            self._write("No pipeline is currently open. Use /open <path> first.")
        return self.context.pipeline


def _render_operation_error(error: NexolithError, pipeline: SelectedPipeline) -> str:
    category = error_category(error)
    if isinstance(error, ConfigurationError):
        message = str(error).replace(str(pipeline.resolved_path), str(pipeline.requested_path))
    elif category == "connector":
        message = "Pipeline execution failed in a connector. Check the source or destination."
    elif category == "transformation":
        message = "Pipeline execution failed during transformations."
    else:
        message = "Pipeline execution failed."
    return f"Error [{category}]: {message}"


def _render_execution_result(result: ExecutionResult) -> str:
    lines = [
        f"Status: {result.status.value}",
        f"Rows read: {result.rows_read}",
        f"Rows written: {result.rows_written}",
    ]
    if result.duration_seconds is not None:
        lines.append(f"Duration: {result.duration_seconds:.3f}s")
    return "\n".join(lines)


def run_interactive_session() -> None:
    """Launch the default terminal-backed session."""
    InteractiveSession().run()
