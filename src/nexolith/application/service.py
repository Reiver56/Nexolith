"""Shared use cases for pipeline validation and execution."""

from collections.abc import Callable
from pathlib import Path

from nexolith.config import PipelineConfig, load_pipeline
from nexolith.events import (
    EventSink,
    FailureCategory,
    PipelineFailed,
    PipelineLoaded,
    PipelineLoadStarted,
    PipelineOperation,
    PipelinePhase,
    emit_event,
)
from nexolith.exceptions import ConfigurationError
from nexolith.execution import DefaultPipelineRunner, PipelineRunner
from nexolith.models import ExecutionResult

PipelineLoader = Callable[[Path], PipelineConfig]


class PipelineApplication:
    """Coordinate pipeline use cases independently from presentation adapters."""

    def __init__(
        self,
        *,
        loader: PipelineLoader = load_pipeline,
        runner: PipelineRunner | None = None,
    ) -> None:
        self._loader = loader
        self._runner = runner or DefaultPipelineRunner()

    def validate_pipeline(
        self, path: Path, *, event_sink: EventSink | None = None
    ) -> PipelineConfig:
        """Load and validate a pipeline definition."""
        return self._load(path, PipelineOperation.VALIDATE, event_sink)

    def run_pipeline(self, path: Path, *, event_sink: EventSink | None = None) -> ExecutionResult:
        """Load and execute a pipeline, raising expected execution failures."""
        config = self._load(path, PipelineOperation.RUN, event_sink)
        return self._runner.run(config, raise_on_error=True, event_sink=event_sink)

    def _load(
        self,
        path: Path,
        operation: PipelineOperation,
        event_sink: EventSink | None,
    ) -> PipelineConfig:
        emit_event(event_sink, PipelineLoadStarted(operation=operation))
        try:
            config = self._loader(path)
        except ConfigurationError:
            emit_event(
                event_sink,
                PipelineFailed(
                    operation=operation,
                    pipeline_name=None,
                    phase=PipelinePhase.LOADING,
                    category=FailureCategory.CONFIGURATION,
                ),
            )
            raise
        emit_event(
            event_sink,
            PipelineLoaded(operation=operation, pipeline_name=config.name),
        )
        return config
