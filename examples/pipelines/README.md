# Example pipelines

`completed_orders.yaml` runs locally from CSV to SQLite. `postgresql_orders.yaml` runs from CSV to
the Docker Compose PostgreSQL service and resolves `DATABASE_URL` from the environment.

Run commands from the repository root so relative data paths resolve correctly. Validate an example
before execution with `uv run nexolith validate examples/pipelines/<name>.yaml`.
