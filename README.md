# Nexolith

> **Build data flows that last.**

Nexolith is a lightweight, modular Python framework for defining, validating, running,
and monitoring data pipelines between files and SQL databases.

[![CI](https://github.com/Reiver56/Nexolith/actions/workflows/ci.yml/badge.svg)](https://github.com/Reiver56/Nexolith/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

> [!IMPORTANT]
> Nexolith is an early-stage project. Its configuration format may evolve before 1.0.

## Tech Stack

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
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

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

```bash
uv sync --extra dev
uv run nexolith --version
```

Install PostgreSQL support when needed:

```bash
uv sync --extra dev --extra postgres
```

## Quick start

Run the fully local CSV-to-SQLite example from the repository root:

```bash
uv run nexolith validate examples/pipelines/completed_orders.yaml
uv run nexolith run examples/pipelines/completed_orders.yaml
```

The pipeline creates `nexolith.db` and replaces the `completed_orders` table:

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
  type: sqlite
  connection_url: sqlite:///nexolith.db
  table: completed_orders
  mode: replace
```

## CLI

```text
nexolith --version
nexolith validate path/to/pipeline.yaml
nexolith run path/to/pipeline.yaml
```

`validate` parses YAML, resolves environment variables, and validates all configuration
without reading or writing data. `run` executes the ordered pipeline and prints status,
duration, row counts, and an error when applicable.

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

The same checks run in GitHub Actions on pushes to `main` and pull requests.

## PostgreSQL with Docker Compose

```bash
docker compose up -d postgres
uv sync --extra dev --extra postgres
```

Set `DATABASE_URL` from `.env.example` in your shell, then run:

```bash
uv run nexolith run examples/pipelines/postgresql_orders.yaml
docker compose down
```

The included credentials are local development defaults only.

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
