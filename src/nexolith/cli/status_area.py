"""Full-screen presentation for `/validate` and `/run`: an in-place step
timeline with an event-pulsed dot while an operation is in progress, and a
bounded, highlighted YAML excerpt for `/validate` configuration errors.

A `/run`'s own outcome -- the bordered result panel, or a plain error line
-- is written to the scrollable output log instead of shown here (NXL-104:
previously a bordered summary panel replaced the timeline in this fixed
area, a different visual style and a different placement than a DAG `/run`
result, which has always gone to the log). This area returns to blank/idle
once a `/run` finishes either way, success or error, matching the log-based
placement DAG results already used. `/validate`'s own completion (the
validation panel, and its error path with a locatable YAML excerpt) is
unaffected -- explicitly out of this story's scope -- and still finishes
here.

`StatusAreaState.dot_on` toggles only inside `_TimelineEventSink.handle()`,
which only runs when `PipelineApplication` delivers a real event through the
`EventSink` protocol -- no timer, no thread, no periodic redraw independent
of that. This is a deliberate architectural constraint (see NXL-69): a true
timer-driven blink was considered and rejected specifically to avoid
reopening the synchronous-only exception already closed for `/validate` and
`/run`. The resulting pulse is irregular, paced by real event arrival, not a
clock -- that is expected, not a defect.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from prompt_toolkit.formatted_text import StyleAndTextTuples

from nexolith.cli.context import SelectedPipeline
from nexolith.cli.interactive import render_operation_error
from nexolith.cli.interactive_types import OutputWriter
from nexolith.cli.nexo_art import BLURPLE, DIM, GREEN, RED, WHITE, panel_lines
from nexolith.cli.render_context import RenderContext
from nexolith.config import PipelineConfig
from nexolith.events import (
    ApplicationEvent,
    EventSink,
    ExtractionCompleted,
    ExtractionStarted,
    PipelineFailed,
    PipelineLoaded,
    PipelineLoadStarted,
    PipelineOperation,
    TransformationsCompleted,
    TransformationsStarted,
    WriteCompleted,
    WriteStarted,
)
from nexolith.exceptions import ConfigurationError, NexolithError
from nexolith.models import ExecutionResult, ExecutionStatus

_EXCERPT_CONTEXT_LINES = 2


def _fg(rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    style = f"fg:#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return f"{style} bold" if bold else style


class StepStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


_STEPS_VALIDATE: tuple[str, ...] = ("Loading",)
_STEPS_RUN: tuple[str, ...] = ("Loading", "Extraction", "Transformation", "Writing")
_PHASE_TO_STEP = {
    "loading": "Loading",
    "extraction": "Extraction",
    "transformation": "Transformation",
    "writing": "Writing",
}


@dataclass
class StatusAreaState:
    """Mutable, event-driven state for the full-screen status area. Every
    field changes only in response to a real InteractiveSession call (a
    lifecycle event via `apply_event`, or a final result/error) -- nothing
    here is ever updated by a timer.
    """

    visible: bool = False
    steps: list[tuple[str, StepStatus]] = field(default_factory=list)
    dot_on: bool = False
    validated_config: PipelineConfig | None = None
    error_text: str | None = None
    error_excerpt: list[tuple[bool, int, str]] | None = None

    def start(self, operation: PipelineOperation) -> None:
        labels = _STEPS_VALIDATE if operation is PipelineOperation.VALIDATE else _STEPS_RUN
        self.steps = [(label, StepStatus.PENDING) for label in labels]
        self.dot_on = False
        self.validated_config = None
        self.error_text = None
        self.error_excerpt = None
        self.visible = True

    def hide(self) -> None:
        """Return this area to its blank idle state (NXL-104) -- used after
        a `/run`'s outcome has been written to the scrollable output log
        instead of shown here, unlike `/validate`'s own completion, which
        still finishes visible in this area via `finish_with_validation()`/
        `finish_with_error()`.
        """
        self.visible = False

    def _set_step(self, label: str, status: StepStatus) -> None:
        self.steps = [(lbl, status if lbl == label else st) for lbl, st in self.steps]

    def apply_event(self, event: ApplicationEvent) -> None:
        if isinstance(event, PipelineLoadStarted):
            self._set_step("Loading", StepStatus.ACTIVE)
        elif isinstance(event, PipelineLoaded):
            self._set_step("Loading", StepStatus.DONE)
        elif isinstance(event, ExtractionStarted):
            self._set_step("Extraction", StepStatus.ACTIVE)
        elif isinstance(event, ExtractionCompleted):
            self._set_step("Extraction", StepStatus.DONE)
        elif isinstance(event, TransformationsStarted):
            self._set_step("Transformation", StepStatus.ACTIVE)
        elif isinstance(event, TransformationsCompleted):
            self._set_step("Transformation", StepStatus.DONE)
        elif isinstance(event, WriteStarted):
            self._set_step("Writing", StepStatus.ACTIVE)
        elif isinstance(event, WriteCompleted):
            self._set_step("Writing", StepStatus.DONE)
        elif isinstance(event, PipelineFailed):
            label = _PHASE_TO_STEP.get(event.phase.value)
            if label is not None:
                self._set_step(label, StepStatus.FAILED)

    def finish_with_validation(self, config: PipelineConfig) -> None:
        self.validated_config = config
        self.visible = True

    def finish_with_error(self, text: str, excerpt: list[tuple[bool, int, str]] | None) -> None:
        self.error_text = text
        self.error_excerpt = excerpt
        self.visible = True

    def render(self) -> StyleAndTextTuples:
        if not self.visible:
            return [("", "")]
        if self.validated_config is not None:
            return _render_validation_panel(self.validated_config)
        if self.error_text is not None:
            return _render_error(self.error_text, self.error_excerpt)
        return _render_timeline(self.steps, self.dot_on)


def _render_timeline(steps: list[tuple[str, StepStatus]], dot_on: bool) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    for label, status in steps:
        if status is StepStatus.DONE:
            marker, marker_style, label_style = "●", _fg(BLURPLE), ""
        elif status is StepStatus.FAILED:
            marker, marker_style, label_style = "✗", _fg(RED, bold=True), _fg(RED)
        elif status is StepStatus.ACTIVE:
            marker = "●" if dot_on else "○"
            marker_style, label_style = _fg(BLURPLE), ""
        else:
            marker, marker_style, label_style = "○", _fg(DIM), _fg(DIM)
        fragments.append((marker_style, f"{marker} "))
        fragments.append((label_style, f"{label}   "))
    return fragments


def _render_error(text: str, excerpt: list[tuple[bool, int, str]] | None) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = [(_fg(RED, bold=True), text)]
    if excerpt:
        fragments.append(("", "\n"))
        for is_target, line_no, content in excerpt:
            fragments.append(
                (_fg(RED, bold=True) if is_target else "", "▸ " if is_target else "  ")
            )
            fragments.append((_fg(DIM), f"{line_no:>4} | "))
            fragments.append((_fg(RED) if is_target else _fg(BLURPLE), f"{content}\n"))
    return fragments


def _render_bordered_panel(
    rows: list[tuple[str, str]], color_for: Callable[[str, str], tuple[int, int, int]]
) -> StyleAndTextTuples:
    plain_lines = [f"{label}: {value}" for label, value in rows]
    width = max(len(line) for line in plain_lines)
    border = _fg(BLURPLE)

    fragments: StyleAndTextTuples = [(border, "╭" + "─" * (width + 2) + "╮\n")]
    for (label, value), plain in zip(rows, plain_lines, strict=True):
        pad = " " * (width - len(plain))
        fragments.append((border, "│ "))
        fragments.append(("", f"{label}: "))
        fragments.append((_fg(color_for(label, value), bold=True), value))
        fragments.append(("", pad))
        fragments.append((border, " │\n"))
    fragments.append((border, "╰" + "─" * (width + 2) + "╯"))
    return fragments


def render_execution_panel(result: ExecutionResult, render_context: RenderContext) -> str:
    """The full-screen `/run` outcome for a classic pipeline (NXL-104):
    written to the scrollable output log via `panel_lines()` -- the exact
    same bordered-panel primitive `runs_render.render_run_detail()` already
    uses for a DAG `/run`'s outcome -- rather than this module's own,
    separate `StyleAndTextTuples`-based panel the fixed status area used to
    show. Both now produce an identical rounded-border visual style in the
    same place; only the row *content* differs (a pipeline never has tasks
    to list, a DAG never has row counts).
    """
    plain = render_context.plain
    success = result.status is ExecutionStatus.SUCCEEDED
    status_color = None if plain else (GREEN if success else RED)
    rows: list[tuple[str, str, tuple[int, int, int] | None]] = [
        ("Status", result.status.value, status_color),
        ("Rows read", str(result.rows_read), _count_color(result.rows_read, success, plain)),
        (
            "Rows written",
            str(result.rows_written),
            _count_color(result.rows_written, success, plain),
        ),
    ]
    if result.duration_seconds is not None:
        rows.append(("Duration", f"{result.duration_seconds:.3f}s", None if plain else WHITE))
    return "\n".join(panel_lines(rows, render_context))


def _count_color(value: int, success: bool, plain: bool) -> tuple[int, int, int] | None:
    if plain:
        return None
    # Row counts: reserve non-blue color specifically for success/error signaling.
    if not success:
        return RED
    return DIM if value == 0 else GREEN


def _render_validation_panel(config: PipelineConfig) -> StyleAndTextTuples:
    """`/validate` never produces row counts or a duration -- `validate_pipeline()`
    only parses and checks the configuration, it never extracts, transforms, or
    writes anything. Show only what a validation actually confirms: the
    pipeline is structurally valid, and which pipeline (by its declared name,
    real data from the parsed config) was checked.
    """
    rows: list[tuple[str, str]] = [("Status", "valid"), ("Pipeline", config.name)]
    return _render_bordered_panel(rows, lambda label, _: GREEN if label == "Status" else WHITE)


class _TimelineEventSink:
    """EventSink that drives the timeline and pulse dot. `handle()` only
    runs when PipelineApplication actually emits an event -- the dot toggle
    lives here and nowhere else, so it cannot fire on a timer.
    """

    def __init__(self, state: StatusAreaState, invalidate: Callable[[], None]) -> None:
        self._state = state
        self._invalidate = invalidate

    def handle(self, event: ApplicationEvent) -> None:
        self._state.dot_on = not self._state.dot_on
        self._state.apply_event(event)
        self._invalidate()


_YAML_LINE_COLUMN_RE = re.compile(r"at line (\d+), column (\d+)")
_VALIDATION_FIELD_RE = re.compile(r"^Invalid pipeline configuration:\n([^:\n]+):", re.MULTILINE)


def _locate_error_line(error: ConfigurationError, yaml_text: str) -> int | None:
    """Best-effort, conservative: return a 1-indexed line number, or None if
    it can't be reliably determined. Never guesses -- only returns a line
    when the source is authoritative (a real YAML parser mark) or an
    unambiguous top-level key match.
    """
    message = str(error)
    match = _YAML_LINE_COLUMN_RE.search(message)
    if match:
        return int(match.group(1))

    field_match = _VALIDATION_FIELD_RE.match(message)
    if field_match:
        top_level_key = field_match.group(1).split(".")[0]
        pattern = re.compile(rf"^{re.escape(top_level_key)}\s*:", re.MULTILINE)
        for index, line in enumerate(yaml_text.splitlines()):
            if pattern.match(line):
                return index + 1
    return None


def build_error_excerpt(
    error: ConfigurationError, pipeline: SelectedPipeline
) -> list[tuple[bool, int, str]] | None:
    """A small, bounded excerpt of the pipeline YAML around the offending
    line, or None when the location can't be reliably determined (the
    caller then falls back to the plain-text-only error message). Reads the
    raw file text directly for this rendering purpose only -- never prints
    the full file, never prints a path (only line numbers and content), so
    it cannot leak anything current error handling doesn't already allow
    (env-substituted secrets never appear in the raw file text itself).
    """
    try:
        yaml_text = pipeline.resolved_path.read_text(encoding="utf-8")
    except OSError:
        return None
    line_number = _locate_error_line(error, yaml_text)
    if line_number is None:
        return None
    lines = yaml_text.splitlines()
    if not (1 <= line_number <= len(lines)):
        return None
    start = max(0, line_number - 1 - _EXCERPT_CONTEXT_LINES)
    end = min(len(lines), line_number + _EXCERPT_CONTEXT_LINES)
    return [(index == line_number - 1, index + 1, lines[index]) for index in range(start, end)]


class FullScreenOperationPresenter:
    """The full-screen `OperationPresenter`: step timeline + pulse dot while
    `/validate`/`/run` are in progress, shown in the fixed status area.

    `/validate`'s own completion (a bordered validation panel on success,
    error text plus a bounded YAML excerpt on failure) still finishes in
    this same fixed area, unaffected by NXL-104 -- explicitly out of that
    story's scope. A `/run`'s completion (success, a failed `ExecutionResult`,
    or a raised error) instead writes to `write` (the scrollable output log)
    and returns this area to blank/idle, matching where a DAG `/run`'s
    outcome has always gone.
    """

    def __init__(
        self,
        state: StatusAreaState,
        invalidate: Callable[[], None],
        *,
        write: OutputWriter,
        render_context: Callable[[], RenderContext],
    ) -> None:
        self._state = state
        self._invalidate = invalidate
        self._write = write
        # A callable, not a frozen value: `full_screen.py` re-detects the
        # real terminal width on every call so a `/run` result panel sizes
        # itself against the current width even after a real terminal
        # resize, rather than whatever was live when this presenter was
        # constructed at session start.
        self._render_context = render_context

    def event_sink(self, operation: PipelineOperation) -> EventSink:
        self._state.start(operation)
        self._invalidate()
        return _TimelineEventSink(self._state, self._invalidate)

    def show_result(self, result: ExecutionResult) -> None:
        self._write(render_execution_panel(result, self._render_context()))
        self._state.hide()
        self._invalidate()

    def show_validation_result(self, config: PipelineConfig) -> None:
        self._state.finish_with_validation(config)
        self._invalidate()

    def show_error(
        self, error: NexolithError, pipeline: SelectedPipeline, operation: PipelineOperation
    ) -> None:
        text = render_operation_error(error, pipeline)
        if operation is PipelineOperation.RUN:
            self._write(text)
            self._state.hide()
            self._invalidate()
            return
        excerpt = (
            build_error_excerpt(error, pipeline) if isinstance(error, ConfigurationError) else None
        )
        self._state.finish_with_error(text, excerpt)
        self._invalidate()


__all__ = [
    "FullScreenOperationPresenter",
    "StatusAreaState",
    "StepStatus",
    "build_error_excerpt",
    "render_execution_panel",
]
