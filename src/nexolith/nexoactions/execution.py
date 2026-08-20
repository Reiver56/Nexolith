"""Side-effect-free Action preparation and post-write invocation."""

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from nexolith.config.models import NexoActionConfig
from nexolith.events import (
    ActionCompleted,
    ActionFailed,
    ActionInvocationStarted,
    ActionNotMatched,
    EventSink,
    emit_event,
)
from nexolith.exceptions import ExecutionError
from nexolith.nexoactions.contracts import (
    NexoActionCondition,
    NexoActionContext,
    NexoActionDefinition,
    NexoActionExecutionResult,
    NexoActionExecutionStatus,
    NexoActionMatchMode,
    NexoActionOperator,
    NexoActionResult,
)
from nexolith.types import Rows, Scalar


@dataclass(frozen=True, slots=True)
class _PreparedAction:
    identifier: str
    matched_rows: tuple[Mapping[str, Scalar], ...]
    definition: NexoActionDefinition | None = None
    context: NexoActionContext | None = None


@dataclass(frozen=True, slots=True)
class _PreparedActions:
    items: tuple[_PreparedAction, ...]


def prepare_actions(
    actions: list[NexoActionConfig],
    rows: Rows,
    *,
    pipeline_name: str,
) -> _PreparedActions:
    """Validate and freeze every Action without invoking handlers or emitting events."""
    return _PreparedActions(
        tuple(_prepare_action(action, rows, pipeline_name) for action in actions)
    )


def execute_prepared_actions(
    prepared: _PreparedActions,
    *,
    event_sink: EventSink | None,
    outcomes: list[NexoActionExecutionResult],
) -> None:
    """Invoke an already validated batch in declaration order."""
    for item in prepared.items:
        identifier = item.identifier
        matched = [dict(row) for row in item.matched_rows]
        if item.context is None or item.definition is None:
            outcomes.append(
                NexoActionExecutionResult(
                    identifier,
                    NexoActionExecutionStatus.NOT_MATCHED,
                    len(matched),
                )
            )
            emit_event(event_sink, ActionNotMatched(identifier, len(matched)))
            continue

        key = item.context.idempotency_key
        emit_event(event_sink, ActionInvocationStarted(identifier, len(matched), key))
        try:
            handler_result = item.definition.execute(matched, item.context)
            if not isinstance(handler_result, NexoActionResult):
                raise TypeError("handler must return NexoActionResult")
        except Exception as exc:
            outcomes.append(
                NexoActionExecutionResult(
                    identifier,
                    NexoActionExecutionStatus.FAILED,
                    len(matched),
                    key,
                )
            )
            emit_event(event_sink, ActionFailed(identifier, len(matched), key))
            raise ExecutionError(
                f"Nexo Action '{identifier}' failed with {type(exc).__name__}."
            ) from exc
        outcomes.append(
            NexoActionExecutionResult(
                identifier,
                NexoActionExecutionStatus.COMPLETED,
                len(matched),
                key,
            )
        )
        emit_event(event_sink, ActionCompleted(identifier, len(matched), key))


def execute_actions(
    actions: list[NexoActionConfig],
    rows: Rows,
    *,
    pipeline_name: str,
    event_sink: EventSink | None,
    outcomes: list[NexoActionExecutionResult],
) -> None:
    """Prepare and immediately invoke Actions outside pipeline orchestration."""
    execute_prepared_actions(
        prepare_actions(actions, rows, pipeline_name=pipeline_name),
        event_sink=event_sink,
        outcomes=outcomes,
    )


def _prepare_action(action: NexoActionConfig, rows: Rows, pipeline_name: str) -> _PreparedAction:
    condition = NexoActionCondition(
        action.condition.field,
        NexoActionOperator(action.condition.operator),
        action.condition.value,
    )
    matched = _matched_rows(rows, condition)
    should_invoke = bool(matched) and (
        action.match is NexoActionMatchMode.ANY
        or (action.match is NexoActionMatchMode.ALL and len(matched) == len(rows))
    )
    definition = action.resolved_action
    if definition is None:
        raise ExecutionError(f"Nexo Action '{action.type}' was not resolved.")
    try:
        parameters = definition.bind_parameters(action.parameters)
    except ValueError as exc:
        raise ExecutionError(f"Nexo Action '{action.type}' has invalid parameters.") from exc
    if not should_invoke:
        return _PreparedAction(action.type, _freeze_rows(matched))

    key = derive_idempotency_key(
        pipeline_name, action.type, matched, tuple(action.idempotency.fields)
    )
    context = NexoActionContext(pipeline_name, action.type, key, parameters)
    return _PreparedAction(action.type, _freeze_rows(matched), definition, context)


