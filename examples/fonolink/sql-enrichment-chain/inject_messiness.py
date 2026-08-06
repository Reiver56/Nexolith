"""Build a deliberately messy staging table for the SQL enrichment chain
example (NXL query_file demo, reusing FonoLink's Postgres).

Run with:
    uv run --with "psycopg[binary]" python \
        examples/fonolink/sql-enrichment-chain/inject_messiness.py

FonoLink's own `cdr_raw` is clean/deterministic by design -- fine for the
stress test's exact-count verification, useless as a "task1 = dirtiest"
starting point. This script samples 2000 real `cdr_raw` rows (deterministic,
`ORDER BY id LIMIT 2000`) and writes them into a new, loosely-typed table
`cdr_enrichment_stage1` (nullable TEXT columns, no CHECK/FK -- a genuinely
dirty table can't live in a column that enforces NOT NULL/CHECK), injecting
one disjoint defect per affected row so every later stage's cleanup count is
independently verifiable. Deterministic and idempotent: fixed SEED, fixed
row-id assignment via `random.Random(42).shuffle`, DROP+CREATE before insert.
"""

import os
import random

import psycopg

SEED = 42
BASE_ROWS = 2000

# Disjoint defect bucket sizes -- sum must equal BASE_ROWS.
N_DUPLICATE = 150
N_NULL_CALL_TYPE = 30
N_NULL_DURATION = 40
N_NULL_CELL_TOWER = 80
N_MALFORMED_TIMESTAMP = 25
N_MIXED_CASE_CALL_TYPE = 200
N_MIXED_CASE_TOWER = 150
N_PRECISION_VARIANT_TIMESTAMP = 150
assert (
    N_DUPLICATE
    + N_NULL_CALL_TYPE
    + N_NULL_DURATION
    + N_NULL_CELL_TOWER
    + N_MALFORMED_TIMESTAMP
    + N_MIXED_CASE_CALL_TYPE
    + N_MIXED_CASE_TOWER
    + N_PRECISION_VARIANT_TIMESTAMP
    <= BASE_ROWS
)

MALFORMED_TIMESTAMP_VALUES = ["N/A", "", "unknown", "TBD", "null"]

FONOLINK_DATABASE_URL = os.environ.get(
    "FONOLINK_DATABASE_URL", "postgresql://fonolink:fonolink@localhost:5434/fonolink"
)


def _mixed_case(value: str, rng: random.Random) -> str:
    variant = rng.choice(["upper", "lower", "title"])
    if variant == "upper":
        return value.upper()
    if variant == "lower":
        return value.lower()
    return value.title()


def _precision_variant(ts_str: str, rng: random.Random) -> str:
    date_part, time_part = ts_str.split(" ")
    variant = rng.choice(["date_only", "t_separator", "microseconds", "no_seconds"])
    if variant == "date_only":
        return date_part
    if variant == "t_separator":
        return f"{date_part}T{time_part}"
    if variant == "microseconds":
        return f"{ts_str}.{rng.randint(0, 999999):06d}"
    return f"{date_part} {time_part.rsplit(':', 1)[0]}"


def main() -> None:
    rng = random.Random(SEED)

    with psycopg.connect(FONOLINK_DATABASE_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, subscriber_id, call_type, duration_or_volume, occurred_at, cell_tower
            FROM cdr_raw
            ORDER BY id
            LIMIT %s
            """,
            (BASE_ROWS,),
        )
        base_rows = cur.fetchall()
        if len(base_rows) < BASE_ROWS:
            raise RuntimeError(
                f"cdr_raw has only {len(base_rows)} rows, need {BASE_ROWS}. "
                "Run the FonoLink ingestion DAG first."
            )

        row_ids = [row[0] for row in base_rows]
        rng.shuffle(row_ids)

        buckets: dict[str, set[int]] = {}
        cursor = 0
        for name, size in [
            ("duplicate", N_DUPLICATE),
            ("null_call_type", N_NULL_CALL_TYPE),
            ("null_duration", N_NULL_DURATION),
            ("null_cell_tower", N_NULL_CELL_TOWER),
            ("malformed_timestamp", N_MALFORMED_TIMESTAMP),
            ("mixed_case_call_type", N_MIXED_CASE_CALL_TYPE),
            ("mixed_case_tower", N_MIXED_CASE_TOWER),
            ("precision_variant_timestamp", N_PRECISION_VARIANT_TIMESTAMP),
        ]:
            buckets[name] = set(row_ids[cursor : cursor + size])
            cursor += size
        clean_count = BASE_ROWS - cursor

        cur.execute("DROP TABLE IF EXISTS cdr_enrichment_stage1")
        cur.execute(
            """
            CREATE TABLE cdr_enrichment_stage1 (
                id INTEGER,
                subscriber_id INTEGER,
                call_type TEXT,
                duration_or_volume NUMERIC,
                occurred_at TEXT,
                cell_tower TEXT
            )
            """
        )

        rows_to_insert: list[tuple[int, int, str | None, float | None, str | None, str | None]] = []
        for (
            row_id,
            subscriber_id,
            call_type,
            duration_or_volume,
            occurred_at,
            cell_tower,
        ) in base_rows:
            occurred_at_str = occurred_at.strftime("%Y-%m-%d %H:%M:%S")

            if row_id in buckets["null_call_type"]:
                call_type = None
            elif row_id in buckets["mixed_case_call_type"]:
                call_type = _mixed_case(call_type, rng)

            if row_id in buckets["null_duration"]:
                duration_or_volume = None

            if row_id in buckets["null_cell_tower"]:
                cell_tower = None
            elif row_id in buckets["mixed_case_tower"]:
                cell_tower = _mixed_case(cell_tower, rng)

            if row_id in buckets["malformed_timestamp"]:
                occurred_at_str = rng.choice(MALFORMED_TIMESTAMP_VALUES)
            elif row_id in buckets["precision_variant_timestamp"]:
                occurred_at_str = _precision_variant(occurred_at_str, rng)

            rows_to_insert.append(
                (row_id, subscriber_id, call_type, duration_or_volume, occurred_at_str, cell_tower)
            )
            if row_id in buckets["duplicate"]:
                rows_to_insert.append(
                    (
                        row_id,
                        subscriber_id,
                        call_type,
                        duration_or_volume,
                        occurred_at_str,
                        cell_tower,
                    )
                )

        cur.executemany(
            """
            INSERT INTO cdr_enrichment_stage1
                (id, subscriber_id, call_type, duration_or_volume, occurred_at, cell_tower)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows_to_insert,
        )

        cur.execute("SELECT count(*) FROM cdr_enrichment_stage1")
        total = cur.fetchone()[0]

    print(f"cdr_enrichment_stage1: {total} rows ({BASE_ROWS} base + {N_DUPLICATE} duplicates)")
    print(f"  clean rows (no defect):        {clean_count}")
    print(f"  duplicated (extra copies):     {N_DUPLICATE}")
    print(f"  null call_type:                {N_NULL_CALL_TYPE}")
    print(f"  null duration_or_volume:       {N_NULL_DURATION}")
    print(f"  null cell_tower:               {N_NULL_CELL_TOWER}")
    print(f"  malformed occurred_at:         {N_MALFORMED_TIMESTAMP}")
    print(f"  mixed-case call_type:          {N_MIXED_CASE_CALL_TYPE}")
    print(f"  mixed-case cell_tower:         {N_MIXED_CASE_TOWER}")
    print(f"  precision-variant occurred_at: {N_PRECISION_VARIANT_TIMESTAMP}")


if __name__ == "__main__":
    main()
