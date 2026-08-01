"""Stable error categorization shared by CLI adapters."""

from nexolith.exceptions import (
    ConfigurationError,
    ConnectorError,
    ExecutionError,
    NexolithError,
    TransformationError,
)


def error_category(error: NexolithError) -> str:
    cause = error.__cause__ if isinstance(error, ExecutionError) else error
    if isinstance(cause, ConfigurationError):
        return "configuration"
    if isinstance(cause, ConnectorError):
        return "connector"
    if isinstance(cause, TransformationError):
        return "transformation"
    return "execution"


def render_error(error: NexolithError) -> str:
    """Render the existing non-interactive diagnostic unchanged."""
    return f"Error [{error_category(error)}]: {error}"
