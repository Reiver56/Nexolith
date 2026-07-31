# Unit tests

These tests cover configuration loading, transformations, runner state, and CLI behavior without
external services. Keep them fast, deterministic, and independent of execution order.

Error tests verify stable exception categories, chained causes, CLI exit codes, absence of
tracebacks for expected failures, and secret-safe messages. Environment diagnostic tests verify
deterministic rendering, optional-feature detection, and the explicit exclusion of environment
values and identifying local paths.

Release validation tests cover exact stable tags, synchronized package versions, dated changelog
sections, and complete wheel/source-distribution sets without contacting PyPI.

Run them with `uv run pytest tests/unit`.
