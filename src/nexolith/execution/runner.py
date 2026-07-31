import logging
from typing import Protocol

from nexolith.config.models import PipelineConfig
from nexolith.connectors.registry import ConnectorRegistry, default_connector_registry
from nexolith.models.execution import ExecutionResult
from nexolith.transformations.registry import (
    TransformationRegistry,
    default_transformation_registry,
)

logger = logging.getLogger(__name__)


class PipelineRunner(Protocol):
    def run(self, config: PipelineConfig) -> ExecutionResult: ...


class DefaultPipelineRunner:
    def __init__(
        self,
        connectors: ConnectorRegistry | None = None,
        transformations: TransformationRegistry | None = None,
    ) -> None:
        self.connectors = connectors or default_connector_registry()
        self.transformations = transformations or default_transformation_registry()

    def run(self, config: PipelineConfig) -> ExecutionResult:
        result = ExecutionResult(pipeline_name=config.name)
        result.start()
        logger.info("Pipeline '%s' started", config.name)
        try:
            rows = self.connectors.create_source(config.source).read()
            result.rows_read = len(rows)
            for transformation_config in config.transformations:
                transformation = self.transformations.create(transformation_config)
                rows = transformation.apply(rows)
            written = self.connectors.create_destination(config.destination).write(rows)
            result.succeed(written)
            logger.info("Pipeline '%s' succeeded", config.name)
        except Exception as exc:
            result.fail(str(exc))
            logger.error("Pipeline '%s' failed: %s", config.name, type(exc).__name__)
        return result
