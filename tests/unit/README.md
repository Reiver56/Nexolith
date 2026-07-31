# Unit tests

`test_application.py` covers dependency injection, the typed lifecycle event order, failure
events, exception propagation, and isolation from CLI presentation dependencies.
`test_interactive_cli.py` covers the Nexo splash, command loop, help, exit, empty and unknown input,
EOF, keyboard interruption, pipeline selection and replacement, contextual prompts, safe context
clearing, changed or missing files, validation, service-free execution, real result metrics, safe
failure recovery, and multi-command sessions without a real terminal. `test_interactive_events.py`
checks the complete event-to-text mapping, incremental output, forward compatibility, and renderer
failure isolation.

These tests cover configuration loading, transformations, runner state, and CLI behavior without
external services. Keep them fast, deterministic, and independent of execution order.

Error tests verify stable exception categories, chained causes, CLI exit codes, absence of
tracebacks for expected failures, and secret-safe messages. Environment diagnostic tests verify
deterministic rendering, optional-feature detection, and the explicit exclusion of environment
values and identifying local paths.

Release validation tests cover exact stable tags, synchronized package versions, dated changelog
sections, and complete wheel/source-distribution sets without contacting PyPI.

Run them with `uv run pytest tests/unit`.
