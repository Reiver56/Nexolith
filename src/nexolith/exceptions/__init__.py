"""Domain exceptions with safe, user-facing messages."""


class NexolithError(Exception):
    """Base class for expected Nexolith failures."""


class ConfigurationError(NexolithError):
    """Raised when pipeline configuration is invalid."""


class ConnectorError(NexolithError):
    """Raised when a connector cannot read or write data."""


class TransformationError(NexolithError):
    """Raised when a transformation cannot process data."""


class ExecutionError(NexolithError):
    """Raised when an expected domain failure prevents pipeline completion."""


# Preserve the 0.1 import while registries use their specific domain category.
ComponentNotFoundError = ConfigurationError
