"""Built-in destination Nexo Functions."""

from nexolith.nexofunctions.contracts import (
    DestinationWriteMode,
    NexoFunctionContext,
    NexoFunctionDefinition,
    NexoFunctionParameter,
    NexoFunctionParameterKind,
    NexoFunctionResult,
)
from nexolith.types import Rows


def _upsert(rows: Rows, context: NexoFunctionContext) -> NexoFunctionResult:
    conflict_keys = context.parameters["conflict_keys"]
    assert isinstance(conflict_keys, tuple)
    return NexoFunctionResult(context.destination.upsert(rows, conflict_keys))


def _truncate_write(rows: Rows, context: NexoFunctionContext) -> NexoFunctionResult:
    return NexoFunctionResult(context.destination.write(rows, DestinationWriteMode.TRUNCATE))


BUILTIN_NEXO_FUNCTIONS = (
    NexoFunctionDefinition(
        name="upsert",
        execute=_upsert,
        parameters=(
            NexoFunctionParameter(
                name="conflict_keys",
                kind=NexoFunctionParameterKind.STRING_LIST,
                minimum_items=1,
                unique_items=True,
            ),
        ),
    ),
    NexoFunctionDefinition(name="truncate_write", execute=_truncate_write),
)
