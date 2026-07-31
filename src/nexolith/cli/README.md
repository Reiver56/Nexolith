# CLI

Running `nexolith` without a subcommand starts a minimal interactive session with a short Nexo
splash and the `nexolith> ` prompt. `/help` lists the available commands and `/exit` closes the
session. EOF and keyboard interruption also exit cleanly.

Use `/open <path>` to load a valid pipeline into the process-local session context. A successful
open stores both the path entered by the user and its absolute resolved identity, but not the
parsed configuration. This lets later operations reload the current file instead of using stale
configuration when its contents change. Opening another pipeline replaces the context only after
loading succeeds.

`/open` without an argument reports the current path and marks it unavailable if the file was
deleted after opening. `/clear` removes the active context. The prompt shows only the selected
filename, for example `nexolith [orders.yaml]> `, and bounds unusual or long names. Context lasts
only for the current interactive process.

Interactive validation and execution, live event output, progress rendering, persistent history,
and completion are deliberately reserved for later stories.

The Typer application also exposes `nexolith validate`, `nexolith run`, `nexolith diagnostics`,
and `nexolith --version`.
Presentation belongs here. The classic `validate` and `run` commands are thin adapters over the
shared application layer; pipeline composition, execution, and I/O belong outside the CLI.

`diagnostics` prints deterministic, issue-friendly environment information. It reports Nexolith,
Python, platform, dependency, and optional-feature versions without inspecting environment
variables or reporting usernames, hostnames, filesystem paths, or connection details.

Expected errors render as `Error [category]: message` without a traceback. Configuration failures
exit with code `2`; execution, connector, and transformation failures exit with code `3`.
Unexpected programming errors are allowed to propagate for debugging.

Add CLI tests under `tests/unit/` whenever diagnostics or exit codes change. Tests must use
recognizable sentinel secrets and generic assertions that never echo those values on failure.
