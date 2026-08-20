"""Typed, presentation-agnostic application lifecycle events."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PipelineOperation(StrEnum):
    """Public application operations that can produce events."""

    VALIDATE = "validate"
    RUN = "run"


class PipelinePhase(StrEnum):
    """Observable phases of a pipeline operation."""

    LOADING = "loading"
    EXTRACTION = "extraction"
    TRANSFORMATION = "transformation"
    WRITING = "writing"
    ACTIONS = "actions"


class FailureCategory(StrEnum):
    """Stable failure categories without exposing exception instances."""

    CONFIGURATION = "configuration"
    CONNECTOR = "connector"
    TRANSFORMATION = "transformation"
    EXECUTION = "execution"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class PipelineLoadStarted:
    operation: PipelineOperation


@dataclass(frozen=True, slots=True)
class PipelineLoaded:
    operation: PipelineOperation
    pipeline_name: str


@dataclass(frozen=True, slots=True)
class PipelineExecutionStarted:
    pipeline_name: str


@dataclass(frozen=True, slots=True)
class ExtractionStarted:
    pipeline_name: str
    source_type: str


@dataclass(frozen=True, slots=True)
class ExtractionCompleted:
    pipeline_name: str
    rows_read: int


@dataclass(frozen=True, slots=True)
class TransformationsStarted:
    pipeline_name: str
    transformation_count: int


@dataclass(frozen=True, slots=True)
class TransformationsCompleted:
    pipeline_name: str
    row_count: int


@dataclass(frozen=True, slots=True)
class WriteStarted:
    pipeline_name: str
    destination_type: str
    row_count: int


@dataclass(frozen=True, slots=True)
class WriteCompleted:
    pipeline_name: str
    rows_written: int


@dataclass(frozen=True, slots=True)
class ActionNotMatched:
    action_identifier: str
    matched_rows: int


@dataclass(frozen=True, slots=True)
class ActionInvocationStarted:
    action_identifier: str
    matched_rows: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ActionCompleted:
    action_identifier: str
    matched_rows: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ActionFailed:
    action_identifier: str
    matched_rows: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PipelineExecutionCompleted:
    pipeline_name: str
    rows_read: int
    rows_written: int


@dataclass(frozen=True, slots=True)
class PipelineFailed:
    operation: PipelineOperation
    pipeline_name: str | None
    phase: PipelinePhase
    category: FailureCategory


type ApplicationEvent = (
    PipelineLoadStarted
    | PipelineLoaded
    | PipelineExecutionStarted
    | ExtractionStarted
    | ExtractionCompleted
    | TransformationsStarted
    | TransformationsCompleted
    | WriteStarted
    | WriteCompleted
    | PipelineExecutionCompleted
    | ActionNotMatched
    | ActionInvocationStarted
    | ActionCompleted
    | ActionFailed
    | PipelineFailed
)


class EventSink(Protocol):
    """Receive application events synchronously in production order."""

    def handle(self, event: ApplicationEvent) -> None: ...


def emit_event(sink: EventSink | None, event: ApplicationEvent) -> None:
    """Deliver an event when a sink was supplied."""
    if sink is not None:
        sink.handle(event)
