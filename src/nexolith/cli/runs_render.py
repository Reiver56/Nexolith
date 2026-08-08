"""Rendering for `nexolith runs list`/`runs show`. Reuses v0.3.1's exact
established conventions -- `BLURPLE`/`WHITE`/`GREEN`/`RED`/`DIM` and (NXL-104)
`panel_lines()` itself, from `nexo_art.py` -- the same rounded-corner
bordered-panel primitive the full-screen session's own classic-pipeline
result panel now builds on too, rather than each keeping a separate
implementation of the same shape. Plain static ANSI strings here, not
`prompt_toolkit` `StyleAndTextTuples`: these are one-shot `typer` commands
that print and exit, not a live session.
"""

from collections import defaultdict
from datetime import datetime

from nexolith.cli.nexo_art import AMBER, BLURPLE, DIM, GREEN, RED, WHITE, colorize, panel_lines
from nexolith.cli.render_context import RenderContext
from nexolith.state import (
    DagRunRecord,
    DagRunStatus,
    TaskAttemptRecord,
    TaskAttemptStatus,
    TaskRunRecord,
    TaskRunStatus,
)

_DAG_RUN_COLOR = {
    DagRunStatus.RUNNING: BLURPLE,
    DagRunStatus.SUCCEEDED: GREEN,
    DagRunStatus.FAILED: RED,
    DagRunStatus.INTERRUPTED: AMBER,
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

# Severity colors (NXL-87): a DAG's own declared how-serious-is-a-failure
# label, rendered alongside (not instead of) the run's status -- RED above
# already means "this run failed" on the same row, so severity needs its
# own scale for "critical, failed" and "low, failed" to read differently.
# "medium" (the default) intentionally carries no color -- the unremarkable
# baseline, nothing to draw the eye to, the same "color absence means
# nothing special" convention PENDING/SKIPPED/BLOCKED task statuses already
# use. "low" reuses DIM for the same reason those do: recede, don't alarm.
_SEVERITY_COLOR: dict[str, tuple[int, int, int] | None] = {
    "critical": RED,
    "high": AMBER,
    "medium": None,
    "low": DIM,
}

_LIST_COLUMNS = ("ID", "DAG", "STATUS", "SEVERITY", "STARTED", "ENDED")


def render_runs_list(runs: list[DagRunRecord], render_context: RenderContext) -> str:
    if not runs:
        return "No DAG runs recorded yet."

    rows = [
        (
            str(run.id),
            run.dag_name,
            run.status.value,
            run.severity,
            run.started_at,
            run.ended_at or "-",
        )
        for run in runs
    ]
    widths = [
        max(len(_LIST_COLUMNS[i]), max((len(row[i]) for row in rows), default=0)) for i in range(6)
    ]

    def format_row(
        values: tuple[str, str, str, str, str, str],
        colors: dict[int, tuple[int, int, int]],
    ) -> str:
        cells = []
        for index, value in enumerate(values):
            color = colors.get(index)
            if color is not None and not render_context.plain:
                cells.append(
                    colorize(value, color, render_context, bold=True)
                    + " " * (widths[index] - len(value))
                )
            else:
                cells.append(value.ljust(widths[index]))
        return "  ".join(cells)

    lines = [format_row(_LIST_COLUMNS, {})]
    for run, row in zip(runs, rows, strict=True):
        colors: dict[int, tuple[int, int, int]] = {}
        status_color = _DAG_RUN_COLOR.get(run.status)
        if status_color is not None:
            colors[2] = status_color
        severity_color = _SEVERITY_COLOR.get(run.severity)
        if severity_color is not None:
            colors[3] = severity_color
        lines.append(format_row(row, colors))
    return "\n".join(lines)


def render_run_not_found(run_id: int) -> str:
    return f"No DAG run found with id {run_id}."


def _partial_success_note(run: DagRunRecord, tasks: list[TaskRunRecord]) -> str | None:
    """NXL-95: `on_failure: skip` (the default policy) only blocks a
    failed task's own transitive dependents -- an independent branch that
    already succeeded, or succeeds alongside the failure, keeps its real,
    persisted effects (rows written, files created) regardless of the
    DAG's own overall `failed` status. Found via FonoLink: `score_churn`
    inserted real rows into `churn_scores` while the DAG it belonged to
    was recorded as `failed` because the unrelated `detect_fraud` task
    failed. Rendering-only: this does not change `on_failure: skip`'s
    actual execution/propagation logic at all, only what `runs show`
    prints alongside a `Status: failed` a reader could otherwise
    reasonably (but wrongly) read as "nothing happened."

    Returns `None` for every other case -- a fully successful run, a
    failed run where nothing at all succeeded (every task genuinely did
    nothing), and a still-`running` run -- so the common case stays exactly
    as uncluttered as before this story.
    """
    if run.status is not DagRunStatus.FAILED:
        return None
    succeeded = sum(1 for task in tasks if task.status is TaskRunStatus.SUCCEEDED)
    if succeeded == 0:
        return None
    task_word = "task" if len(tasks) == 1 else "tasks"
    return (
        f"Partial: {succeeded} of {len(tasks)} {task_word} succeeded before this run failed -- "
        "they may have made real, persisted changes despite the overall failure."
    )


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
    severity_color = None if plain else _SEVERITY_COLOR.get(run.severity)
    panel_rows: list[tuple[str, str, tuple[int, int, int] | None]] = [
        ("Status", run.status.value, status_color),
        ("DAG", run.dag_name, None),
        ("Trigger", run.trigger_reason, None),
        ("Policy", run.on_failure, None),
        ("Severity", run.severity, severity_color),
        ("Started", run.started_at, None),
        ("Ended", run.ended_at or "(in progress)", None),
    ]
    if run.error:
        panel_rows.append(("Error", run.error, None if plain else RED))

    lines = panel_lines(panel_rows, render_context)

    partial_success_note = _partial_success_note(run, tasks)
    if partial_success_note is not None:
        lines.append("")
        lines.append(
            partial_success_note
            if plain
            else colorize(partial_success_note, AMBER, render_context, bold=True)
        )

    lines.append("")
    lines.append("Tasks:" if plain else colorize("Tasks:", WHITE, render_context, bold=True))

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
            marker = colorize(_TASK_MARKER[task.status], color, render_context, bold=True)
            status_text = colorize(task.status.value, color, render_context) + " " * (
                status_width - len(task.status.value)
            )
        row = f"  {marker} {name}  {status_text}  {duration}"
        if task.error:
            row += f"  {task.error}"
        lines.append(row)

        task_attempts = attempts_by_task.get(task.task_name, [])
        if len(task_attempts) > 1:
            lines.extend(_render_attempt_lines(task_attempts, render_context))

    return "\n".join(lines)


def _render_attempt_lines(
    attempts: list[TaskAttemptRecord], render_context: RenderContext
) -> list[str]:
    lines = []
    for attempt in attempts:
        duration = _format_duration(attempt.started_at, attempt.ended_at)
        status_text = attempt.status.value
        if not render_context.plain:
            status_text = colorize(status_text, _ATTEMPT_COLOR[attempt.status], render_context)
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
