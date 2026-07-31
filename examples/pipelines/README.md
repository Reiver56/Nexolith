# Example pipelines

`completed_orders.yaml` is the service-free CSV-to-CSV example used by the
[first-pipeline tutorial](../../FIRST_PIPELINE.md). It writes generated output under `build/`.
`postgresql_orders.yaml` is the advanced example: it writes to the Docker Compose PostgreSQL
service and resolves `DATABASE_URL` from the environment.

Run commands from the repository root so relative data paths resolve correctly. Validate an example
before execution with `uv run nexolith validate examples/pipelines/<name>.yaml`. Keep pipeline
files declarative, small, and paired with committed input data under `examples/data/`.
