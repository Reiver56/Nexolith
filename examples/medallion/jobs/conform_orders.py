"""Silver-layer conforming logic for the medallion example (NXL-84).

A Model A `python_job` step: real cleaning logic (exact-duplicate removal,
missing-value dropping, status normalization, numeric type coercion) that
Nexolith's declarative transforms (select/rename/drop_nulls/filter) cannot
express -- there is no dedup or type-coercion transform. This is exactly
the kind of logic ADR-7 scoped Model A for.
"""

from nexolith.jobs import JobContext
from nexolith.types import Rows


def run(rows: Rows, context: JobContext) -> Rows:
    seen: set[tuple[object, object, object, object]] = set()
    cleaned_rows: Rows = []
    for row in rows:
        customer_id = row.get("customer_id")
        amount = row.get("amount")
        if not customer_id or not amount:
            continue  # bronze carries raw gaps; silver must not

        key = (customer_id, row.get("order_date"), row.get("status"), amount)
        if key in seen:
            continue  # exact-duplicate row, e.g. a re-landed bronze record
        seen.add(key)

        cleaned = dict(row)
        cleaned["status"] = str(row.get("status") or "").lower()
        cleaned["amount"] = f"{float(str(amount)):.2f}"
        cleaned_rows.append(cleaned)
    return cleaned_rows
