# Unit tests

These tests cover configuration loading, transformations, runner state, and CLI behavior without
external services. Keep them fast, deterministic, and independent of execution order.

Error tests verify stable exception categories, chained causes, CLI exit codes, absence of
tracebacks for expected failures, and secret-safe messages.

Run them with `uv run pytest tests/unit`.
