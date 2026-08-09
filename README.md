<p align="center">
  <img src="https://raw.githubusercontent.com/nexolith-labs/Nexolith/v0.2.0/assets/nexo-icon.png" alt="Nexo, the Nexolith mascot" width="160">
</p>

# Nexolith

> **Build data flows that last.**

Nexolith is a lightweight, modular Python framework for defining, validating, running,
and monitoring data pipelines between files and SQL databases.

[![CI](https://github.com/nexolith-labs/Nexolith/actions/workflows/ci.yml/badge.svg)](https://github.com/nexolith-labs/Nexolith/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12--3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)

> [!IMPORTANT]
> Nexolith is an early-stage project. Its configuration format may evolve before 1.0.

See the [changelog](CHANGELOG.md) for user-facing release notes, compatibility changes, and
migration guidance.

Maintainers publish tagged packages through the documented [Trusted Publishing release
process](RELEASING.md).

## Tech Stack

[![Python](https://img.shields.io/badge/Python-3.12--3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Typer](https://img.shields.io/badge/Typer-CLI-009688)](https://typer.tiangolo.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API-009688)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-8-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-supported-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![pytest](https://img.shields.io/badge/pytest-tested-0A9EDC?logo=pytest&logoColor=white)](https://pytest.org/)
[![Ruff](https://img.shields.io/badge/Ruff-lint%20%26%20format-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-CI-2088FF?logo=githubactions&logoColor=white)](https://github.com/features/actions)

## Features

- Declarative YAML pipelines with Pydantic validation
- CSV, SQLite, and PostgreSQL sources and destinations, including parameterized and
  file-backed (`query_file`) SQL queries
- Safe select, rename, drop-null, and declarative filter transformations, plus in-process
  Python (`python_job`) and subprocess script job steps for logic the declarative set can't
  express (see [Transformations](#transformations))
- DAG orchestration across multiple pipelines: dependencies, retries, failure-propagation
  policy, cross-DAG triggers, priority, and severity, with a scheduler daemon and persisted
  run history (see [CLI](#cli))
- An interactive CLI session — full-screen with tab-completion on a capable terminal, a
  plain-text line loop otherwise — alongside scriptable one-shot commands
- Environment variable substitution using `${VARIABLE_NAME}`
- Execution status, timing, row counts, and safe error reporting
- Small registries for adding connectors and transformations
- A Typer CLI with non-zero exit codes on failure
- An optional, versioned API for DAG/run monitoring and explicit DAG/scheduler actions
- A polished, responsive React monitoring UI for DAGs, interactive dependency graphs, recent
  runs, run/task/attempt history, DAG actions, and scheduler control

## Architecture

Configuration loading is separate from I/O and processing. A pipeline runner asks the
connector registry for its source and destination, and the transformation registry builds
each ordered transformation. Rows use the intentionally small `list[dict]` representation.
Execution results are plain models that can later be persisted without coupling persistence
to the runner.

A DAG layer sits above single pipelines: declarative dependency graphs (`nexolith.dag`) are
executed in order (`nexolith.dag`'s executor), with run/task state persisted to a local
SQLite store (`nexolith.state`) that a polling scheduler daemon (`nexolith.scheduler`) reads
to trigger due or cross-DAG-triggered DAGs. See [src/nexolith/cli/README.md](src/nexolith/cli/README.md)
for how the CLI presentation layer is organized.

The optional HTTP adapter (`nexolith.api`) composes explicit models over existing query and shared
action layers. It opens one state store per DAG request and does not make the domain, executor,
scheduler, or state packages depend on FastAPI.

## Requirements and installation

- Python 3.12, 3.13, or 3.14
- Node.js 24 LTS and npm 11 when developing the optional React UI

Install Nexolith from PyPI:

```bash
pip install nexolith
nexolith --version
```

For development from a source checkout, install [uv](https://docs.astral.sh/uv/) and run:

```bash
uv sync --extra dev
uv run nexolith --version
```

Install PostgreSQL support when needed:

```bash
uv sync --extra dev --extra postgres
```

Install the v0.3.4 query and action API when needed:

```bash
pip install "nexolith[api]"
# Source checkout:
uv sync --extra api --extra dev
```

## Quick start

New to Nexolith? Follow the **[zero-to-first-pipeline tutorial](FIRST_PIPELINE.md)** to install
the project, understand the example, validate and run it, and inspect the resulting CSV in under
ten minutes.

The short version, run from the repository root, is:

```bash
uv run nexolith validate examples/pipelines/completed_orders.yaml
uv run nexolith run examples/pipelines/completed_orders.yaml
```

The pipeline reads five orders, keeps the three completed ones, selects and renames columns, and
writes `build/completed_orders.csv`:

```yaml
name: completed_orders
source:
  type: csv
  path: examples/data/orders.csv
transformations:
  - type: filter
    column: status
    operator: equals
    value: completed
  - type: select
    columns: [id, customer_id, total]
  - type: rename
    columns:
      total: order_total
destination:
  type: csv
  path: build/completed_orders.csv
```

See the [tutorial](FIRST_PIPELINE.md) for the expected command output, a portable output check,
troubleshooting, and suggested next steps.

## CLI

```text
nexolith
nexolith --version
nexolith diagnostics
nexolith validate path/to/pipeline.yaml
nexolith run path/to/pipeline.yaml
nexolith api start
```

Running `nexolith` without a subcommand opens the Nexo interactive session — full-screen, with a
colored pixel-art panel header and tab-completion, on a capable terminal; a plain-text line loop
otherwise (`NO_COLOR`, no real TTY, or a narrow terminal). Use `/help` to list its currently
available commands, `/open <path>` to select or replace a valid pipeline or DAG file (path
completion included in full-screen mode; DAG files are recognized the same way `nexolith
validate`/`run` recognize them), `/validate` and `/run` to operate on whichever is currently open,
`/open` to show the current selection, `/clear` to remove it, and `/exit` to leave. `/runs` lists
recent DAG runs and `/runs <id>` shows one in full detail — the same rendering `nexolith runs
list`/`runs show` produce. `/scheduler status` reports whether the scheduler daemon is running and
`/scheduler stop` stops one; starting the daemon (`/scheduler start`) is deliberately not offered
from inside the session, since it runs as its own long-lived foreground process and would block
the session's event loop — start it from a separate terminal with `nexolith scheduler start`
instead. The selected filename appears in the prompt and the context lasts only for the current
process. Validation and execution report observable pipeline phases as plain text; successful runs
finish with the real status, row counts, and duration. Expected failures keep the session usable,
and `Ctrl+C` returns to the prompt after interrupting the current synchronous operation.

`diagnostics` prints shareable Nexolith, Python, platform, dependency, and optional-feature
information without exposing environment variables, usernames, hostnames, filesystem paths, or
connection details. `validate` parses YAML, resolves environment variables, and validates all
configuration without reading or writing data. `run` executes the ordered pipeline and prints
status, duration, row counts, and an error when applicable.

### Query and action API (v0.3.4)

Start the foreground server on its local-only default (`127.0.0.1:8765`):

```bash
nexolith api start
nexolith api start --host 127.0.0.1 --port 9000
# Wildcard binds require one or more exact Host values:
nexolith api start --host 0.0.0.0 --trusted-host nexolith.internal
```

The API exposes typed GET routes and explicit POST operations to register a DAG, trigger a
registered DAG run, and start or stop the scheduler. DAG triggers complete synchronously and
return the persisted run ID/status. No layer retries actions; retrying after a lost response can
create another run. Scheduler start launches the existing foreground command as a separate process
that survives API shutdown; stop remains PID-plus-creation-time-bound.

Interactive documentation is at `/docs` and the typed contract at `/openapi.json`. There is no
authentication or authorization: keep it on localhost or a trusted network. Actions require JSON,
Host values are allowlisted, cross-origin browser actions are rejected by default, and no wildcard
CORS is enabled. See
[the API package documentation](src/nexolith/api/README.md) for lifecycle, error, security, and
OpenAPI compatibility guarantees.

### React monitoring UI (v0.3.4)

The source-only frontend in [`web/`](web/README.md) provides monitoring routes for registered DAGs,
interactive task dependency graphs, recent runs, run detail, and scheduler state. Graphs combine
the current DAG definition with task statuses from its latest persisted run, and display cross-DAG
triggers at a separate DAG boundary. They support pan, zoom, reset, keyboard focus, and a textual
dependency summary. Accessible confirmation dialogs protect explicit DAG registration, run
triggering, and scheduler start/stop actions. Successful actions refresh backend state, and newly
triggered runs open their persisted detail route. The UI does not expose PID/process identity,
retry mutation requests, edit YAML or schedules, or invent optimistic operational states.

Install the API and frontend dependencies, then run both development processes:

```bash
uv sync --extra api --extra dev
uv run nexolith api start

# Separate terminal
cd web
npm ci
npm run dev
```

Vite serves `http://127.0.0.1:5173` and proxies relative `/api` reads to the local Nexolith API at
`http://127.0.0.1:8765`; no wildcard CORS or developer-machine hostname is required. The current
story does not serve frontend assets from FastAPI, package them in the Python wheel, publish them,
or add a `nexolith ui` command.

### Known limitations

The full-screen interactive session may occasionally render with visual corruption — overlapping
or garbled text in the scrollable output log, typically after a task failure followed by further
interaction. This has been reproduced in both VS Code's integrated terminal and plain
PowerShell/`conhost`, so it is not specific to one terminal application.

Root cause: believed to be a general class of alt-screen-buffer rendering bug affecting Windows
terminal emulators broadly under heavy redraw activity, not something Nexolith's code can fully
control from inside `prompt_toolkit`. See
[microsoft/terminal#3545](https://github.com/microsoft/terminal/issues/3545),
[#12329](https://github.com/microsoft/terminal/issues/12329),
[#13741](https://github.com/microsoft/terminal/issues/13741), and
[prompt-toolkit#1258](https://github.com/prompt-toolkit/python-prompt-toolkit/issues/1258).

Two real, confirmed contributing factors have already been fixed or mitigated: a stale
terminal-width bug where panel/border rendering kept using the width detected at session start
instead of the real width after a terminal resize (fixed), and redraw-burst timing during rapid
event streams (mitigated via `min_redraw_interval`). Both reduce how often this occurs but do not
eliminate it — the remaining cause sits outside code this project controls.

The classic, non-full-screen CLI (`validate`, `run`, `scheduler`, `runs`, and friends) is
completely unaffected in any terminal — this is specific to the full-screen session's alternate
screen buffer.

If you hit this: try `/clear`, or exit (`/exit`) and restart the full-screen session. If it
recurs often in your environment, prefer the classic CLI commands for that work instead.

(A secondary, unconfirmed hypothesis worth revisiting later: the blue divider lines separating the
full-screen layout's regions may visually blend with adjacent log content in some color schemes,
possibly contributing to perceived severity. This doesn't explain the scattered/duplicated text
fragments actually observed, which look like genuine buffer/redraw corruption rather than a
contrast issue, so it's noted as a possible minor factor, not the primary cause.)

### Error handling and exit codes

Expected failures use stable configuration, connector, transformation, and execution error
categories. CLI diagnostics use the form `Error [category]: message`, omit tracebacks, and redact
credentials, authenticated connection URLs, and raw driver details.

| Exit code | Meaning |
|---:|---|
| `0` | Command succeeded |
| `2` | Pipeline configuration or validation failed |
| `3` | Pipeline execution, connector, or transformation failed |

Unexpected programming errors are not converted into expected failures and may show a traceback for
debugging. Reproduce those failures in a development environment, sanitize logs before sharing
them, and never include credentials in issue reports.

### stdout, stderr, and scripting compatibility

`--version`, `diagnostics`, `validate`, and `run` are deterministic, non-interactive commands
suitable for CI and scripts. Their result output goes to stdout; `Error [category]: message`
goes to stderr. `run` additionally configures root Python logging (`INFO Pipeline '<name>'
started`, `succeeded`, or `failed: <type>`) which the standard library sends to stderr, never
stdout. `--version` is eager: it prints and exits before any subcommand or the interactive
session would start. These four commands never construct an interactive session or attach the
`EventSink` renderer, so their stdout/stderr contract does not change when Nexolith is invoked
without arguments and starts the interactive shell instead.

## Connectors

| Type | Source | Destination | Notes |
|---|---:|---:|---|
| `csv` | Yes | Yes | Header row required |
| `sqlite` | Yes | Yes | SQLAlchemy URL |
| `postgresql` | Yes | Yes | Install the `postgres` extra |

SQL sources accept either `table` or a read-only `query`. SQL destinations support:

- `append`: insert into an existing table, or create it when absent. Never clears existing rows.
- `replace`: drop an existing table, recreate it from the incoming columns, then insert. Destroys
  any real schema the table had -- foreign keys, primary keys, check constraints included. Only
  safe for a table Nexolith itself owns outright (e.g. a throwaway staging table); never safe
  against a real, pre-migrated, constrained schema.
- `fail`: stop if the table already exists at all -- a pure existence check, not a schema or
  content check. Unusable against a pre-migrated schema where the table legitimately exists
  before the first run.
- `truncate`: clear the table's existing rows in place (via `DELETE FROM`, not the SQL `TRUNCATE`
  statement -- see `SqlDestination`'s docstring for why), then insert. Schema and constraints are
  left untouched. The safe alternative to `replace` for a properly-migrated, constrained
  destination schema.

## Transformations

- `select`: keep the configured `columns`.
- `rename`: map old column names to new names.
- `drop_nulls`: drop rows with null or empty values in all or selected `columns`.
- `filter`: compare a column with `equals`, `not_equals`, `greater_than`,
  `greater_than_or_equal`, `less_than`, `less_than_or_equal`, `contains`, `is_null`, or
  `is_not_null`.
- `python_job`: run a user-supplied Python function in-process, as part of the pipeline's own
  data flow. See [Python job steps](#python-job-steps) below.

Filters, `select`, `rename`, and `drop_nulls` are interpreted operations; Nexolith never
evaluates YAML as Python code for them.

### Python job steps

> [!WARNING]
> `python_job` runs your own local Python code with no sandboxing. It is a deliberate,
> scoped exception to Nexolith's declarative design (see ADR-7), meant only for trusted
> local code you already control -- never for code from an untrusted source. Prefer the
> declarative transforms above whenever they can express the same logic.

A `python_job` step references a local `.py` file and a documented entrypoint function:

```yaml
transformations:
  - type: python_job
    file: jobs/enrich_customers.py
    entrypoint: run          # optional, defaults to "run"
    parameters:
      threshold: 10
```

`file` resolves relative to the pipeline YAML's own directory (same convention as
`query_file` and DAG `pipeline:` references). The entrypoint must match:

```python
from nexolith.jobs import JobContext
from nexolith.types import Rows


def run(rows: Rows, context: JobContext) -> Rows:
    ...
    return rows
```

`rows` is exactly the same `list[dict]` shape that flows between every other transform step;
the entrypoint receives it and must return data in that same shape. `context.parameters` carries
the step's own static `parameters:` values (same `dict` shape as story 2's SQL parameters).
There is no access to the pipeline configuration, connectors, or the wider application --
the contract is intentionally minimal.

Loading a job file is a normal Python import (`importlib.util.spec_from_file_location`), not
`eval`/`exec` on a string -- but that also means the file's own top-level code (module-level
statements, imports, decorators) runs exactly as it would for any Python import, both when
`nexolith validate` confirms the entrypoint exists and again when the step actually executes.
Keep job files free of expensive or unsafe top-level side effects; put all logic inside the
entrypoint function.

An exception raised inside a job's entrypoint fails the pipeline cleanly, the same way any
other transformation error does: the original exception's message is never included in
Nexolith's own error output (only its type name is), so a job's own logging accidentally
containing something sensitive is not repeated back through Nexolith's error path.

`python_job` is Model A -- in-process, exchanging Nexolith's own in-flight rows. It is not
suitable for engines with their own distributed data model (e.g. PySpark); that is Model B,
below.

### Script job steps (Model B)

> [!WARNING]
> `script:` runs your own local script, in its own subprocess, with no sandboxing. Same
> trusted-local-code posture as `python_job` (see ADR-7) -- never for untrusted code.

Model B is a DAG task type, not a pipeline transform step: a self-contained script that
manages its own I/O entirely (its own reads, its own writes, its own database connections)
and does not receive or return Nexolith's in-flight rows. It exists for shapes a single
pipeline can't express -- for example a single source read that fans out into several
independently-filtered exports -- and for engines with their own execution model (e.g.
PySpark) that shouldn't run inside Nexolith's own process.

A DAG task references either a pipeline or a script, never both:

```yaml
name: fan_out_exports
tasks:
  - name: export_by_segment
    script: jobs/fan_out.py
    entrypoint: run              # optional, defaults to "run"
    interpreter: .venv-spark/bin/python  # optional, defaults to Nexolith's own interpreter
    parameters:
      database: data/customers.db
      out_dir: build/exports
    depends_on: []
```

The script's entrypoint receives a single `context` argument exposing `.parameters` (a plain
object, not an importable Nexolith type -- the configured interpreter may be a completely
separate virtual environment with no `nexolith` package installed at all, e.g. a dedicated
PySpark venv):

```python
def run(context):
    database = context.parameters["database"]
    ...  # the script's own I/O; nothing is returned to Nexolith
```

Unlike `python_job`, a script step always runs as a subprocess, for two reasons: an engine's
own session lifecycle and heavy dependencies (PySpark's JVM gateway, for example) should not
be importable into Nexolith's own long-lived process, and the `interpreter:` field -- letting
a script use its own venv with dependencies Nexolith itself never needs -- can only work by
launching a separate process with that interpreter. A clean exit is success; an exception or
any non-zero exit is a failure, recorded the same way a failed pipeline task is (including
retries, if configured).

Because a script's own stdout/stderr is arbitrary text a script's author controls, Nexolith
cannot inspect or redact it the way it can a caught Python exception's type. Captured output
is logged for real debugging, but -- unlike every other Nexolith error message -- that log
line is **not** guaranteed free of anything sensitive a misbehaving script prints; the safe,
generic failure message recorded in DAG run state (`nexolith runs show`) never includes it.

Only the script's own file is validated ahead of time (`nexolith validate` confirms it
exists); unlike `python_job`, the entrypoint itself is not checked in advance, since doing so
would mean importing the script into Nexolith's own interpreter -- exactly the coupling
subprocess isolation exists to avoid. A missing or misnamed entrypoint surfaces as a real,
clean execution failure instead.

## Environment variables

Use exact or embedded placeholders:

```yaml
connection_url: ${DATABASE_URL}
```

Missing variables produce an error naming only the missing variable. Nexolith does not log
connection URLs or environment values. Copy `.env.example` as a reference; Nexolith does
not automatically load `.env` files.

## Development

```bash
uv sync --extra api --extra dev
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest
```

Frontend checks run from `web/` with the Node version pinned in `.node-version`:

```bash
npm ci
npm run api:check
npm run lint
npm run typecheck
npm test
npm run build
```

These commands run without Docker or external services. PostgreSQL tests are marked and skipped
unless explicitly selected. The same standard checks run in GitHub Actions on pushes to `master`
and pull requests. CI also builds the package and installs the wheel on every supported Python
version (3.12, 3.13, and 3.14) before verifying import and the first CLI workflow.

## PostgreSQL with Docker Compose

PostgreSQL integration requires Docker Engine with Docker Compose v2. The Compose service uses a
fixed PostgreSQL 17.6 Alpine image, local-only development credentials, a health check, and a named
volume.

Copy the example environment file, or export its values in your shell:

```bash
cp .env.example .env
uv sync --extra dev --extra postgres
docker compose up -d --wait postgres
```

Nexolith does not load `.env` automatically. Export the URL before running the integration tests
or example:

```bash
export DATABASE_URL="postgresql+psycopg://nexolith:nexolith@localhost:5432/nexolith"
uv run pytest -m postgres
uv run nexolith validate examples/pipelines/postgresql_orders.yaml
uv run nexolith run examples/pipelines/postgresql_orders.yaml
docker compose down
```

PowerShell equivalent:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://nexolith:nexolith@localhost:5432/nexolith"
uv run pytest -m postgres
uv run nexolith validate examples/pipelines/postgresql_orders.yaml
uv run nexolith run examples/pipelines/postgresql_orders.yaml
docker compose down
```

Use `docker compose down --volumes` when you also want to remove the Nexolith development data.
The included `nexolith` credentials are intentionally non-sensitive local defaults; override
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT` when needed. Never reuse
these defaults outside an isolated development environment.

If port `5432` is already occupied, choose another host port and keep the URL consistent:

```powershell
$env:POSTGRES_PORT = "55432"
$env:DATABASE_URL = "postgresql+psycopg://nexolith:nexolith@localhost:55432/nexolith"
docker compose up -d --wait postgres
```

An explicitly requested `pytest -m postgres` run fails clearly when `DATABASE_URL` is absent or
PostgreSQL is unreachable. The standard `pytest` command skips these service-dependent tests.
PostgreSQL schemas are inferred from the first incoming row, and migrations, schema evolution,
and connection pooling configuration remain outside the current MVP.

## Roadmap

Available:

- [x] YAML validation and environment substitution
- [x] CSV, SQLite, and PostgreSQL connectors, including parameterized and file-backed queries
- [x] Core transformations, an interactive and scriptable CLI, and in-memory execution results
- [x] In-process Python and subprocess script job steps
- [x] DAG orchestration, configurable retries and failure-propagation policy, cross-DAG
      triggers, and priority/severity classification
- [x] A scheduler daemon and persisted execution history
- [x] A typed REST API for monitoring and explicit DAG/scheduler actions
- [x] A responsive React UI for DAG/run monitoring and explicit backend actions
- [x] Interactive read-only DAG dependency graphs with latest-run task status

See [CHANGELOG.md](CHANGELOG.md) for exactly which release each landed in.

Planned, not implemented:

- [ ] API authentication/authorization, MySQL, and additional connectors
- [ ] A custom transformation plugin system
- [ ] Parallel task execution within a DAG (currently sequential)
- [ ] Data-quality checks
- [ ] Browser action controls, lineage, metrics, and observability

## Contributing, security, and license

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report
vulnerabilities according to [SECURITY.md](SECURITY.md), not in public issues. Community
participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Nexolith is licensed under the [Apache License 2.0](LICENSE).
