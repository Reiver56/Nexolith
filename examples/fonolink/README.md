# FonoLink — a stress test, not a polished demo

A fictional telecom company, ten Postgres tables, ten pipelines spread across three
cross-DAG-triggered DAGs, real volumes (tens of thousands of CDR rows), and the real
scheduler daemon. Built specifically to find out what actually happens at a scale and
shape the TEC and medallion examples never exercised — a real, pre-constrained Postgres
schema written to by pipelines, a genuine multi-hop cross-DAG cascade, and DAG-level
priority/severity on real, differently-important flows. Every number in this README is
real, from an actual run, not illustrative.

Run everything from the **repository root**, same convention as every other example.

## Setup

```bash
cd examples/fonolink
docker compose up -d --wait postgres
cd ../..
uv run --with faker --with "psycopg[binary]" python examples/fonolink/seed.py
```

Postgres runs on **port 5434** (distinct from the project's own 5432 and TEC's 5433 —
all three can run simultaneously). Default credentials `fonolink`/`fonolink`/`fonolink`,
override via `FONOLINK_POSTGRES_DB`/`_USER`/`_PASSWORD`/`_PORT`. `docker compose down
--volumes` removes the data.

Two separate connection-URL env vars, deliberately not shared, matching TEC's own
established convention: `FONOLINK_DATABASE_URL` (plain `postgresql://`, used by `seed.py`
and the two Model B scripts, all raw `psycopg`) and `FONOLINK_PIPELINE_DATABASE_URL`
(`postgresql+psycopg://`, used inside pipeline YAMLs — Nexolith's own SQLAlchemy-based
connector needs the different scheme).

```bash
export FONOLINK_PIPELINE_DATABASE_URL="postgresql+psycopg://fonolink:fonolink@localhost:5434/fonolink"
uv run --extra postgres nexolith run examples/fonolink/dags/ingestion.yaml
uv run --extra postgres nexolith run examples/fonolink/dags/billing.yaml
uv run --extra postgres nexolith run examples/fonolink/dags/risk.yaml
```

(No CLI command registers a DAG without running it yet — same pre-existing limitation
the medallion/TEC examples already documented. Running each DAG once, in dependency
order, is also how it gets auto-registered for the scheduler daemon afterward.)

## Schema

Ten tables (`schema.sql`). Three categories, by who populates them:

- **Pre-seeded directly by `seed.py`**: `subscribers` (800), `sim_cards` (720 of them —
  90% of the base, already `active`; the rest provisioned by the ingestion DAG itself).
- **External-feed landing targets** (`seed.py` writes a CSV, a pipeline or script loads
  it): `cdr_raw` (`ingest_cdr`), the other 80 `sim_cards` (`provision_sim` +
  `confirm_activation`), `support_tickets` (`ingest_support_tickets`).
- **Fully pipeline-derived**: `usage_daily`, `billing_charges`, `invoices`, `payments`,
  `fraud_flags`, `churn_scores`.

Seeded volumes, one real run: 800 subscribers, 78,785 `cdr_raw` rows across a 14-day
window (2026-07-19 to 2026-08-01), 133 support tickets, 608 bank settlements. 16
subscribers carry a deliberately seeded fraud signal (8 with an extreme single-day voice
burst, 8 with an extreme single-day data spike); 45 carry a deliberately seeded churn
signal (declining usage in the second half of the window, plus 1–3 open/escalated
tickets each).

## The DAG/pipeline structure, and why

Three DAGs, connected by **cross-DAG triggers** (`trigger.on_success_of`), not a single
mega-DAG with internal `depends_on` alone — the whole point of this exercise was to
actually exercise that v0.3.3 feature across real DAG boundaries.

```
fonolink_ingestion (priority: normal, severity: medium, schedule: 1h)
  ingest_cdr ─────────────► aggregate_usage
  provision_sim ──────────► confirm_activation
  ingest_support_tickets
        │
        │ on_success_of
        ▼
fonolink_billing (priority: critical, severity: critical)
  calculate_billing ──────► generate_invoices ──────► reconcile_payments
        │
        │ on_success_of
        ▼
fonolink_risk (priority: high, severity: high)
  detect_fraud
  score_churn   (reads usage_daily + support_tickets [ingestion] + invoices [billing])
```

Priority/severity are declared **per DAG**, not per task — that's the real, current shape
of `DagConfig.priority`/`.severity` (confirmed by reading `dag/models.py`, not assumed).
The prompt's own phrasing ("`reconcile_payments` should be high priority/critical
severity") talks about individual pipelines; since that granularity doesn't exist in the
product, each DAG's priority/severity was set to match the most important thing inside
it — billing carries `reconcile_payments` (real money), so the whole billing DAG is
`priority: critical, severity: critical`; risk (fraud/churn) is important but one step
removed from money moving, `priority: high, severity: high`; ingestion is foundational
but routine, `priority: normal, severity: medium`.

Where Model A / Model B / parameterized queries were used, and why each specific spot:

