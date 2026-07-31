# Integration tests

This directory verifies real I/O boundaries. CSV and SQLite tests run in the standard suite.
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
