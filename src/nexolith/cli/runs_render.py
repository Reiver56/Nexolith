"""Rendering for `nexolith runs list`/`runs show`. Reuses v0.3.1's exact
established conventions -- `BLURPLE`/`WHITE`/`GREEN`/`RED`/`DIM` from
`nexo_art.py`, the same rounded-corner bordered-panel shape used for the
interactive session's own run summary -- rather than inventing a separate
visual language for these commands. Plain static ANSI strings here, not
`prompt_toolkit` `StyleAndTextTuples`: these are one-shot `typer` commands
that print and exit, not a live session.
"""

from datetime import datetime

from nexolith.cli.nexo_art import BLURPLE, DIM, GREEN, RED, WHITE
from nexolith.cli.render_context import RenderContext
from nexolith.state import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

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

# ● covers both an in-progress task and a succeeded one -- the status word
# printed alongside it (and its color, in styled mode) is what disambiguates,
# matching the "never let color alone carry meaning" rule status_area.py
# already established for its own timeline.
_TASK_MARKER = {
    TaskRunStatus.PENDING: "○",
    TaskRunStatus.RUNNING: "●",
    TaskRunStatus.SUCCEEDED: "●",
    TaskRunStatus.FAILED: "✗",
    TaskRunStatus.SKIPPED: "⊘",
}

_TASK_COLOR = {
    TaskRunStatus.PENDING: DIM,
    TaskRunStatus.RUNNING: BLURPLE,
    TaskRunStatus.SUCCEEDED: GREEN,
    TaskRunStatus.FAILED: RED,
    TaskRunStatus.SKIPPED: DIM,
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
    run: DagRunRecord, tasks: list[TaskRunRecord], render_context: RenderContext
) -> str:
    plain = render_context.plain
    status_color = None if plain else _DAG_RUN_COLOR.get(run.status)
    panel_rows: list[tuple[str, str, tuple[int, int, int] | None]] = [
        ("Status", run.status.value, status_color),
        ("DAG", run.dag_name, None),
        ("Trigger", run.trigger_reason, None),
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

    name_width = max(len(task.task_name) for task in tasks)
    status_width = max(len(task.status.value) for task in tasks)
    for task in tasks:
        duration = _format_duration(task.started_at, task.ended_at)
        marker = _TASK_MARKER[task.status]
        name = task.task_name.ljust(name_width)
        status_text = task.status.value.ljust(status_width)
        if not plain:
            color = _TASK_COLOR[task.status]
            marker = _colorize(marker, color, bold=True)
            status_text = _colorize(task.status.value, color) + " " * (
                status_width - len(task.status.value)
            )
        row = f"  {marker} {name}  {status_text}  {duration}"
        if task.error:
            row += f"  {task.error}"
        lines.append(row)

    return "\n".join(lines)


def _format_duration(started_at: str | None, ended_at: str | None) -> str:
    if started_at is None or ended_at is None:
        return "-"
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(ended_at)
    return f"{(end - start).total_seconds():.3f}s"
