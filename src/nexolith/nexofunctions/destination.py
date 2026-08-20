"""Destination adapter and execution boundary for Nexo Functions."""

from dataclasses import dataclass

from nexolith.config.models import NexoFunctionDestinationConfig
from nexolith.connectors.sql import SqlDestination
from nexolith.exceptions import ConfigurationError, ExecutionError, NexolithError
from nexolith.nexofunctions.contracts import (
    DestinationWriteMode,
    NexoFunctionContext,
    NexoFunctionResult,
)
from nexolith.types import Rows


@dataclass(frozen=True)
class SqlDestinationCapability:
    connection_url: str
    table: str

    def write(self, rows: Rows, mode: DestinationWriteMode) -> int:
        return SqlDestination(self.connection_url, self.table, mode.value).write(rows)

    def upsert(self, rows: Rows, conflict_keys: tuple[str, ...]) -> int:
        return SqlDestination(self.connection_url, self.table, "append").upsert(rows, conflict_keys)


def execute_destination_function(config: NexoFunctionDestinationConfig, rows: Rows) -> int:
    definition = config.resolved_function
    if definition is None:
        raise ConfigurationError("Nexo Function destination was not resolved during loading.")
    try:
        parameters = definition.bind_parameters(config.parameters)
        context = NexoFunctionContext(
            destination=SqlDestinationCapability(
                connection_url=config.target.connection_url,
                table=config.target.table,
            ),
            parameters=parameters,
        )
        result = definition.execute(rows, context)
    except NexolithError:
        raise
    except Exception as exc:
        raise ExecutionError(
            f"Nexo Function '{definition.identifier}' failed with {type(exc).__name__}."
        ) from exc
    if not isinstance(result, NexoFunctionResult):
        error = TypeError("Nexo Function returned an invalid result")
        raise ExecutionError(
            f"Nexo Function '{definition.identifier}' must return NexoFunctionResult."
        ) from error
    return result.rows_written
