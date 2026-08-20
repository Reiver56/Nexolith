# Examples

Examples provide small pipelines, DAGs, and datasets that can be run from the repository root.

Start with the [first-pipeline tutorial](../FIRST_PIPELINE.md) for a single CSV-to-CSV pipeline
that needs no services -- complete commands and an explanation of the input, transformations, and
generated output. `pipelines/` holds that tutorial's pipeline definitions and a PostgreSQL variant
(Docker Compose and `DATABASE_URL` required); `data/` holds their datasets.

For DAG orchestration across multiple pipelines and job step types, see:

- **[medallion/](medallion/README.md)** -- a self-contained bronze/silver/gold pipeline pattern
  (declarative, `python_job`, and `script` steps chained in one DAG), CSV-only, no services needed.
- **[fonolink/](fonolink/README.md)** -- a larger, Postgres-backed stress test: 10 pipelines across
  3 cross-DAG-triggered DAGs with real fraud/churn signal, plus
  [fonolink/sql-enrichment-chain/](fonolink/sql-enrichment-chain/README.md), a focused demo of
  `query_file`-based pipelines chained through a 5-task DAG.

For named reusable destination behavior and trusted project-local discovery, see
**[nexo-functions/](nexo-functions/README.md)**. It is service-free and demonstrates the local
function contract without introducing package plugins or Nexo Actions.

Keep examples small and deterministic.
