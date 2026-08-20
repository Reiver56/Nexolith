import logging
from typing import Protocol

from nexolith.config.models import NexoFunctionDestinationConfig, PipelineConfig
from nexolith.connectors.registry import ConnectorRegistry, default_connector_registry
from nexolith.events import (
    EventSink,
    ExtractionCompleted,
    ExtractionStarted,
    FailureCategory,
    PipelineExecutionCompleted,
    PipelineExecutionStarted,
    PipelineFailed,
    PipelineOperation,
    PipelinePhase,
    TransformationsCompleted,
    TransformationsStarted,
    WriteCompleted,
    WriteStarted,
    emit_event,
)
from nexolith.exceptions import (
    ConnectorError,
    ExecutionError,
    NexolithError,
    TransformationError,
)
from nexolith.models.execution import ExecutionResult
from nexolith.nexoactions.execution import execute_prepared_actions, prepare_actions
from nexolith.nexofunctions.destination import execute_destination_function
from nexolith.transformations.registry import (
    TransformationRegistry,
    default_transformation_registry,
)

logger = logging.getLogger(__name__)


class PipelineRunner(Protocol):
    def run(
        self,
        config: PipelineConfig,
        *,
        raise_on_error: bool = False,
        event_sink: EventSink | None = None,
    ) -> ExecutionResult: ...


class DefaultPipelineRunner:
    def __init__(
        self,
        connectors: ConnectorRegistry | None = None,
        transformations: TransformationRegistry | None = None,
    ) -> None:
        self.connectors = connectors or default_connector_registry()
        self.transformations = transformations or default_transformation_registry()

    def run(
        self,
        config: PipelineConfig,
        *,
        raise_on_error: bool = False,
        event_sink: EventSink | None = None,
    ) -> ExecutionResult:
        result = ExecutionResult(pipeline_name=config.name)
        result.start()
        logger.info("Pipeline '%s' started", config.name)
        emit_event(event_sink, PipelineExecutionStarted(pipeline_name=config.name))
        phase = PipelinePhase.EXTRACTION
        try:
            emit_event(
                event_sink,
                ExtractionStarted(
                    pipeline_name=config.name,
                    source_type=config.source.type,
                ),
            )
            rows = self.connectors.create_source(config.source).read()
            result.rows_read = len(rows)
            emit_event(
                event_sink,
                ExtractionCompleted(
                    pipeline_name=config.name,
                    rows_read=result.rows_read,
                ),
            )
            phase = PipelinePhase.TRANSFORMATION
            emit_event(
                event_sink,
                TransformationsStarted(
                    pipeline_name=config.name,
                    transformation_count=len(config.transformations),
                ),
            )
            for transformation_config in config.transformations:
                transformation = self.transformations.create(transformation_config)
                rows = transformation.apply(rows)
            emit_event(
                event_sink,
                TransformationsCompleted(
                    pipeline_name=config.name,
                    row_count=len(rows),
                ),
            )
            phase = PipelinePhase.ACTIONS
            prepared_actions = prepare_actions(
                config.actions,
                rows,
                pipeline_name=config.name,
            )
            phase = PipelinePhase.WRITING
            emit_event(
                event_sink,
                WriteStarted(
                    pipeline_name=config.name,
                    destination_type=config.destination.type,
                    row_count=len(rows),
                ),
            )
            if isinstance(config.destination, NexoFunctionDestinationConfig):
                written = execute_destination_function(config.destination, rows)
            else:
                written = self.connectors.create_destination(config.destination).write(rows)
            emit_event(
                event_sink,
                WriteCompleted(
                    pipeline_name=config.name,
                    rows_written=written,
                ),
            )
            result.rows_written = written
            phase = PipelinePhase.ACTIONS
            execute_prepared_actions(
                prepared_actions,
                event_sink=event_sink,
                outcomes=result.actions,
            )
            result.succeed(written)
            logger.info("Pipeline '%s' succeeded", config.name)
            emit_event(
                event_sink,
                PipelineExecutionCompleted(
                    pipeline_name=config.name,
                    rows_read=result.rows_read,
                    rows_written=result.rows_written,
                ),
            )
        except NexolithError as exc:
            execution_error = ExecutionError(f"Pipeline '{config.name}' failed: {exc}")
            result.fail(str(execution_error))
            logger.error("Pipeline '%s' failed: %s", config.name, type(exc).__name__)
            emit_event(
                event_sink,
                PipelineFailed(
                    operation=PipelineOperation.RUN,
                    pipeline_name=config.name,
                    phase=phase,
                    category=_failure_category(exc),
                ),
            )
            if raise_on_error:
                raise execution_error from exc
        except Exception:
            emit_event(
                event_sink,
                PipelineFailed(
                    operation=PipelineOperation.RUN,
                    pipeline_name=config.name,
                    phase=phase,
                    category=FailureCategory.UNEXPECTED,
                ),
            )
            raise
        return result


def _failure_category(error: NexolithError) -> FailureCategory:
    if isinstance(error, ConnectorError):
        return FailureCategory.CONNECTOR
    if isinstance(error, TransformationError):
        return FailureCategory.TRANSFORMATION
    return FailureCategory.EXECUTION
