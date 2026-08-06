"""Minimal, testable interactive CLI session."""

import sys
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from nexolith.application import PipelineApplication
from nexolith.cli.context import SelectedPipeline, SessionContext
from nexolith.cli.document_kind import DocumentKind, detect_document_kind
from nexolith.cli.errors import error_category
from nexolith.cli.event_renderer import InteractiveEventRenderer
from nexolith.cli.interactive_types import InputReader, OutputWriter
from nexolith.cli.nexo_art import render_nexo_panel
from nexolith.cli.nexo_kitty import render_nexo_kitty_protocol
from nexolith.cli.render_context import RenderContext, detect_render_context
from nexolith.cli.runs_render import render_run_detail, render_run_not_found, render_runs_list
from nexolith.cli.scheduler_render import (
    SchedulerStatus,
    render_scheduler_not_running,
    render_scheduler_status,
    render_scheduler_stop_uncertain,
    render_scheduler_stopped,
    render_windows_stop_caveat,
)
from nexolith.config import PipelineConfig
from nexolith.dag import execute_dag, load_dag
from nexolith.events import EventSink, PipelineOperation
from nexolith.exceptions import ConfigurationError, ExecutionError, NexolithError
from nexolith.models import ExecutionResult
from nexolith.scheduler import (
    default_pidfile_path,
    is_process_alive,
    read_pidfile,
    remove_pidfile,
    stop_process,
)
from nexolith.state import StateStore

DEFAULT_PROMPT = "nexolith> "
SPLASH = "Nexo - Nexolith interactive session\nType /help for available commands."
HELP = (
    "Available commands:\n"
    "  /help  Show available commands.\n"
    "  /open <path>  Open or replace the current pipeline or DAG.\n"
    "  /open  Show the current pipeline or DAG.\n"
    "  /clear  Clear the current pipeline or DAG.\n"
    "  /validate  Validate the current pipeline or DAG.\n"
    "  /run  Run the current pipeline or DAG.\n"
    "  /runs  List recent DAG runs.\n"
    "  /runs <id>  Show detail for one DAG run.\n"
    "  /scheduler status  Report whether the scheduler daemon is running.\n"
    "  /scheduler stop  Stop a running scheduler daemon.\n"
    "  /exit  Exit the interactive session.\n"
    "Operations use the pipeline or DAG currently shown in the prompt.\n"
    "The scheduler itself is started from a separate terminal "
    "(nexolith scheduler start) -- it runs as its own long-lived process."
)
GOODBYE = "Goodbye."
NO_PIPELINE = "No pipeline is currently open."
_SCHEDULER_START_HINT = (
    "Start the scheduler in its own terminal: nexolith scheduler start. "
    "It runs as a long-lived foreground process, so it can't run inside "
    "this session without blocking it."
)


class InteractiveCommand(StrEnum):
    EMPTY = "empty"
    HELP = "help"
    OPEN = "open"
    CLEAR = "clear"
    VALIDATE = "validate"
    RUN = "run"
    RUNS = "runs"
    SCHEDULER = "scheduler"
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
    if command == "/runs":
        return ParsedCommand(InteractiveCommand.RUNS)
    if command.startswith("/runs"):
        parts = command.split(maxsplit=1)
        if parts[0] == "/runs" and len(parts) == 2:
            return ParsedCommand(InteractiveCommand.RUNS, parts[1])
    if command == "/scheduler":
        return ParsedCommand(InteractiveCommand.SCHEDULER)
    if command.startswith("/scheduler"):
        parts = command.split(maxsplit=1)
        if parts[0] == "/scheduler" and len(parts) == 2:
            return ParsedCommand(InteractiveCommand.SCHEDULER, parts[1])
    if command == "/exit":
        return ParsedCommand(InteractiveCommand.EXIT)
    return ParsedCommand(InteractiveCommand.UNKNOWN, command)


