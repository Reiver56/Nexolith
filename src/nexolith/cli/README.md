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

`/validate` reloads and validates the selected pipeline. `/run` reloads and executes it through the
shared application layer. Both commands render plain-text lifecycle events as they arrive; a run
reports loading, extraction, transformation, writing, and completion phases, followed by the real
status, row counts, and duration returned by the runner. The renderer uses no synthetic percentage
or timing estimates.

Expected operation failures are reported without a traceback or sensitive connector details, and
the selected pipeline remains available for correction and retry. `Ctrl+C` during validation or a
run interrupts that synchronous operation and returns to the prompt; Nexolith starts no background
work. Persistent history, completion, progress bars, and live Rich rendering remain out of scope.
`/logs` also remains out of scope until persistent execution history exists.

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

`--version`, `diagnostics`, `validate`, and `run` are the scriptable commands (NXL-40): result
text goes to stdout, `Error [category]: message` goes to stderr, and exit codes follow the table
above. `run` also configures root logging (`INFO Pipeline '<name>' started/succeeded/failed: ...`),
which lands on stderr through the standard library's default handler, not through this CLI's own
writes. None of the four pass an `EventSink` into `PipelineApplication`, so interactive-only text
(the splash, prompts, `/validate` and `/run` phase lines) never reaches them; this must stay true
for any future interactive work. Bare `nexolith` with no subcommand starts the interactive session
instead of printing help, which is a deliberate behavior of the session itself, not one of the four
scriptable commands.

Add CLI tests under `tests/unit/` whenever diagnostics or exit codes change. Tests must use
recognizable sentinel secrets and generic assertions that never echo those values on failure.