- **`confirm_activation`, `reconcile_payments` — Model B scripts.** Both need a real
  `UPDATE` against existing rows (flip a SIM to active; mark an invoice paid). Nexolith's
  SQL destination has exactly three modes — `append`, `replace`, `fail` — and none of them
  is "update matching rows in place." `reconcile_payments` also genuinely needs two
  sources (the real `invoices` table and an external settlements feed) and two kinds of
  writes, which a single Nexolith pipeline (one source, one destination) cannot express
  at all. Real justification, not a stylistic choice.
- **`generate_invoices`, `detect_fraud`, `score_churn` — Model A `python_job`.**
  Per-row conditional business logic (`generate_invoices`: issued vs. void), a
  multi-branch threshold check with a distinct reason per branch (`detect_fraud`), and a
  three-signal weighted score that needs to clamp and combine differently-scaled
  quantities into one 0–1 number (`score_churn`) — none of that has a declarative-
  transform equivalent (`select`/`rename`/`drop_nulls`/`filter` have no computed or
  conditional column, and `filter` compares exactly one column to one static value).
  `score_churn`'s own source query is the genuine cross-DAG fan-in: a single SQL query
  joining `usage_daily` + `support_tickets` (ingestion's own output) with `invoices`
  (billing's), safe to read only because the risk DAG's cross-DAG trigger guarantees
  billing already finished.
- **`aggregate_usage`, `calculate_billing` — plain SQL, no job at all.** Both are `GROUP
  BY` aggregations; SQL already expresses them cleanly, so a job would be pure overhead
  here — the ADR's own "declarative first" principle, not every aggregation needs Python.
- **Parameterized queries (story 2)**: `calculate_billing` (`:period`, `:period_start`,
  `:period_end`, scoping the usage join to the billing period) and `score_churn`
  (`:recent_start`, splitting usage into "recent" vs. "early" halves for the decline
  calculation) — both genuine date/period-scoped aggregations, exactly the case story 2
  was built for.

## Running it, and what was actually verified

Two full passes were run, both from a fresh, deterministic seed (fixed `SEED = 42`):

**Pass 1** — direct, in dependency order (`nexolith run` on each DAG once), used to
verify real end-to-end data correctness:

- Fraud: **16 out of 16** deliberately seeded fraud subscribers detected, zero false
  positives, zero false negatives — reasons split correctly between the two seeded signal
  shapes ("excessive call volume", "excessive data volume").
- Churn: of the top 45 highest-scored subscribers, **39 (87%)** are exactly the seeded
  churn cohort; the other 6 are subscribers who, by the same real formula, happened to
  also pick up a support ticket and an overdue invoice — genuine signal from the same
  code path, not noise.
- Billing: 726 invoices totaling **$79,376.54**; 608 paid (exactly matching the 608
  seeded bank settlements), 118 overdue (exactly `726 − 608`).
- `sim_cards`: 800 total (720 pre-seeded + 80 provisioned), 788 active — `720 + round(80
  × 0.85) = 788`, exactly matching the seeded 85% confirmation rate.

**Pass 2** — a from-scratch reset, this time triggered through the **real, running
scheduler daemon** (`nexolith scheduler start`), to time the actual cross-DAG cascade.
Ingestion was triggered manually once (the realistic "a user/cron kicks off the day's
ingestion" starting point); billing and risk were left to the daemon to discover on its
own. Real observed wall-clock latency, default `poll_interval_seconds = 5.0`:

| Hop | Latency |
|---|---:|
| ingestion completion → billing detected | ~18.6s |
| billing completion → risk detected | ~5.0s |
| **total, ingestion completion → risk completion** | **~26.4s** |

The billing→risk hop lands almost exactly on one poll interval — a clean example of the
documented v0.3.3 priority-story behavior change ("cross-DAG triggers no longer cascade
within the same tick; each hop costs at least one extra tick"). The longer ingestion→
billing gap is consistent with several ticks elapsing while the daemon was first starting
up, not evidence that detection itself needs more than one tick once the daemon is
already running continuously.

**Is ~26s for a 2-hop chain a concern?** For a batch/ETL cadence (this is a telecom
billing pipeline, not a real-time system) it's a non-issue. But latency compounds
per hop and is bounded below by the poll interval regardless of how fast the actual
pipelines run (ingestion, billing, and risk each finish in single-digit seconds; the
cascade *detection* dominates total wall-clock time). A 4–5-DAG chain would cost
20–25+ seconds of pure polling latency on top of real work, which is worth knowing before
building a deep chain and expecting near-real-time reactions — the poll interval is
configurable per `Scheduler(poll_interval_seconds=...)`, so this is tunable, but the
default's cost at real chain depths is worth having actually measured rather than
assumed.

`runs list`/`runs show` (both CLI commands, now working per the just-fixed P0 bug) were
used throughout to inspect this — severity rendered correctly and distinctly for all
three DAGs in both the table and detail views (`critical` for billing, `high` for risk,
`medium`, uncolored, for ingestion — confirmed via `runs show`'s real `Trigger: schedule`
field on the daemon-triggered runs, versus `Trigger: manual` on the direct ones).
**Priority's execution-order effect was not independently re-observed here**: FonoLink's
own three DAGs form a strict serial dependency chain by design (each one's due-ness
depends on the previous one's completion), so no two of them were ever simultaneously
due in this exercise — there was no real contention for priority to resolve. Priority
ordering itself already has dedicated, deterministic tests in its own story (NXL-86);
this exercise didn't manufacture an artificial multi-DAG-contention scenario just to
re-prove something already covered elsewhere, and says so plainly here rather than
overclaiming it was demonstrated.

## What went wrong / rough edges found (the actual point of this exercise)

**A real incident, not hypothetical — manually running a downstream DAG does not record
a cross-DAG trigger reaction.** After pass 1, the derived tables were truncated (a fresh
reseed) to prepare for pass 2's timing run. When the scheduler daemon was started next,
it *immediately* re-triggered both `fonolink_billing` and `fonolink_risk` — against the
now-empty tables — and both failed (`calculate_billing`'s query returned zero rows against
an empty `usage_daily`; `detect_fraud`'s did too). The cause: `dag_trigger_reactions` is
only ever written by `Scheduler.tick()` itself, when *it* decides to trigger a DAG.
Running `nexolith run billing.yaml` directly (as pass 1 did) never touches that table —
so from the scheduler's point of view, billing had *never* reacted to ingestion's
successful run, and it correctly (by the letter of the actual mechanism) considered it
still due, and fired. This is not a crash and not memory-unsafe — the failure was clean,
recorded, and didn't corrupt anything — but it is a genuinely surprising interaction: a
user who manually runs a downstream DAG once "by hand" and later starts the scheduler
daemon should not assume the daemon won't independently re-fire the same DAG off the
same upstream completion it already handled manually. Worth documenting prominently
wherever the scheduler's cross-DAG behavior is explained; not something this exercise
worked around silently (this is a real, precisely-described gap, not a bug fixed here).

**A DAG whose overall run is `failed` can still have real side effects from independent
tasks.** In the same incident, `fonolink_risk`'s two tasks (`detect_fraud`, `score_churn`)
have no `depends_on` relationship to each other. `detect_fraud` failed; `score_churn`
does not depend on it, so it ran anyway — and because its own query uses `LEFT JOIN`s
against then-empty tables, it didn't fail, it just produced 726 rows of degenerate
all-zero scores, which were genuinely inserted into `churn_scores` even though the DAG
run as a whole is recorded as `failed`. This matches `on_failure: skip`'s documented
semantics exactly (independent branches proceed) — not a bug — but it's a sharp edge:
"the run shows failed" does not mean "nothing was written." Anyone treating a failed
`dag_runs` row as "no side effects happened" would be wrong here.

**`mode: replace`/`mode: fail` were avoided by design, and it's worth explaining why,
since it wasn't obvious until this example needed a real pre-constrained schema.** Every
FonoLink destination uses `mode: append`, deliberately. Reading `connectors/sql.py`
confirms `replace` drops the target table and recreates it from the incoming rows'
*inferred* column types — for a table with a real PK, FK, or `CHECK` constraint (every
table in this schema), that silently destroys all of it. `fail` checks only whether the
table *exists* (not whether it has rows), so it's unusable against any already-migrated,
pre-created-but-empty schema — which is the completely normal case `schema.sql`
produces. `append` is the only mode that preserves a real schema (it reflects the
existing table via SQLAlchemy rather than recreating it). The real consequence: there is
no upsert/idempotent-recompute mode, so re-running a pipeline a second time against a
table with a `UNIQUE` or composite-PK constraint (`usage_daily`, `billing_charges`,
`invoices`, `churn_scores`) will genuinely fail with a constraint violation unless the
derived tables are truncated first — this example's own two full passes each start from
a fresh reseed specifically because of this. TEC and medallion never surfaced this
because neither ever wrote pipeline output into a table with real constraints (medallion
is CSV-only; TEC's own pipelines only ever wrote to CSV destinations too).

## What worked cleanly

- Real Postgres ingestion at volume: 78,785 rows in ~3.2–4.3s via the CLI (`csv` source
  → `postgresql` `append` destination), no special handling needed for the CSV → Postgres
  `NUMERIC` type coercion.
- Both Model A (`python_job`) and Model B (`script`) worked exactly as designed at this
  scale, including the two-source, two-write `reconcile_payments` script.
- Parameterized queries (period-scoped billing, recent-window churn) worked correctly
  with static YAML-declared values.
- The full three-DAG cross-DAG cascade, run twice from the same seed, produced
  byte-for-byte identical real numbers both times (726 invoices, $79,376.54, 16 fraud
  flags, 726 churn scores) — genuinely deterministic and reproducible.
- `runs list`/`runs show` (the just-fixed CLI) correctly rendered a real, mixed set of
  DAG runs with different severities, different trigger reasons (`manual` vs.
  `schedule`), and real timestamps throughout.

## Cleanup

```bash
cd examples/fonolink && docker compose down --volumes
```

`data/`, `state*/` under this directory are generated output (gitignored) — safe to
delete and regenerate via `seed.py` at any time.