def render_splash(render_context: RenderContext | None = None) -> str:
    """Render the startup splash, which also stands in for the prompt's idle state:
    it is the only thing shown before the first (and every subsequent, unchanged)
    prompt in this synchronous, single-shot REPL.

    Tiered: (1) the Kitty graphics protocol at full fidelity, when heuristically
    detected; (2) a bordered ANSI truecolor panel around the same block art, for
    any other color-capable terminal; (3) exactly today's plain text, when
    `render_context` is absent or `plain`. Tier 1 has no synchronous
    acknowledgement from the terminal, so a build/write failure is the only
    detectable failure mode; any exception there falls back to tier 2 rather
    than risk crashing the session.
    """
    if render_context is None or render_context.plain:
        return SPLASH
    if render_context.use_kitty:
        try:
            return f"{render_nexo_kitty_protocol()}\n{SPLASH}"
        except Exception:
            pass
    return f"{render_nexo_panel(render_context)}\n{SPLASH}"


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


class OperationPresenter(Protocol):
    """How `/validate` and `/run` progress and outcomes get shown. The
    classic loop and the full-screen session each inject their own;
    `InteractiveSession`'s dispatch logic is identical either way.
    """

    def event_sink(self, operation: PipelineOperation) -> EventSink: ...
    def show_result(self, result: ExecutionResult) -> None: ...
    def show_validation_result(self, config: PipelineConfig) -> None: ...
    def show_error(
        self, error: NexolithError, pipeline: SelectedPipeline, operation: PipelineOperation
    ) -> None: ...


