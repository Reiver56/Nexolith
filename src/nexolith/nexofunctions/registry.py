"""Deterministic built-in and trusted-local Nexo Function registry."""

from nexolith.exceptions import ConfigurationError
from nexolith.nexofunctions.builtin import BUILTIN_NEXO_FUNCTIONS
from nexolith.nexofunctions.contracts import (
    NexoFunctionDefinition,
    function_name_from_identifier,
)


class NexoFunctionRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, NexoFunctionDefinition] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def register(self, definition: NexoFunctionDefinition, *, replace: bool = False) -> None:
        if definition.name in self._definitions and not replace:
            raise ConfigurationError(f"Nexo Function '{definition.name}' is already registered.")
        self._definitions[definition.name] = definition

    def lookup(self, identifier: str) -> NexoFunctionDefinition:
        try:
            name = function_name_from_identifier(identifier)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid Nexo Function identifier: {identifier!r}.") from exc
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown Nexo Function: {identifier}.") from exc


def builtin_nexo_function_registry() -> NexoFunctionRegistry:
    registry = NexoFunctionRegistry()
    for definition in BUILTIN_NEXO_FUNCTIONS:
        registry.register(definition)
    return registry
