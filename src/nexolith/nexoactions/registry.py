"""Deterministic trusted-local Nexo Action registry."""

from nexolith.exceptions import ConfigurationError
from nexolith.nexoactions.contracts import NexoActionDefinition, action_name_from_identifier


class NexoActionRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, NexoActionDefinition] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def register(self, definition: NexoActionDefinition) -> None:
        if definition.name in self._definitions:
            raise ConfigurationError(f"Nexo Action '{definition.name}' is already registered.")
        self._definitions[definition.name] = definition

    def lookup(self, identifier: str) -> NexoActionDefinition:
        try:
            name = action_name_from_identifier(identifier)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid Nexo Action identifier: {identifier!r}.") from exc
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown Nexo Action: {identifier}.") from exc


def builtin_nexo_action_registry() -> NexoActionRegistry:
    """No cosmetic built-ins: the first slice is intentionally trusted-local."""
    return NexoActionRegistry()
