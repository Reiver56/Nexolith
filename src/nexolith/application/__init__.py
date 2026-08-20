"""Presentation-independent pipeline use cases and lifecycle events."""

from pathlib import Path

from nexolith.application.service import PipelineApplication, PipelineLoader
from nexolith.config import PipelineConfig
from nexolith.events import (
    ActionCompleted,
    ActionFailed,
    ActionInvocationStarted,
    ActionNotMatched,
    ApplicationEvent,
    EventSink,
    ExtractionCompleted,
    ExtractionStarted,
    FailureCategory,
    PipelineExecutionCompleted,
    PipelineExecutionStarted,
    PipelineFailed,
    PipelineLoaded,
    PipelineLoadStarted,
    PipelineOperation,
    PipelinePhase,
    TransformationsCompleted,
    TransformationsStarted,
    WriteCompleted,
    WriteStarted,
)
from nexolith.models import ExecutionResult

_default_application = PipelineApplication()


def validate_pipeline(path: Path, *, event_sink: EventSink | None = None) -> PipelineConfig:
    """Validate a pipeline through the default application service."""
    return _default_application.validate_pipeline(path, event_sink=event_sink)


def run_pipeline(path: Path, *, event_sink: EventSink | None = None) -> ExecutionResult:
    """Run a pipeline through the default application service."""
    return _default_application.run_pipeline(path, event_sink=event_sink)


__all__ = [
    "ActionCompleted",
    "ActionFailed",
    "ActionInvocationStarted",
    "ActionNotMatched",
    "ApplicationEvent",
    "EventSink",
    "ExecutionResult",
    "ExtractionCompleted",
    "ExtractionStarted",
    "FailureCategory",
    "PipelineApplication",
    "PipelineExecutionCompleted",
    "PipelineExecutionStarted",
    "PipelineFailed",
    "PipelineLoadStarted",
    "PipelineLoaded",
    "PipelineLoader",
    "PipelineOperation",
    "PipelinePhase",
    "TransformationsCompleted",
    "TransformationsStarted",
    "WriteCompleted",
    "WriteStarted",
    "run_pipeline",
    "validate_pipeline",
]
