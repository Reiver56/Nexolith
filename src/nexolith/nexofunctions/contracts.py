"""Stable contracts for named destination Nexo Functions."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from nexolith.types import Rows, Scalar

FUNCTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
FUNCTION_IDENTIFIER_PREFIX = "nexofunction."

type NexoFunctionParameterValue = Scalar | tuple[str, ...]
type ConfiguredParameterValue = Scalar | list[str]


class NexoFunctionParameterKind(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"


class DestinationWriteMode(StrEnum):
    APPEND = "append"
    TRUNCATE = "truncate"


class DestinationCapability(Protocol):
    """Narrow destination operations available to trusted local functions."""

    def write(self, rows: Rows, mode: DestinationWriteMode) -> int: ...

    def upsert(self, rows: Rows, conflict_keys: tuple[str, ...]) -> int: ...


@dataclass(frozen=True)
class NexoFunctionContext:
    destination: DestinationCapability
    parameters: Mapping[str, NexoFunctionParameterValue]


@dataclass(frozen=True)
class NexoFunctionResult:
    """Destination outcome. Nexo Functions do not return transformed rows."""

    rows_written: int

    def __post_init__(self) -> None:
        if self.rows_written < 0:
            raise ValueError("rows_written cannot be negative")


type NexoFunctionExecutor = Callable[[Rows, NexoFunctionContext], NexoFunctionResult]


@dataclass(frozen=True)
class NexoFunctionParameter:
    name: str
    kind: NexoFunctionParameterKind
    minimum_items: int = 0
    unique_items: bool = False

    def __post_init__(self) -> None:
        validate_function_name(self.name, label="parameter name")
        if self.minimum_items < 0:
            raise ValueError("minimum_items cannot be negative")
        if self.kind is not NexoFunctionParameterKind.STRING_LIST and (
            self.minimum_items != 0 or self.unique_items
        ):
            raise ValueError("list constraints require kind=string_list")


@dataclass(frozen=True)
class NexoFunctionDefinition:
    name: str
    execute: NexoFunctionExecutor
    parameters: tuple[NexoFunctionParameter, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_function_name(self.name)
        if not callable(self.execute):
            raise ValueError("execute must be callable")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")

    @property
    def identifier(self) -> str:
        return FUNCTION_IDENTIFIER_PREFIX + self.name

    def bind_parameters(
        self, configured: Mapping[str, ConfiguredParameterValue]
    ) -> Mapping[str, NexoFunctionParameterValue]:
        declared = {parameter.name: parameter for parameter in self.parameters}
        unknown = sorted(set(configured) - set(declared))
        if unknown:
            raise ValueError("unknown parameter(s): " + ", ".join(unknown))
        missing = sorted(set(declared) - set(configured))
        if missing:
            raise ValueError("missing parameter(s): " + ", ".join(missing))

        bound: dict[str, NexoFunctionParameterValue] = {}
        for name, parameter in declared.items():
            value = configured[name]
            _validate_parameter_value(parameter, value)
            bound[name] = tuple(value) if isinstance(value, list) else value
        return MappingProxyType(bound)


def validate_function_name(name: str, *, label: str = "function name") -> str:
    if FUNCTION_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(
            f"invalid {label}; use 1-63 lowercase letters, digits, or underscores, "
            "starting with a letter"
        )
    return name


def function_name_from_identifier(identifier: str) -> str:
    if not identifier.startswith(FUNCTION_IDENTIFIER_PREFIX):
        raise ValueError("Nexo Function identifiers must start with 'nexofunction.'")
    return validate_function_name(identifier.removeprefix(FUNCTION_IDENTIFIER_PREFIX))


def _validate_parameter_value(
    parameter: NexoFunctionParameter, value: ConfiguredParameterValue
) -> None:
    kind = parameter.kind
    valid = (
        (kind is NexoFunctionParameterKind.STRING and isinstance(value, str))
        or (
            kind is NexoFunctionParameterKind.INTEGER
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or (
            kind is NexoFunctionParameterKind.NUMBER
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        )
        or (kind is NexoFunctionParameterKind.BOOLEAN and isinstance(value, bool))
        or (
            kind is NexoFunctionParameterKind.STRING_LIST
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
