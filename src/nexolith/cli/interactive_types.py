"""Small shared typing boundary for interactive CLI adapters."""

from collections.abc import Callable

InputReader = Callable[[str], str]
OutputWriter = Callable[[str], None]
