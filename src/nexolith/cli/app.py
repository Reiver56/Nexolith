import logging
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer

from nexolith import __version__
from nexolith.application import run_pipeline, validate_pipeline
from nexolith.cli.diagnostics import (
    collect_environment_diagnostics,
    render_environment_diagnostics,
)
from nexolith.cli.interactive import run_interactive_session
from nexolith.exceptions import (
    ConfigurationError,
    ConnectorError,
    ExecutionError,
    NexolithError,
    TransformationError,
)
from nexolith.models import ExecutionResult

app = typer.Typer(
    help="Build data flows that last.",
    invoke_without_command=True,
    no_args_is_help=False,
)


class ExitCode(IntEnum):
    CONFIGURATION_ERROR = 2
    EXECUTION_ERROR = 3


def _error_category(error: NexolithError) -> str:
    cause = error.__cause__ if isinstance(error, ExecutionError) else error
    if isinstance(cause, ConfigurationError):
        return "configuration"
    if isinstance(cause, ConnectorError):
        return "connector"
    if isinstance(cause, TransformationError):
        return "transformation"
    return "execution"


def _show_error(error: NexolithError) -> None:
    typer.echo(f"Error [{_error_category(error)}]: {error}", err=True)


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
    """Validate a pipeline YAML file without running it."""
    try:
        config = validate_pipeline(path)
    except ConfigurationError as exc:
        _show_error(exc)
        raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
    typer.echo(f"Pipeline '{config.name}' is valid.")


@app.command()
def run(path: Annotated[Path, typer.Argument(exists=False, readable=True)]) -> None:
    """Run a pipeline YAML file."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = run_pipeline(path)
    except ConfigurationError as exc:
        _show_error(exc)
        raise typer.Exit(code=ExitCode.CONFIGURATION_ERROR) from exc
    except ExecutionError as exc:
        _show_error(exc)
        raise typer.Exit(code=ExitCode.EXECUTION_ERROR) from exc
    _show_result(result)
