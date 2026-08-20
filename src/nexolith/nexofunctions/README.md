# Nexo Functions

Nexo Functions are named destination operations. `nexofunction.<name>` resolves through a typed
registry populated first with built-ins, then with trusted project-local definitions discovered
from `nexofunctions/*.py` beside the pipeline YAML. Local definitions intentionally replace a
built-in with the same name; duplicate local names fail deterministically.

Each local module exports exactly one `NEXO_FUNCTION: NexoFunctionDefinition`. Its callable receives
the pipeline's in-flight `Rows` and an immutable `NexoFunctionContext`. The context exposes declared,
validated parameters and a narrow destination capability, not runner or SQLAlchemy internals. It
returns `NexoFunctionResult(rows_written=...)`; functions never return rows and are not
transformations.

Discovery imports local modules during configuration loading. This executes their top-level Python
code. Local functions are trusted code with no sandbox. Import and execution failures retain their
causes, while expected public diagnostics contain only file/function names and exception types.

Built-ins:

- `nexofunction.upsert`: atomic SQLite/PostgreSQL insert-or-update against an existing table whose
  primary key or unique constraint exactly matches `parameters.conflict_keys`.
- `nexofunction.truncate_write`: delegates to `SqlDestination`'s existing `truncate` mode, preserving
  its schema/constraint and transaction behavior.

Package entry points, third-party plugins, source/hook/validation functions, Nexo Actions, and
sandboxing are outside this package's current scope.
