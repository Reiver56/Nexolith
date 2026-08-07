import logging
import os
import sys
import time
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer

from nexolith import __version__
from nexolith.application import run_pipeline, validate_pipeline
from nexolith.cli.dag_register_render import render_dag_registration
from nexolith.cli.diagnostics import (
    collect_environment_diagnostics,
    render_environment_diagnostics,
)
from nexolith.cli.document_kind import DocumentKind, detect_document_kind
from nexolith.cli.errors import render_error
from nexolith.cli.interactive import run_interactive_session
from nexolith.cli.render_context import detect_render_context
from nexolith.cli.runs_render import render_run_detail, render_run_not_found, render_runs_list
from nexolith.cli.scheduler_render import (
    SchedulerStatus,
    render_scheduler_not_running,
    render_scheduler_started,
    render_scheduler_status,
    render_scheduler_stop_uncertain,
    render_scheduler_stopped,
    render_windows_stop_caveat,
)
from nexolith.dag import execute_dag, load_dag, register_dag
from nexolith.exceptions import ConfigurationError, ExecutionError
from nexolith.models import ExecutionResult
from nexolith.scheduler import (
    Scheduler,
    default_pidfile_path,
    is_process_alive,
    read_pidfile,
    remove_pidfile,
    stop_process,
    write_pidfile,
)
from nexolith.state import DagRunStatus, StateStore

app = typer.Typer(
    help="Build data flows that last.",
    invoke_without_command=True,
    no_args_is_help=False,
)
scheduler_app = typer.Typer(help="Manage the scheduler daemon.")
runs_app = typer.Typer(help="Observe DAG run history.")
dag_app = typer.Typer(help="Manage registered DAGs.")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(runs_app, name="runs")
app.add_typer(dag_app, name="dag")


class ExitCode(IntEnum):
    CONFIGURATION_ERROR = 2
    EXECUTION_ERROR = 3


def _show_error(error: ConfigurationError | ExecutionError) -> None:
    typer.echo(render_error(error), err=True)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"Nexolith {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    context: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Nexolith pipeline CLI."""
    if context.invoked_subcommand is None:
        run_interactive_session()


def _show_result(result: ExecutionResult) -> None:
    duration = result.duration_seconds or 0.0
    typer.echo(f"Pipeline: {result.pipeline_name}")
    typer.echo(f"Status: {result.status.value}")
    typer.echo(f"Rows read: {result.rows_read}")
    typer.echo(f"Rows written: {result.rows_written}")
    typer.echo(f"Duration: {duration:.3f}s")
    if result.error:
        typer.echo(f"Error: {result.error}", err=True)


@app.command()
def diagnostics() -> None:
    """Print secret-safe environment details for troubleshooting."""
    report = collect_environment_diagnostics()
    typer.echo(render_environment_diagnostics(report))


@app.command()
def validate(path: Annotated[Path, typer.Argument(exists=False, readable=True)]) -> None:
    """Validate a pipeline or DAG YAML file without running it."""
    if detect_document_kind(path) is DocumentKind.DAG:
        try:
            dag = load_dag(path)
        except ConfigurationError as exc:
            _show_error(exc)
            raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
        task_label = "task" if len(dag.tasks) == 1 else "tasks"
        typer.echo(f"DAG '{dag.name}' is valid ({len(dag.tasks)} {task_label}).")
        return
    try:
        config = validate_pipeline(path)
    except ConfigurationError as exc:
        _show_error(exc)
        raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
    typer.echo(f"Pipeline '{config.name}' is valid.")


