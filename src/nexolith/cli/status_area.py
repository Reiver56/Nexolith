"""Full-screen presentation for `/validate` and `/run`: an in-place step
timeline with an event-pulsed dot, a bordered summary panel on completion,
and a bounded, highlighted YAML excerpt for `/validate` configuration errors.

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
from nexolith.cli.nexo_art import BLURPLE, DIM, GREEN, RED, WHITE
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
    result: ExecutionResult | None = None
    validated_config: PipelineConfig | None = None
    error_text: str | None = None
    error_excerpt: list[tuple[bool, int, str]] | None = None

    def start(self, operation: PipelineOperation) -> None:
        labels = _STEPS_VALIDATE if operation is PipelineOperation.VALIDATE else _STEPS_RUN
        self.steps = [(label, StepStatus.PENDING) for label in labels]
        self.dot_on = False
        self.result = None
        self.validated_config = None
        self.error_text = None
        self.error_excerpt = None
        self.visible = True

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

    def finish_with_result(self, result: ExecutionResult) -> None:
        self.result = result
        self.visible = True

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
        if self.result is not None:
            return _render_summary_panel(self.result)
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


def _render_summary_panel(result: ExecutionResult) -> StyleAndTextTuples:
    success = result.status is ExecutionStatus.SUCCEEDED
    rows: list[tuple[str, str]] = [
        ("Status", result.status.value),
        ("Rows read", str(result.rows_read)),
        ("Rows written", str(result.rows_written)),
    ]
    if result.duration_seconds is not None:
        rows.append(("Duration", f"{result.duration_seconds:.3f}s"))
    return _render_bordered_panel(rows, lambda label, value: _value_color(label, value, success))


def _value_color(label: str, value: str, success: bool) -> tuple[int, int, int]:
    if label == "Status":
        return GREEN if success else RED
    if label == "Duration":
        return WHITE
    # Row counts: reserve non-blue color specifically for success/error signaling.
    if not success:
        return RED
    return DIM if value == "0" else GREEN


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
    `/validate`/`/run` are in progress, a bordered summary panel on success,
    the existing safe error text (plus a bounded YAML excerpt for
    `/validate` configuration errors, when locatable) on failure.
    """

    def __init__(self, state: StatusAreaState, invalidate: Callable[[], None]) -> None:
        self._state = state
        self._invalidate = invalidate

    def event_sink(self, operation: PipelineOperation) -> EventSink:
        self._state.start(operation)
        self._invalidate()
        return _TimelineEventSink(self._state, self._invalidate)

    def show_result(self, result: ExecutionResult) -> None:
        self._state.finish_with_result(result)
        self._invalidate()

    def show_validation_result(self, config: PipelineConfig) -> None:
        self._state.finish_with_validation(config)
        self._invalidate()

    def show_error(
        self, error: NexolithError, pipeline: SelectedPipeline, operation: PipelineOperation
    ) -> None:
        text = render_operation_error(error, pipeline)
        excerpt = None
        if operation is PipelineOperation.VALIDATE and isinstance(error, ConfigurationError):
            excerpt = build_error_excerpt(error, pipeline)
        self._state.finish_with_error(text, excerpt)
        self._invalidate()


__all__ = [
    "FullScreenOperationPresenter",
    "StatusAreaState",
    "StepStatus",
    "build_error_excerpt",
]
