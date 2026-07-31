# CLI

The Typer application exposes `nexolith validate`, `nexolith run`, and `nexolith --version`.
Presentation belongs here; pipeline execution and I/O belong in their dedicated packages.

Expected errors render as `Error [category]: message` without a traceback. Configuration failures
exit with code `2`; execution, connector, and transformation failures exit with code `3`.
Unexpected programming errors are allowed to propagate for debugging.

Add CLI tests under `tests/unit/` whenever diagnostics or exit codes change. Tests must use
recognizable sentinel secrets and generic assertions that never echo those values on failure.
