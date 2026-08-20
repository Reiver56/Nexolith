from nexolith.nexofunctions import (
    DestinationWriteMode,
    NexoFunctionContext,
    NexoFunctionDefinition,
    NexoFunctionResult,
)
from nexolith.types import Rows


def run(rows: Rows, context: NexoFunctionContext) -> NexoFunctionResult:
    written = context.destination.write(rows, DestinationWriteMode.APPEND)
    return NexoFunctionResult(rows_written=written)


NEXO_FUNCTION = NexoFunctionDefinition(name="append_write", execute=run)
