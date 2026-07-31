"""Domain exceptions with safe, user-facing messages."""


class NexolithError(Exception):
    """Base class for expected Nexolith failures."""


class ConfigurationError(NexolithError):
    """Raised when pipeline configuration is invalid."""


class ComponentNotFoundError(ConfigurationError):
    """Raised when a configured component type is not registered."""


class ConnectorError(NexolithError):
    """Raised when a connector cannot read or write data."""


class TransformationError(NexolithError):
    """Raised when a transformation cannot process data."""
