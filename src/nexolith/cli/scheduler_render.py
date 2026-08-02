"""Rendering for `nexolith scheduler status`, following v0.3.1's established
conventions (blue Discord-like palette, `RenderContext` plain-mode
fallback) rather than a new visual language for this story's commands.
"""

from dataclasses import dataclass

from nexolith.cli.nexo_art import DIM, GREEN
from nexolith.cli.render_context import RenderContext

_RESET = "\x1b[0m"


def _colorize(text: str, rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    red, green, blue = rgb
    prefix = "\x1b[1m" if bold else ""
    return f"{prefix}\x1b[38;2;{red};{green};{blue}m{text}{_RESET}"


@dataclass(frozen=True, slots=True)
class SchedulerStatus:
    running: bool
    pid: int | None
    started_at: str | None


def render_scheduler_status(status: SchedulerStatus, render_context: RenderContext) -> str:
    if status.running:
        label = "running" if render_context.plain else _colorize("running", GREEN, bold=True)
        lines = [f"Scheduler: {label}"]
        if status.pid is not None:
            lines.append(f"PID: {status.pid}")
        if status.started_at is not None:
            lines.append(f"Started: {status.started_at}")
        return "\n".join(lines)

    label = "not running" if render_context.plain else _colorize("not running", DIM)
    return f"Scheduler: {label}"


def render_scheduler_started(pid: int) -> str:
    return f"Scheduler started (PID {pid}). Press Ctrl+C to stop."


def render_scheduler_not_running() -> str:
    return "Scheduler is not running."


def render_scheduler_stopped(pid: int) -> str:
    return f"Scheduler stopped (PID {pid})."


def render_scheduler_stop_uncertain(pid: int) -> str:
    return f"Stop signal sent to PID {pid}, but it may still be shutting down."


_WINDOWS_STOP_CAVEAT = (
    "Note: on Windows this forcibly stops the process -- it does not wait "
    "for an in-progress DAG run to finish, unlike Ctrl+C in the scheduler's "
    "own terminal."
)


def render_windows_stop_caveat() -> str:
    return _WINDOWS_STOP_CAVEAT
