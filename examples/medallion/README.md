# Medallion (bronze/silver/gold) pipeline pattern

A worked example of a layered bronze/silver/gold pipeline pattern, starting from raw CSV
ingestion, orchestrated as a single Nexolith DAG (NXL-84). Self-contained: CSV files only, no
Docker, no database. Every CSV `path:` inside a pipeline YAML here is relative to that YAML's own
directory (NXL-89) -- the same convention `query_file`/`python_job`'s `file:`/DAG
`pipeline:`/`script:` already use -- so the `pipelines/` and `jobs/` directories are portable as a
unit. The DAG itself (`dag.yaml`) is still invoked with a path relative to wherever you run
Nexolith from, same as any other DAG file.

## What bronze/silver/gold mean here

- **Bronze** (`pipelines/bronze_orders.yaml`): raw ingestion, as-is. `data/raw_orders.csv` copied
  straight through to `bronze/orders.csv` with `transformations: []` -- no cleaning, duplicates
  and gaps and all. This is "lightly landed" data: proof that the file arrived, nothing more.
- **Silver** (`pipelines/silver_orders.yaml`): cleaned and conformed. Reads bronze's own output,
  runs it through a `python_job` step (`jobs/conform_orders.py`) that drops rows with a missing
  `customer_id` or `amount`, removes exact-duplicate rows, lowercases `status`, and coerces
  `amount` to a consistent two-decimal string. None of that is expressible with Nexolith's
  declarative transforms (`select`/`rename`/`drop_nulls`/`filter`) -- there is no dedup or
  type-coercion transform -- which is exactly the gap Model A (ADR-7) exists to fill.
- **Gold** (`jobs/revenue_reports.py`, a DAG `script:` task, not a pipeline): business-ready
  aggregation. Reads silver's real output once and fans out into two independent reports --
  `revenue_by_customer.csv` (completed-order revenue per customer) and `revenue_by_status.csv`
  (order count and total amount per status). Aggregation (group-by/sum) has no declarative
  equivalent in Nexolith at all, and "one read, several independently-shaped exports" is the
  exact validated use case Model B (NXL-88) exists for -- it doesn't fit a single pipeline's
  one-source/one-destination contract.

Story 2 (parameterized SQL queries) is deliberately **not** used in this example: every layer
here is CSV, and parameterization is a SQL-source capability. It fits naturally in a
medallion pipeline the moment any layer reads from SQL instead (e.g. a date-range-scoped gold
aggregation query) -- there's nothing about the pattern itself that excludes it.

## How one layer's output becomes the next layer's input

The convention this example confirms, using only what already exists -- no new engine
capability: **a fixed, coordinated path**. Bronze's `destination.path` (`../bronze/orders.csv`)
and silver's `source.path` (also `../bronze/orders.csv`, both relative to `pipelines/`) name the
same real file; silver's `destination.path` (`../silver/orders.csv`) and gold's `silver_path`
parameter (`examples/medallion/silver/orders.csv`, relative to wherever the DAG itself is
invoked from) name the same file again. The DAG's `depends_on` (`silver` depends on `bronze`,
`gold` depends on `silver`) guarantees the producer always finishes writing before the consumer
runs. Confirmed for real, not faked: the DAG below was actually executed end-to-end, and
silver's own output file is what gold's script actually opened and aggregated -- see the
Verification section.

### A gap this example surfaced, since fixed (NXL-89)

Building this convention for real originally surfaced a genuine inconsistency: `query_file`
(NXL-81), `python_job`'s `file:` (NXL-83), and a DAG's `pipeline:`/`script:` (NXL-88) all
resolved relative to the referencing YAML's own directory, but CSV source/destination `path:`
resolved only against the process's current working directory (`connectors/csv.py` had no
`base_dir` concept at all) -- confirmed by reading the code, then reproduced for real by loading
this exact example from an unrelated working directory and watching the read fail. Filed and
fixed as NXL-89: `CsvSource`/`CsvDestination` paths now resolve relative to the pipeline YAML's
own directory, exactly like the others. This example's own `bronze_orders.yaml`/
`silver_orders.yaml` paths were rewritten (`examples/medallion/bronze/orders.csv` ->
`../bronze/orders.csv`, etc.) as part of that fix -- the `pipelines/` directory is now genuinely
portable, not just correct by cwd coincidence.

A DAG task's `parameters:` (used here by the gold script for `silver_path`/`out_dir`) are
arbitrary values with no Nexolith-side path resolution at all -- a script does its own I/O with
whatever string it's given. That's unaffected by NXL-89 and unrelated to the CSV fix: it's why
`dag.yaml`'s `silver_path`/`out_dir` stay relative to wherever the DAG itself is invoked from.

## Running it

```bash
uv run python -c "
from pathlib import Path
from nexolith.dag import execute_dag
from nexolith.state import StateStore

store = StateStore(Path('build/medallion_state.db'))
try:
    run_id = execute_dag(Path('examples/medallion/dag.yaml'), store)
    print(store.get_dag_run(run_id).status)
finally:
    store.close()
"
```

(No CLI command triggers a DAG directly yet -- same as the TEC reference dataset's own
documented workaround.) Inspect `examples/medallion/bronze/orders.csv`,
`examples/medallion/silver/orders.csv`, and `examples/medallion/gold/*.csv` afterward; all three
are generated output, gitignored, safe to delete and regenerate.

## Verification

A real run against the checked-in `data/raw_orders.csv` (10 raw rows: one exact duplicate, one
missing `amount`, one missing `customer_id`):

- Bronze: 10 rows, unchanged.
- Silver: 7 rows (duplicate removed, the two invalid rows dropped, `status` lowercased).
- Gold `revenue_by_customer.csv`: `101,99.80` / `102,152.10` / `103,60.00` / `105,200.00`
  (customer `104`'s only order was cancelled, so it has no completed revenue and is correctly
  absent, not a zero row).
- Gold `revenue_by_status.csv`: `cancelled,1,75.50` / `completed,6,511.90`.

Covered by `tests/unit/test_medallion_example.py`, which asserts these exact values, not just a
successful exit status.
