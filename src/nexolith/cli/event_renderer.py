"""Linear, fail-safe rendering for application events."""

from nexolith.cli.interactive_types import OutputWriter
from nexolith.events import (
    ApplicationEvent,
    ExtractionCompleted,
    ExtractionStarted,
    PipelineExecutionCompleted,
    PipelineExecutionStarted,
    PipelineFailed,
    PipelineLoaded,
    PipelineLoadStarted,
    PipelineOperation,
    TransformationsCompleted,
    TransformationsStarted,
    WriteCompleted,
    WriteStarted,
)


def render_event(event: object) -> str | None:
    """Map every known event to plain text and ignore compatible future events."""
    if isinstance(event, PipelineLoadStarted):
        return "Loading pipeline..."
    if isinstance(event, PipelineLoaded):
        return (
            "Pipeline valid."
            if event.operation is PipelineOperation.VALIDATE
            else "Pipeline loaded."
        )
    if isinstance(event, PipelineExecutionStarted):
        return "Starting execution..."
    if isinstance(event, ExtractionStarted):
        return "Extracting..."
    if isinstance(event, ExtractionCompleted):
        return f"Extraction completed: {event.rows_read} rows read."
    if isinstance(event, TransformationsStarted):
        return "Transforming..."
    if isinstance(event, TransformationsCompleted):
        return f"Transformations completed: {event.row_count} rows ready."
    if isinstance(event, WriteStarted):
        return "Writing..."
    if isinstance(event, WriteCompleted):
        return f"Write completed: {event.rows_written} rows written."
    if isinstance(event, PipelineExecutionCompleted):
        return "Pipeline completed."
    if isinstance(event, PipelineFailed):
        return f"Pipeline failed during {event.phase.value}."
    return None


class InteractiveEventRenderer:
    """Write each event immediately without leaking renderer failures upstream."""

    def __init__(self, output_writer: "OutputWriter") -> None:
        self._write = output_writer

    def handle(self, event: ApplicationEvent) -> None:
        try:
            rendered = render_event(event)
            if rendered is not None:
                self._write(rendered)
        except Exception:
            return
