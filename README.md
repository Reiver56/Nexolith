<p align="center">
  <img src="assets/nexo-icon.png" alt="Nexo, the Nexolith mascot" width="160">
</p>

# Nexolith

> **Build data flows that last.**

Nexolith is a lightweight, modular Python framework for defining, validating, running,
and monitoring data pipelines between files and SQL databases.

[![CI](https://github.com/Reiver56/Nexolith/actions/workflows/ci.yml/badge.svg)](https://github.com/Reiver56/Nexolith/actions/workflows/ci.yml)
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
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-supported-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![pytest](https://img.shields.io/badge/pytest-tested-0A9EDC?logo=pytest&logoColor=white)](https://pytest.org/)
[![Ruff](https://img.shields.io/badge/Ruff-lint%20%26%20format-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-CI-2088FF?logo=githubactions&logoColor=white)](https://github.com/features/actions)

## Features

- Declarative YAML pipelines with Pydantic validation
- CSV, SQLite, and PostgreSQL sources and destinations
- Safe select, rename, drop-null, and declarative filter transformations
- Environment variable substitution using `${VARIABLE_NAME}`
- Execution status, timing, row counts, and safe error reporting
- Small registries for adding connectors and transformations
- A Typer CLI with non-zero exit codes on failure

## Architecture

Configuration loading is separate from I/O and processing. A pipeline runner asks the
connector registry for its source and destination, and the transformation registry builds
each ordered transformation. Rows use the intentionally small `list[dict]` representation.
Execution results are plain models that can later be persisted without coupling persistence
to the runner.

## Requirements and installation

- Python 3.12, 3.13, or 3.14

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
```

Running `nexolith` without a subcommand opens the minimal Nexo interactive session. Use `/help` to
list its currently available commands, `/open <path>` to select or replace a valid pipeline,
`/open` to show the current selection, `/clear` to remove it, and `/exit` to leave. The selected
filename appears in the prompt and the context lasts only for the current process. Interactive
validation and execution are not available yet; pipeline operations remain available through the
established non-interactive `validate` and `run` commands.

`diagnostics` prints shareable Nexolith, Python, platform, dependency, and optional-feature
information without exposing environment variables, usernames, hostnames, filesystem paths, or
connection details. `validate` parses YAML, resolves environment variables, and validates all
configuration without reading or writing data. `run` executes the ordered pipeline and prints
status, duration, row counts, and an error when applicable.

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

## Connectors

| Type | Source | Destination | Notes |
|---|---:|---:|---|
| `csv` | Yes | Yes | Header row required |
| `sqlite` | Yes | Yes | SQLAlchemy URL |
| `postgresql` | Yes | Yes | Install the `postgres` extra |

SQL sources accept either `table` or a read-only `query`. SQL destinations support:

- `append`: insert into an existing table, or create it when absent.
- `replace`: drop an existing table, recreate it from the incoming columns, then insert.
- `fail`: stop if the table already exists.

## Transformations

- `select`: keep the configured `columns`.
- `rename`: map old column names to new names.
- `drop_nulls`: drop rows with null or empty values in all or selected `columns`.
- `filter`: compare a column with `equals`, `not_equals`, `greater_than`,
  `greater_than_or_equal`, `less_than`, `less_than_or_equal`, `contains`, `is_null`, or
  `is_not_null`.

Filters are interpreted operations. Nexolith never evaluates YAML as Python code.

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
uv sync --extra dev
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest
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

Available in `0.1.0`:

- [x] YAML validation and environment substitution
- [x] CSV, SQLite, and PostgreSQL connectors
- [x] Core transformations, CLI, and in-memory execution results

Planned, not implemented:

- [ ] REST API, MySQL, and additional connectors
- [ ] Custom transformation plugins and configurable retries
- [ ] Persisted execution history and scheduling
- [ ] Parallel execution and data-quality checks
- [ ] Lineage, metrics, observability, and a web dashboard

## Contributing, security, and license

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report
vulnerabilities according to [SECURITY.md](SECURITY.md), not in public issues. Community
participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Nexolith is licensed under the [Apache License 2.0](LICENSE).
