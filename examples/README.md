# Examples

Examples provide small pipelines and datasets that can be run from the repository root. The local
CSV-to-CSV example needs no services; the PostgreSQL example uses Docker Compose and
`DATABASE_URL`.

Start with the [first-pipeline tutorial](../FIRST_PIPELINE.md) for complete commands and an
explanation of the input, transformations, and generated output. Add datasets under `data/` and
their matching pipeline definitions under `pipelines/`; keep examples small and deterministic.
