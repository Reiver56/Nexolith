# SQL enrichment chain (query_file demo)

Focused demo of `query_file`-based pipelines (v0.3.3 story 1, NXL-81) chained
through a single linear DAG: `task1 -> task2 -> task3 -> task4 -> task5`.
Reuses FonoLink's existing Postgres instance (`examples/fonolink/docker-compose.yml`,
port `5434`) -- no new database, no new example, no branch merges.

## Why not just use `cdr_raw` directly

FonoLink's own `cdr_raw` is clean and deterministic by design -- the stress
test's own verification relied on exact, reproducible counts. That's useless
as a "task1 = dirtiest" starting point. `inject_messiness.py` builds a
separate, deliberately messy staging table instead.

## Step 1: injected messiness

`inject_messiness.py` samples 2000 real `cdr_raw` rows (deterministic,
`ORDER BY id LIMIT 2000`) into a new, loosely-typed table
`cdr_enrichment_stage1` (nullable TEXT columns, no CHECK/FK -- genuinely
dirty data can't live in a column that enforces either). One disjoint defect
per affected row, `random.Random(42)`-assigned, so every later stage's
cleanup is independently checkable against a known number:

| Defect | Rows |
|---|---|
| Clean (no defect) | 1175 |
| Exact duplicate (inserted twice) | 150 |
| `call_type` = NULL | 30 |
| `duration_or_volume` = NULL | 40 |
| `cell_tower` = NULL | 80 |
| `occurred_at` malformed (`N/A`, `''`, `unknown`, `TBD`, `null`) | 25 |
| `call_type` mixed-case (`Voice`/`VOICE`/`Data`, ...) | 200 |
| `cell_tower` mixed-case | 150 |
| `occurred_at` valid but inconsistent precision/format (date-only, `T`-separated, microsecond-suffixed) | 150 |

Total staged rows: 2000 base + 150 duplicates = **2150**, confirmed against
the real table:

```
total | null_call_type | null_duration | null_tower | distinct_ids
------+----------------+---------------+------------+--------------
 2150 |             30 |            40 |         80 |         2000
```

Run with:
```bash
uv run --with "psycopg[binary]" python examples/fonolink/sql-enrichment-chain/inject_messiness.py
```

## Step 2: the 5-stage chain

Each stage is one pipeline, bound to its SQL via `query_file` (resolved
relative to the pipeline's own directory, e.g. `../queries/task1_stage.sql`),
writing to its own real Postgres table via `mode: replace`. `replace` is
safe here specifically because these are throwaway analytics tables with no
real constraints to lose -- unlike FonoLink's own `sim_cards`/`invoices`,
where `replace`'s drop-and-recreate would destroy real FK/PK/CHECK
constraints (see the FonoLink README).

| Task | Query file | Destination | Does |
|---|---|---|---|
| task1 | `task1_stage.sql` | `cdr_stage1` | Light initial pass: names the messy staged data into the chain's own first table. Casts `duration_or_volume` to `float8` (see Bug found, below). |
| task2 | `task2_dedup_and_nulls.sql` | `cdr_stage2` | `DISTINCT ON (id)` removes exact duplicates; `WHERE ... IS NOT NULL` drops rows missing a field that can't be safely defaulted. |
| task3 | `task3_normalize.sql` | `cdr_stage3` | Casing collapsed (`LOWER`/`UPPER`); `occurred_at::timestamp` parses any of the valid formats; unparseable `occurred_at` rows dropped (can't normalize garbage). |
| task4 | `task4_enrich.sql` | `cdr_stage4` | Joins `subscribers` for `name`/`plan`/`status` business context. Inner join, safe: `subscriber_id` is a real NOT NULL FK. |
| task5 | `task5_summarize.sql` | `cdr_stage5_enriched` | Per-subscriber usage summary with plan context: call/session/message counts, totals, first/last record timestamps. |

DAG: `dag.yaml`, linear `depends_on` chain, `task1 -> task2 -> task3 -> task4 -> task5`.

## Step 3: real run and verification

```bash
export FONOLINK_PIPELINE_DATABASE_URL="postgresql+psycopg://fonolink:fonolink@localhost:5434/fonolink"
uv run --extra postgres nexolith run examples/fonolink/sql-enrichment-chain/dag.yaml
```

All 5 tasks succeeded (run id 5, `nexolith runs show 5`; severity `low`,
priority `normal` -- this is a demo, not FonoLink's own operational chain).

Real row counts at each stage, verified directly against Postgres:

| Stage | Rows | Distinct IDs | Change |
|---|---|---|---|
| stage1 | 2150 | 2000 | baseline (2000 base + 150 duplicates) |
| stage2 | 1850 | 1850 | -300 (150 duplicates + 30+40+80 nulls removed) |
| stage3 | 1825 | 1825 | -25 (unparseable `occurred_at` dropped) |
| stage4 | 1825 | 1825 | 0 (inner join lost nothing -- FK guarantees a match) |
| stage5 | 20 rows (1 per subscriber) | -- | `SUM(total_records) = 1825`, exact |

Every number matches Step 1's injected counts exactly: 150 duplicates gone
by stage2, 150 nulls gone by stage2, 25 malformed timestamps gone by stage3,
zero further loss through stage4. Spot-checks:

- `SELECT DISTINCT call_type FROM cdr_stage3` -> exactly `data`, `sms`,
  `voice` (all mixed-case variants collapsed).
- `SELECT cell_tower FROM cdr_stage3 WHERE cell_tower <> UPPER(cell_tower)`
  -> 0 rows (all uppercase).
- `pg_typeof(duration_or_volume)` in `cdr_stage3` -> `double precision`
  (the float8 cast held through the chain, so `SUM()` in task5 works).
- Only 20 of FonoLink's 800 subscribers appear in the final summary. Not a
  bug: `ORDER BY id LIMIT 2000` samples the *earliest* `cdr_raw` rows, and
  `seed.py` generates CDRs per-subscriber-then-next-subscriber, so the first
  2000 rows land on roughly 2000 / (78785 / 800) approx 20 subscribers. A
  random sample would have spread wider; this is a direct, explainable
  consequence of the deterministic `ORDER BY id` choice, not a defect.

## Step 4: what didn't go as expected -- a real bug found

First run (`runs show 4`) **failed** at task5 with `ConnectorError: Could
not read from SQL source`. Root cause, found by inspecting the actual
`cdr_stage4` schema Nexolith had created:

```
duration_or_volume | character varying
occurred_at        | character varying
```

Both should be numeric/timestamp. `SqlDestination._prepare_table`'s type
inference (`src/nexolith/connectors/sql.py:61`, `mode: replace`/first
write) only recognizes three Python types:

```python
def column_type(value: Any) -> Any:
    if isinstance(value, bool):
        return Integer()
    if isinstance(value, int):
        return Integer()
    if isinstance(value, float):
        return Float()
    return String()
```

Postgres `NUMERIC` comes back from psycopg as `decimal.Decimal`, and
`TIMESTAMP` as `datetime.datetime` -- neither matches any branch, so both
silently fall through to `String()`. This is a real, general bug (not
specific to this demo): any pipeline whose source is a NUMERIC/TIMESTAMP
column, written to a fresh table via `postgresql`/`sqlite` destination,
gets a mis-typed destination column with no warning -- fine for storage and
even for `MIN`/`MAX` (string comparison happens to preserve chronological
order for a fixed-format timestamp prefix), but any `SUM`/`AVG` on the
downgraded numeric column fails outright the moment something tries it,
exactly as task5 did.

Worked around in this demo (did not touch `src/nexolith/`, per scope) by
casting `duration_or_volume::float8` in `task1_stage.sql` -- `float8` comes
back as a real Python `float`, which the inference *does* recognize. The
`occurred_at`/datetime half of the same bug was deliberately **not** worked
around, so it's still visible: `cdr_stage3.occurred_at` is `character
varying`, not `timestamp`, in the real table today. It doesn't break this
demo (no arithmetic on it, and `MIN`/`MAX` string-order still matches
chronological order for same-format timestamps), but it's the same root
cause and worth fixing in `nexolith.connectors.sql.column_type` for anyone
relying on a `replace`-mode-created table having correct native types.

A second, smaller real observation: of the 150 precision-variant rows, the
48 that got a microsecond suffix injected in Step 1 keep that suffix after
`occurred_at::timestamp` round-trips through Python and back to a `String`
column (`str(datetime)` includes microseconds when present, omits them
otherwise) -- so `cdr_stage3.occurred_at` is not fully format-normalized to
one exact string shape, even though every value is independently valid and
chronologically sortable. Harmless here, but a real consequence of the same
type-inference gap: a `timestamp` destination column would have normalized
this for free.

## Files

- `inject_messiness.py` -- builds `cdr_enrichment_stage1` (Step 1).
- `queries/task{1..5}_*.sql` -- the 5 SQL stages.
- `pipelines/task{1..5}_*.yaml` -- one `query_file`-bound pipeline per stage.
- `dag.yaml` -- `sql_enrichment_chain`, linear 5-task dependency chain.

## Commit

Single commit, this example only. No `src/nexolith/` changes (the type
inference bug above is reported, not fixed, per scope). No push, merge,
rebase, squash, PR, or Notion update.