@app.command()
def run(path: Annotated[Path, typer.Argument(exists=False, readable=True)]) -> None:
    """Run a pipeline or DAG YAML file."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if detect_document_kind(path) is DocumentKind.DAG:
        store = StateStore()
        try:
            try:
                run_id = execute_dag(path, store)
            except ConfigurationError as exc:
                _show_error(exc)
                raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
            dag_run = store.get_dag_run(run_id)
            if dag_run is None:
                raise RuntimeError(f"DAG run {run_id} was not recorded")
            tasks = store.list_task_runs(run_id)
            attempts = store.list_run_attempts(run_id)
        finally:
            store.close()
        typer.echo(render_run_detail(dag_run, tasks, detect_render_context(), attempts))
        if dag_run.status is DagRunStatus.FAILED:
            raise typer.Exit(code=ExitCode.EXECUTION_ERROR)
        return
    try:
        result = run_pipeline(path)
    except ConfigurationError as exc:
        _show_error(exc)
        raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
    except ExecutionError as exc:
        _show_error(exc)
        raise typer.Exit(code=ExitCode.EXECUTION_ERROR) from exc
    _show_result(result)


@scheduler_app.command("start")
def scheduler_start() -> None:
    """Run the scheduler daemon in the foreground until stopped (Ctrl+C)."""
    pidfile_path = default_pidfile_path()
    existing = read_pidfile(pidfile_path)
    if existing is not None and is_process_alive(existing.pid):
        typer.echo(
            f"Scheduler already appears to be running (PID {existing.pid}, "
            f"started {existing.started_at}).",
            err=True,
        )
        raise typer.Exit(code=1)
    if existing is not None:
        remove_pidfile(pidfile_path)  # stale marker from a prior, uncleanly-stopped run

    store = StateStore()
    scheduler = Scheduler(store)
    write_pidfile(pidfile_path, os.getpid(), datetime.now(UTC).isoformat())
    typer.echo(render_scheduler_started(os.getpid()))
    try:
        scheduler.run()
    finally:
        remove_pidfile(pidfile_path)
        store.close()


@scheduler_app.command("stop")
def scheduler_stop() -> None:
    """Stop a running scheduler daemon.

    Best-effort on Windows: this forcibly stops the process rather than
    signaling it to shut down gracefully -- there is no reliable
    cross-process graceful-stop mechanism on Windows in the standard
    library (verified directly against a real process, not assumed; see
    nexolith.scheduler.pidfile's own docstring for the investigation). An
    in-progress DAG run is not waited on. Use Ctrl+C in the scheduler's own
    terminal for a fully graceful stop.
    """
    pidfile_path = default_pidfile_path()
    record = read_pidfile(pidfile_path)
    if record is None:
        typer.echo(render_scheduler_not_running())
        return
    if not is_process_alive(record.pid):
        remove_pidfile(pidfile_path)
        typer.echo(render_scheduler_not_running())
        return

    if sys.platform == "win32":
        typer.echo(render_windows_stop_caveat(), err=True)
    stop_process(record.pid)

    for _ in range(20):  # ~2s budget for the process to actually exit
        if not is_process_alive(record.pid):
            break
        time.sleep(0.1)

    # This command owns marker cleanup rather than trusting the target
    # process's own shutdown path -- see pidfile.py's docstring for why
    # that trust wouldn't be well-founded on Windows.
    remove_pidfile(pidfile_path)
    if is_process_alive(record.pid):
        typer.echo(render_scheduler_stop_uncertain(record.pid))
    else:
        typer.echo(render_scheduler_stopped(record.pid))


@scheduler_app.command("status")
def scheduler_status() -> None:
    """Report whether the scheduler daemon appears to be running."""
    pidfile_path = default_pidfile_path()
    record = read_pidfile(pidfile_path)
    render_context = detect_render_context()
    if record is None or not is_process_alive(record.pid):
        if record is not None:
            remove_pidfile(pidfile_path)
        typer.echo(render_scheduler_status(SchedulerStatus(False, None, None), render_context))
        return
    typer.echo(
        render_scheduler_status(
            SchedulerStatus(True, record.pid, record.started_at), render_context
        )
    )


@runs_app.command("list")
def runs_list(
    dag: Annotated[str | None, typer.Option(help="Filter to one DAG's runs.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of runs to show.")] = 20,
) -> None:
    """List recent DAG runs."""
    store = StateStore()
    try:
        if dag is not None:
            runs = store.list_dag_runs(dag)[:limit]
        else:
            runs = store.list_recent_dag_runs(limit)
    finally:
        store.close()
    typer.echo(render_runs_list(runs, detect_render_context()))


@runs_app.command("show")
def runs_show(run_id: Annotated[int, typer.Argument(help="The DAG run id to show.")]) -> None:
    """Show full detail for one DAG run, including per-task status and any
    retry attempts."""
    store = StateStore()
    try:
        run = store.get_dag_run(run_id)
        if run is None:
            typer.echo(render_run_not_found(run_id), err=True)
            raise typer.Exit(code=1)
        tasks = store.list_task_runs(run_id)
        attempts = store.list_run_attempts(run_id)
    finally:
        store.close()
    typer.echo(render_run_detail(run, tasks, detect_render_context(), attempts))


@dag_app.command("register")
def dag_register(
    path: Annotated[Path, typer.Argument(exists=False, readable=True)],
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Update an already-registered DAG's schedule/source path from the file."
        ),
    ] = False,
) -> None:
    """Register a DAG for scheduled execution without running it.

    Validates the file and creates (or, with --force, updates) its `dags`
    row from the file's own `schedule:` -- no task is executed. The
    scheduler daemon picks up a DAG registered this way on its next poll
    tick exactly as if it had already been run manually once.
    """
    store = StateStore()
    try:
        try:
            result = register_dag(path, store, force=force)
        except ConfigurationError as exc:
            _show_error(exc)
            raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
    finally:
        store.close()
    typer.echo(render_dag_registration(result))
