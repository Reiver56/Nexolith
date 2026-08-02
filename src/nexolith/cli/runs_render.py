"""Rendering for `nexolith runs list`/`runs show`. Reuses v0.3.1's exact
established conventions -- `BLURPLE`/`WHITE`/`GREEN`/`RED`/`DIM` from
`nexo_art.py`, the same rounded-corner bordered-panel shape used for the
interactive session's own run summary -- rather than inventing a separate
visual language for these commands. Plain static ANSI strings here, not
`prompt_toolkit` `StyleAndTextTuples`: these are one-shot `typer` commands
that print and exit, not a live session.
"""

from collections import defaultdict
from datetime import datetime

from nexolith.cli.nexo_art import BLURPLE, DIM, GREEN, RED, WHITE
from nexolith.cli.render_context import RenderContext
from nexolith.state import (
    DagRunRecord,
    DagRunStatus,
    TaskAttemptRecord,
    TaskAttemptStatus,
    TaskRunRecord,
    TaskRunStatus,
)

_RESET = "\x1b[0m"


def _colorize(text: str, rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    red, green, blue = rgb
    prefix = "\x1b[1m" if bold else ""
    return f"{prefix}\x1b[38;2;{red};{green};{blue}m{text}{_RESET}"


_DAG_RUN_COLOR = {
    DagRunStatus.RUNNING: BLURPLE,
    DagRunStatus.SUCCEEDED: GREEN,
    DagRunStatus.FAILED: RED,
}

# ● covers both an in-progress task and a succeeded one, and ⊘ covers both
# 'skipped' and 'blocked' -- the status word printed alongside each marker
# (and its color, in styled mode) is what disambiguates, matching the
# "never let color alone carry meaning" rule status_area.py already
# established for its own timeline. skipped and blocked are visually
# identical on purpose: what distinguishes them for a reader is the word
# itself ("skipped" vs "blocked"), not a bespoke third symbol.
_TASK_MARKER = {
    TaskRunStatus.PENDING: "○",
    TaskRunStatus.RUNNING: "●",
    TaskRunStatus.SUCCEEDED: "●",
    TaskRunStatus.FAILED: "✗",
    TaskRunStatus.SKIPPED: "⊘",
    TaskRunStatus.BLOCKED: "⊘",
}

# Plain-mode fallback (NXL-80): these must never reach the terminal in an
# encoding that can't represent them -- the styled markers above are pure
# ANSI-terminal decoration, not something plain mode may ever fall back to
# partially. Bracket-words rather than single ASCII characters, matching
# the panel border's own ASCII fallback ('+'/'-'/'|') in spirit: cheap to
# read unambiguously without color. Each status gets its own distinct word
# here (unlike the shared ⊘ above) because there's no scarce-glyph
# constraint in plain text and no color to lean on for disambiguation.
_PLAIN_TASK_MARKER = {
    TaskRunStatus.PENDING: "[PENDING]",
    TaskRunStatus.RUNNING: "[RUNNING]",
    TaskRunStatus.SUCCEEDED: "[OK]",
    TaskRunStatus.FAILED: "[FAIL]",
    TaskRunStatus.SKIPPED: "[SKIP]",
    TaskRunStatus.BLOCKED: "[BLOCKED]",
}

_TASK_COLOR = {
    TaskRunStatus.PENDING: DIM,
    TaskRunStatus.RUNNING: BLURPLE,
    TaskRunStatus.SUCCEEDED: GREEN,
    TaskRunStatus.FAILED: RED,
    TaskRunStatus.SKIPPED: DIM,
    TaskRunStatus.BLOCKED: DIM,
}

_ATTEMPT_COLOR = {
    TaskAttemptStatus.RUNNING: BLURPLE,
    TaskAttemptStatus.SUCCEEDED: GREEN,
    TaskAttemptStatus.FAILED: RED,
}

_LIST_COLUMNS = ("ID", "DAG", "STATUS", "STARTED", "ENDED")


def render_runs_list(runs: list[DagRunRecord], render_context: RenderContext) -> str:
    if not runs:
        return "No DAG runs recorded yet."

    rows = [
        (str(run.id), run.dag_name, run.status.value, run.started_at, run.ended_at or "-")
        for run in runs
    ]
    widths = [
        max(len(_LIST_COLUMNS[i]), max((len(row[i]) for row in rows), default=0)) for i in range(5)
    ]

    def format_row(
        values: tuple[str, str, str, str, str], color: tuple[int, int, int] | None
    ) -> str:
        cells = []
        for index, value in enumerate(values):
            if index == 2 and color is not None and not render_context.plain:
                cells.append(
                    _colorize(value, color, bold=True) + " " * (widths[index] - len(value))
                )
            else:
                cells.append(value.ljust(widths[index]))
        return "  ".join(cells)

    lines = [format_row(_LIST_COLUMNS, None)]
    for run, row in zip(runs, rows, strict=True):
        lines.append(format_row(row, _DAG_RUN_COLOR.get(run.status)))
    return "\n".join(lines)


def render_run_not_found(run_id: int) -> str:
    return f"No DAG run found with id {run_id}."


def _panel_lines(
    rows: list[tuple[str, str, tuple[int, int, int] | None]], *, plain: bool
) -> list[str]:
    plain_lines = [f"{label}: {value}" for label, value, _color in rows]
    width = max(len(line) for line in plain_lines)

    if plain:
        border = "+" + "-" * (width + 2) + "+"
        body = [f"| {line.ljust(width)} |" for line in plain_lines]
        return [border, *body, border]

    top = _colorize("╭" + "─" * (width + 2) + "╮", BLURPLE)
    bottom = _colorize("╰" + "─" * (width + 2) + "╯", BLURPLE)
    side = _colorize("│", BLURPLE)
    body = []
    for (label, value, color), plain_line in zip(rows, plain_lines, strict=True):
        pad = " " * (width - len(plain_line))
        value_text = _colorize(value, color, bold=True) if color is not None else value
        body.append(f"{side} {label}: {value_text}{pad} {side}")
    return [top, *body, bottom]


def render_run_detail(
    run: DagRunRecord,
    tasks: list[TaskRunRecord],
    render_context: RenderContext,
    attempts: list[TaskAttemptRecord] | None = None,
) -> str:
    """`attempts` is optional (defaults to none) so existing callers that
    predate retry support keep working; a real caller passes
    `store.list_run_attempts(run.id)`. Only tasks that were actually
    retried (more than one recorded attempt) get a visible attempt
    breakdown -- a task that ran exactly once, the common case, renders
    exactly as it did before this story.
    """
    plain = render_context.plain
    status_color = None if plain else _DAG_RUN_COLOR.get(run.status)
    panel_rows: list[tuple[str, str, tuple[int, int, int] | None]] = [
        ("Status", run.status.value, status_color),
        ("DAG", run.dag_name, None),
        ("Trigger", run.trigger_reason, None),
        ("Policy", run.on_failure, None),
        ("Started", run.started_at, None),
        ("Ended", run.ended_at or "(in progress)", None),
    ]
    if run.error:
        panel_rows.append(("Error", run.error, None if plain else RED))

    lines = _panel_lines(panel_rows, plain=plain)
    lines.append("")
    lines.append("Tasks:" if plain else _colorize("Tasks:", WHITE, bold=True))

    if not tasks:
        lines.append("  (no tasks recorded)")
        return "\n".join(lines)

    attempts_by_task: dict[str, list[TaskAttemptRecord]] = defaultdict(list)
    for attempt in attempts or []:
        attempts_by_task[attempt.task_name].append(attempt)

    name_width = max(len(task.task_name) for task in tasks)
    status_width = max(len(task.status.value) for task in tasks)
    for task in tasks:
        duration = _format_duration(task.started_at, task.ended_at)
        name = task.task_name.ljust(name_width)
        status_text = task.status.value.ljust(status_width)
        if plain:
            marker = _PLAIN_TASK_MARKER[task.status]
        else:
            color = _TASK_COLOR[task.status]
            marker = _colorize(_TASK_MARKER[task.status], color, bold=True)
            status_text = _colorize(task.status.value, color) + " " * (
                status_width - len(task.status.value)
            )
        row = f"  {marker} {name}  {status_text}  {duration}"
        if task.error:
            row += f"  {task.error}"
        lines.append(row)

        task_attempts = attempts_by_task.get(task.task_name, [])
        if len(task_attempts) > 1:
            lines.extend(_render_attempt_lines(task_attempts, plain=plain))

    return "\n".join(lines)


def _render_attempt_lines(attempts: list[TaskAttemptRecord], *, plain: bool) -> list[str]:
    lines = []
    for attempt in attempts:
        duration = _format_duration(attempt.started_at, attempt.ended_at)
        status_text = attempt.status.value
        if not plain:
            status_text = _colorize(status_text, _ATTEMPT_COLOR[attempt.status])
        line = f"      attempt {attempt.attempt_number}: {status_text} ({duration})"
        if attempt.error:
            line += f" - {attempt.error}"
        lines.append(line)
    return lines


def _format_duration(started_at: str | None, ended_at: str | None) -> str:
    if started_at is None or ended_at is None:
        return "-"
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(ended_at)
    return f"{(end - start).total_seconds():.3f}s"