class ClassicOperationPresenter:
    """Today's plain-text behavior: each event is written as a line
    immediately, the final result/error is one more line. Unchanged from
    before this story's presenter seam existed.
    """

    def __init__(self, write: OutputWriter) -> None:
        self._write = write

    def event_sink(self, operation: PipelineOperation) -> EventSink:
        return InteractiveEventRenderer(self._write)

    def show_result(self, result: ExecutionResult) -> None:
        self._write(render_execution_result(result))

    def show_validation_result(self, config: PipelineConfig) -> None:
        # No-op: the event stream already prints "Pipeline valid." (see
        # event_renderer.render_event's PipelineOperation.VALIDATE branch) --
        # classic mode's per-event lines are its terminal-state signal, same
        # as every other event here. Only the full-screen presenter's status
        # area needs an explicit completion call, since it replaces a live
        # timeline in place rather than appending lines.
        pass

    def show_error(
        self, error: NexolithError, pipeline: SelectedPipeline, operation: PipelineOperation
    ) -> None:
        self._write(render_operation_error(error, pipeline))


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
        presenter: OperationPresenter | None = None,
    ) -> None:
        self._read = input_reader or input
        self._write = output_writer or print
        # NXL-100: the classic loop's default writer (print) goes straight
        # to a real terminal stream, which can render whatever ANSI tier
        # self.render_context says it can. The full-screen session's own
        # writer (see set_output_writer) targets a plain-text prompt_toolkit
        # TextArea buffer instead -- that widget has no ANSI interpretation
        # at all, at any color depth, so anything with embedded escape
        # codes written there shows up as literal text regardless of tier.
        # True by default (matches every existing caller's real behavior
        # today, including every test that never sets this explicitly).
        self._output_ansi_capable = True
        self.context = context or SessionContext()
        self._application = application or PipelineApplication()
        self._presenter: OperationPresenter = presenter or ClassicOperationPresenter(self._write)
        # Detected once per session so future colorized/panel renderers (splash,
        # timeline, summary, /validate highlighting) share one consistent capability
        # check instead of re-detecting per render call. See src/nexolith/cli/README.md.
        self.render_context = render_context or detect_render_context()
        if __import__("os").environ.get("NEXOLITH_DEBUG_RENDER"):
            import sys as _sys

            _sys.stderr.write("=== NEXOLITH_DEBUG_RENDER (temporary, not committed) ===\n")
            _sys.stderr.write(f"sys.stdout type: {type(_sys.stdout)!r}\n")
            _sys.stderr.write(f"sys.stdout is sys.__stdout__: {_sys.stdout is _sys.__stdout__!r}\n")
            _sys.stderr.write(f"sys.stdout.isatty(): {_sys.stdout.isatty()!r}\n")
            _sys.stderr.write(f"sys.stdout.encoding: {getattr(_sys.stdout, 'encoding', None)!r}\n")
            _sys.stderr.write(f"self._write is builtins.print: {self._write is print!r}\n")
            for _field in (
                "is_tty",
                "color_enabled",
                "width",
                "forced_plain",
                "encoding_safe",
                "kitty_graphics",
            ):
                _sys.stderr.write(
                    f"render_context.{_field}: {getattr(self.render_context, _field)!r}\n"
                )
            _sys.stderr.write(f"render_context.plain: {self.render_context.plain!r}\n")
            _sys.stderr.write(f"render_context.use_kitty: {self.render_context.use_kitty!r}\n")
            _sys.stderr.write("=== end debug ===\n")

    def set_output_writer(self, writer: OutputWriter, *, ansi_capable: bool = True) -> None:
        """Redirect where this session's output goes, e.g. to a full-screen
        session's scrollable log instead of the classic loop's direct writes.

        `ansi_capable=False` (NXL-100) marks a sink that cannot interpret
        embedded ANSI escape codes at all -- e.g. the full-screen session's
        plain-text output log -- so `_output_render_context()` forces plain
        rendering for anything written through it, regardless of what this
        session's own `render_context` otherwise detected. Defaults to True,
        matching every caller before this story (a real terminal stream).
        """
        self._write = writer
        self._output_ansi_capable = ansi_capable

    def set_presenter(self, presenter: OperationPresenter) -> None:
        """Redirect how `/validate`/`/run` progress and outcomes are shown,
        e.g. to a full-screen session's step timeline and summary panel
        instead of the classic loop's plain-text lines."""
        self._presenter = presenter

    def run(self) -> None:
        """Run until explicit exit, EOF, or an expected keyboard interruption."""
        self._write(render_splash(self.render_context))
        while True:
            try:
                command = parse_command(self._read(render_prompt(self.context)))
            except (EOFError, KeyboardInterrupt):
                self._write(GOODBYE)
                return
            if not self.dispatch(command):
                return

    def dispatch(self, command: ParsedCommand) -> bool:
        """Handle one already-parsed command. Returns False when the session
        should end (explicit `/exit`). Shared by the classic blocking loop
        (`run`) and the full-screen session so both dispatch identically —
        only presentation differs between them.
        """
        if command.kind is InteractiveCommand.EMPTY:
            return True
        if command.kind is InteractiveCommand.HELP:
            self._write(render_help())
            return True
        if command.kind is InteractiveCommand.OPEN:
            self._open_pipeline(command.text)
            return True
        if command.kind is InteractiveCommand.CLEAR:
            self._clear_pipeline()
            return True
        if command.kind is InteractiveCommand.VALIDATE:
            self._validate_pipeline()
            return True
        if command.kind is InteractiveCommand.RUN:
            self._run_pipeline()
            return True
        if command.kind is InteractiveCommand.RUNS:
            self._show_runs(command.text)
            return True
        if command.kind is InteractiveCommand.SCHEDULER:
            self._scheduler_command(command.text)
            return True
        if command.kind is InteractiveCommand.EXIT:
            self._write(GOODBYE)
            return False
        self._write(render_unknown(command.text))
        return True

    def _open_pipeline(self, value: str) -> None:
        if not value:
            self._write(render_current_pipeline(self.context.pipeline))
            return

        requested_path = Path(value)
        try:
            resolved_path = requested_path.resolve()
            if detect_document_kind(requested_path) is DocumentKind.DAG:
                load_dag(requested_path)
                kind_label = "DAG"
            else:
                self._application.validate_pipeline(requested_path)
                kind_label = "Pipeline"
        except ConfigurationError as error:
            self._write(f"Could not open pipeline: {error}")
            return
        except (OSError, RuntimeError):
            self._write("Could not open pipeline: the path could not be resolved.")
            return

        self.context.select(requested_path, resolved_path)
        self._write(f"{kind_label} opened: {requested_path}")

    def _clear_pipeline(self) -> None:
        if self.context.clear():
            self._write("Pipeline context cleared.")
        else:
            self._write(NO_PIPELINE)

    def _validate_pipeline(self) -> None:
        pipeline = self._require_pipeline()
        if pipeline is None:
            return
        if detect_document_kind(pipeline.resolved_path) is DocumentKind.DAG:
            self._validate_dag(pipeline)
            return
        sink = self._presenter.event_sink(PipelineOperation.VALIDATE)
        try:
            config = self._application.validate_pipeline(pipeline.resolved_path, event_sink=sink)
        except KeyboardInterrupt:
            self._write("Validation interrupted.")
        except ConfigurationError as error:
            self._presenter.show_error(error, pipeline, PipelineOperation.VALIDATE)
        else:
            self._presenter.show_validation_result(config)

    def _run_pipeline(self) -> None:
        pipeline = self._require_pipeline()
        if pipeline is None:
            return
        if detect_document_kind(pipeline.resolved_path) is DocumentKind.DAG:
            self._run_dag(pipeline)
            return
        sink = self._presenter.event_sink(PipelineOperation.RUN)
        try:
            result = self._application.run_pipeline(pipeline.resolved_path, event_sink=sink)
        except KeyboardInterrupt:
            self._write("Execution interrupted.")
        except (ConfigurationError, ExecutionError) as error:
            self._presenter.show_error(error, pipeline, PipelineOperation.RUN)
        else:
            self._presenter.show_result(result)

    def _validate_dag(self, pipeline: SelectedPipeline) -> None:
        """DAG validation has no event stream to drive the presenter's step
        timeline (`load_dag()` is a single synchronous parse+check, not a
        `PipelineApplication`-style operation) -- written directly to the
        output log instead, matching the classic CLI's own DAG `validate`
        branch (a single echoed line, no progress display there either).
        """
        try:
            dag = load_dag(pipeline.resolved_path)
        except KeyboardInterrupt:
            self._write("Validation interrupted.")
            return
        except ConfigurationError as error:
            self._write(render_operation_error(error, pipeline))
            return
        task_label = "task" if len(dag.tasks) == 1 else "tasks"
        self._write(f"DAG '{dag.name}' is valid ({len(dag.tasks)} {task_label}).")

    def _run_dag(self, pipeline: SelectedPipeline) -> None:
        """Reuses `execute_dag()` and `render_run_detail()` exactly as the
        classic CLI's `run` command does for a DAG file -- same state store,
        same recorded run, same rendering (bordered panel, severity,
        partial-success note, retry attempts, width-capped wrapping).
        """
        store = StateStore()
        try:
            try:
                run_id = execute_dag(pipeline.resolved_path, store)
            except KeyboardInterrupt:
                self._write("Execution interrupted.")
                return
            except ConfigurationError as error:
                self._write(render_operation_error(error, pipeline))
                return
            dag_run = store.get_dag_run(run_id)
            assert dag_run is not None
            tasks = store.list_task_runs(run_id)
            attempts = store.list_run_attempts(run_id)
        finally:
            store.close()
        self._write(render_run_detail(dag_run, tasks, self._output_render_context(), attempts))

    def _output_render_context(self) -> RenderContext:
        """The render context to use for anything about to go through
        `self._write()` -- `self.render_context` unchanged when the current
        output sink can actually render ANSI, or a plain-forced copy when
        it can't (NXL-100: the full-screen session's output log is a plain
        prompt_toolkit TextArea with zero ANSI interpretation, at any color
        tier -- unlike the status area/header, which render through
        prompt_toolkit's own style system and degrade color depth safely on
        their own, confirmed directly). `self.render_context` itself is
        left untouched either way -- the status area and header still use
        it directly and still get real color.
        """
        if self._output_ansi_capable:
            return self.render_context
        return replace(self.render_context, forced_plain=True)

    def _show_runs(self, value: str) -> None:
        """`/runs` (list) and `/runs <id>` (show) -- reuses `runs_render.py`'s
        rendering exactly, the same functions `nexolith runs list`/`runs show`
        call, so severity, the partial-success note, retry attempts, and
        width-capped wrapping all render identically here.
        """
        if not value:
            store = StateStore()
            try:
                runs = store.list_recent_dag_runs(20)
            finally:
                store.close()
            self._write(render_runs_list(runs, self._output_render_context()))
            return

        try:
            run_id = int(value)
        except ValueError:
            self._write(f"Usage: /runs [id] -- '{value}' is not a valid run id.")
            return

        store = StateStore()
        try:
            run = store.get_dag_run(run_id)
            if run is None:
                self._write(render_run_not_found(run_id))
                return
            tasks = store.list_task_runs(run_id)
            attempts = store.list_run_attempts(run_id)
        finally:
            store.close()
        self._write(render_run_detail(run, tasks, self._output_render_context(), attempts))

    def _scheduler_command(self, value: str) -> None:
        subcommand = value.strip()
        if not subcommand:
            self._write("Usage: /scheduler status|stop")
            return
        if subcommand == "status":
            self._scheduler_status()
            return
        if subcommand == "stop":
            self._scheduler_stop()
            return
        if subcommand == "start":
            self._write(_SCHEDULER_START_HINT)
            return
        self._write(f"Unknown scheduler command: {subcommand}. Use /scheduler status or stop.")

    def _scheduler_status(self) -> None:
        pidfile_path = default_pidfile_path()
        record = read_pidfile(pidfile_path)
        render_context = self._output_render_context()
        if record is None or not is_process_alive(record.pid):
            if record is not None:
                remove_pidfile(pidfile_path)
            self._write(render_scheduler_status(SchedulerStatus(False, None, None), render_context))
            return
        self._write(
            render_scheduler_status(
                SchedulerStatus(True, record.pid, record.started_at), render_context
            )
        )

    def _scheduler_stop(self) -> None:
        """Mirrors the classic CLI's `scheduler stop` exactly: a bounded
        ~2s poll for the target process to actually exit. That's a real,
        synchronous block on this session's single thread for up to two
        seconds -- consistent with `/validate`/`/run` already blocking
        synchronously for however long real pipeline execution takes; not a
        new category of blocking this session didn't already accept.
        """
        pidfile_path = default_pidfile_path()
        record = read_pidfile(pidfile_path)
        if record is None:
            self._write(render_scheduler_not_running())
            return
        if not is_process_alive(record.pid):
            remove_pidfile(pidfile_path)
            self._write(render_scheduler_not_running())
            return

        if sys.platform == "win32":
            self._write(render_windows_stop_caveat())
        stop_process(record.pid)

        for _ in range(20):  # ~2s budget for the process to actually exit
            if not is_process_alive(record.pid):
                break
            time.sleep(0.1)

        remove_pidfile(pidfile_path)
        if is_process_alive(record.pid):
            self._write(render_scheduler_stop_uncertain(record.pid))
        else:
            self._write(render_scheduler_stopped(record.pid))

    def _require_pipeline(self) -> SelectedPipeline | None:
        if self.context.pipeline is None:
            self._write("No pipeline is currently open. Use /open <path> first.")
        return self.context.pipeline


def render_operation_error(error: NexolithError, pipeline: SelectedPipeline) -> str:
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


def render_execution_result(result: ExecutionResult) -> str:
    lines = [
        f"Status: {result.status.value}",
        f"Rows read: {result.rows_read}",
        f"Rows written: {result.rows_written}",
    ]
    if result.duration_seconds is not None:
        lines.append(f"Duration: {result.duration_seconds:.3f}s")
    return "\n".join(lines)


def run_interactive_session() -> None:
    """Launch the default terminal-backed session: full-screen when the
    terminal supports it, today's classic line-based loop otherwise.

    `render_context.plain` is the real, deterministic, tested fallback gate
    (NO_COLOR, non-TTY, narrow width). The broad except below is a narrow
    defensive backstop only, for the rare case where prompt_toolkit itself
    cannot acquire a real terminal for full-screen mode despite `is_tty`
    being true — it only wraps session construction/startup, before any
    output has been drawn, so falling back at that point is still a clean,
    single decision rather than an accident mid-session.
    """
    render_context = detect_render_context()
    if not render_context.plain:
        from nexolith.cli.full_screen import run_full_screen_session

        try:
            run_full_screen_session(render_context)
            return
        except Exception:
            pass
    InteractiveSession(render_context=render_context).run()
