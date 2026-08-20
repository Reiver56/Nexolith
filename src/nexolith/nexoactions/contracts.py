"""Stable contracts for declarative, trusted-local Nexo Actions."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from nexolith.types import Rows, Scalar

ACTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
ACTION_IDENTIFIER_PREFIX = "nexoaction."

type ConfiguredActionParameterValue = Scalar | list[str]
type NexoActionParameterValue = Scalar | tuple[str, ...]


class NexoActionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"


class NexoActionMatchMode(StrEnum):
    ANY = "any"
    ALL = "all"


class NexoActionExecutionStatus(StrEnum):
    NOT_MATCHED = "not_matched"
    COMPLETED = "completed"
    FAILED = "failed"


class NexoActionParameterKind(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"


@dataclass(frozen=True, slots=True)
class NexoActionCondition:
    field: str
    operator: NexoActionOperator
    value: Scalar


@dataclass(frozen=True)
class NexoActionContext:
    """Safe invocation metadata; the stable key is the deduplication boundary."""

    pipeline_name: str
    action_identifier: str
    idempotency_key: str
    parameters: Mapping[str, NexoActionParameterValue]


@dataclass(frozen=True, slots=True)
class NexoActionResult:
    """Marker returned only after a handler has completed its side effect."""


type NexoActionHandler = Callable[[Rows, NexoActionContext], NexoActionResult]


@dataclass(frozen=True)
class NexoActionParameter:
    name: str
    kind: NexoActionParameterKind
    minimum_items: int = 0
    unique_items: bool = False

    def __post_init__(self) -> None:
        validate_action_name(self.name, label="parameter name")
        if self.minimum_items < 0:
            raise ValueError("minimum_items cannot be negative")
        if self.kind is not NexoActionParameterKind.STRING_LIST and (
            self.minimum_items != 0 or self.unique_items
        ):
            raise ValueError("list constraints require kind=string_list")


@dataclass(frozen=True)
class NexoActionDefinition:
    name: str
    execute: NexoActionHandler
    parameters: tuple[NexoActionParameter, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_action_name(self.name)
        if not callable(self.execute):
            raise ValueError("execute must be callable")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")

    @property
    def identifier(self) -> str:
        return ACTION_IDENTIFIER_PREFIX + self.name

    def bind_parameters(
        self, configured: Mapping[str, ConfiguredActionParameterValue]
    ) -> Mapping[str, NexoActionParameterValue]:
        declared = {parameter.name: parameter for parameter in self.parameters}
        unknown = sorted(set(configured) - set(declared))
        if unknown:
            raise ValueError("unknown parameter(s): " + ", ".join(unknown))
        missing = sorted(set(declared) - set(configured))
        if missing:
            raise ValueError("missing parameter(s): " + ", ".join(missing))

        bound: dict[str, NexoActionParameterValue] = {}
        for name, parameter in declared.items():
            value = configured[name]
            _validate_parameter_value(parameter, value)
            bound[name] = tuple(value) if isinstance(value, list) else value
        return MappingProxyType(bound)


@dataclass(frozen=True, slots=True)
class NexoActionExecutionResult:
    identifier: str
    status: NexoActionExecutionStatus
    matched_rows: int
    idempotency_key: str | None = None


def validate_action_name(name: str, *, label: str = "action name") -> str:
    if ACTION_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(
            f"invalid {label}; use 1-63 lowercase letters, digits, or underscores, "
            "starting with a letter"
        )
    return name


def action_name_from_identifier(identifier: str) -> str:
    if not identifier.startswith(ACTION_IDENTIFIER_PREFIX):
        raise ValueError("Nexo Action identifiers must start with 'nexoaction.'")
    return validate_action_name(identifier.removeprefix(ACTION_IDENTIFIER_PREFIX))


def _validate_parameter_value(
    parameter: NexoActionParameter, value: ConfiguredActionParameterValue
) -> None:
    kind = parameter.kind
    valid = (
        (kind is NexoActionParameterKind.STRING and isinstance(value, str))
        or (
            kind is NexoActionParameterKind.INTEGER
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or (
            kind is NexoActionParameterKind.NUMBER
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        )
        or (kind is NexoActionParameterKind.BOOLEAN and isinstance(value, bool))
        or (
            kind is NexoActionParameterKind.STRING_LIST
            and isinstance(value, list)
            and all(isinstance(item, str) and item for item in value)
        )
    )
    if not valid:
        raise ValueError(f"parameter '{parameter.name}' must be {kind.value}")
    if isinstance(value, list):
        if len(value) < parameter.minimum_items:
            raise ValueError(
                f"parameter '{parameter.name}' requires at least {parameter.minimum_items} item(s)"
            )
        if parameter.unique_items and len(value) != len(set(value)):
            raise ValueError(f"parameter '{parameter.name}' cannot contain duplicates")
