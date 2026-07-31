import logging
from pathlib import Path
from typing import Annotated

import typer

from nexolith import __version__
from nexolith.config import load_pipeline
from nexolith.exceptions import NexolithError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionResult, ExecutionStatus

app = typer.Typer(help="Build data flows that last.", no_args_is_help=True)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"Nexolith {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Nexolith pipeline CLI."""


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
def validate(path: Annotated[Path, typer.Argument(exists=False, readable=True)]) -> None:
    """Validate a pipeline YAML file without running it."""
    try:
        config = load_pipeline(path)
    except NexolithError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Pipeline '{config.name}' is valid.")


@app.command()
def run(path: Annotated[Path, typer.Argument(exists=False, readable=True)]) -> None:
    """Run a pipeline YAML file."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load_pipeline(path)
    except NexolithError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    result = DefaultPipelineRunner().run(config)
    _show_result(result)
    if result.status is ExecutionStatus.FAILED:
        raise typer.Exit(code=1)
