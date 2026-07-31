# Integration tests

This directory verifies real I/O boundaries. CSV and SQLite tests run in the standard suite. The
[first-pipeline tutorial](../../FIRST_PIPELINE.md) is checked by copying its real example into a
temporary clean directory, invoking both documented CLI commands, and asserting the generated CSV.
Extend that test when the introductory example's commands or expected rows change.

PostgreSQL tests use the Compose service and dedicated `nxl9_*` tables that are removed after each
test.

```bash
docker compose up -d --wait postgres
uv sync --extra dev --extra postgres
export DATABASE_URL="postgresql+psycopg://nexolith:nexolith@localhost:5432/nexolith"
uv run pytest -m postgres
docker compose down --volumes
```

In PowerShell, set the URL with
`$env:DATABASE_URL = "postgresql+psycopg://nexolith:nexolith@localhost:5432/nexolith"`.
Explicit PostgreSQL runs fail when the URL is missing or the service is unreachable; ordinary
`uv run pytest` skips the marked tests.
