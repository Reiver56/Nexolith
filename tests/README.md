# Tests

Unit tests exercise isolated configuration and transformations. Integration tests exercise file,
SQLite, pipeline, and opt-in PostgreSQL behavior.

Run the service-free suite with `uv run pytest`. PostgreSQL tests are skipped unless selected with
`uv run pytest -m postgres`; see [integration tests](integration/README.md) for the Docker workflow.