def _freeze_rows(rows: Rows) -> tuple[Mapping[str, Scalar], ...]:
    return tuple(MappingProxyType(dict(row)) for row in rows)


def _matched_rows(rows: Rows, condition: NexoActionCondition) -> Rows:
    matches: Rows = []
    # Evaluate the complete batch before returning. A late schema/type
    # error therefore cannot follow a partial handler invocation.
    for row in rows:
        if condition.field not in row:
            raise ExecutionError(
                f"Nexo Action condition field '{condition.field}' is missing from a row."
            )
        if _matches(row[condition.field], condition):
            matches.append(dict(row))
    return matches


def _matches(actual: Scalar, condition: NexoActionCondition) -> bool:
    expected = condition.value
    operator = condition.operator
    if operator in {NexoActionOperator.EQUALS, NexoActionOperator.NOT_EQUALS}:
        if actual is None or expected is None:
            equal = actual is expected
            return equal if operator is NexoActionOperator.EQUALS else not equal
        _require_comparable(actual, expected, condition.field, ordered=False)
        equal = actual == expected
        return equal if operator is NexoActionOperator.EQUALS else not equal

    _require_comparable(actual, expected, condition.field, ordered=True)
    if isinstance(actual, str) and isinstance(expected, str):
        return _compare_strings(actual, expected, operator)
    assert isinstance(actual, int | float) and not isinstance(actual, bool)
    assert isinstance(expected, int | float) and not isinstance(expected, bool)
    return _compare_numbers(float(actual), float(expected), operator)


def _compare_strings(actual: str, expected: str, operator: NexoActionOperator) -> bool:
    if operator is NexoActionOperator.LESS_THAN:
        return actual < expected
    if operator is NexoActionOperator.LESS_THAN_OR_EQUAL:
        return actual <= expected
    if operator is NexoActionOperator.GREATER_THAN:
        return actual > expected
    return actual >= expected


def _compare_numbers(actual: float, expected: float, operator: NexoActionOperator) -> bool:
    if operator is NexoActionOperator.LESS_THAN:
        return actual < expected
    if operator is NexoActionOperator.LESS_THAN_OR_EQUAL:
        return actual <= expected
    if operator is NexoActionOperator.GREATER_THAN:
        return actual > expected
    return actual >= expected


def _require_comparable(actual: Scalar, expected: Scalar, field: str, *, ordered: bool) -> None:
    actual_kind = _scalar_kind(actual)
    expected_kind = _scalar_kind(expected)
    compatible = actual_kind == expected_kind
    if ordered:
        compatible = compatible and actual_kind in {"number", "string"}
    if not compatible or not _finite_if_number(actual) or not _finite_if_number(expected):
        raise ExecutionError(f"Nexo Action condition field '{field}' has an incompatible type.")


def _finite_if_number(value: object) -> bool:
    return not isinstance(value, float) or math.isfinite(value)


def _scalar_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def derive_idempotency_key(
    pipeline_name: str,
    identifier: str,
    rows: Rows,
    fields: tuple[str, ...],
) -> str:
    canonical_rows: list[str] = []
    for row in rows:
        identity: dict[str, Scalar] = {}
        for field in sorted(fields):
            if field not in row:
                raise ExecutionError(
                    f"Nexo Action idempotency field '{field}' is missing from a matched row."
                )
            value = row[field]
            if _scalar_kind(value) == "unsupported":
                raise ExecutionError(
                    f"Nexo Action idempotency field '{field}' has an unsupported type."
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise ExecutionError(
                    f"Nexo Action idempotency field '{field}' contains an unstable number."
                )
            identity[field] = value
        canonical_rows.append(_canonical_json(identity))
    material: Mapping[str, object] = {
        "action": identifier,
        "pipeline": pipeline_name,
        "identities": sorted(canonical_rows),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
