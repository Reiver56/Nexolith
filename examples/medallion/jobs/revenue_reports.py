"""Gold-layer aggregation for the medallion example (NXL-84, NXL-88).

A Model B script task: reads silver's real output once and fans out into
two independent, business-ready aggregates. Aggregation (group-by/sum) has
no declarative-transform equivalent in Nexolith at all, and this is also
exactly the "single read, multiple independent exports" shape a single
pipeline's one-destination contract can't express -- the validated use
case Model B was scoped for. Standard library only, deliberately: a script
is self-contained and may not have `nexolith` importable at all.
"""

import csv
import os
from collections import defaultdict


def run(context: object) -> None:
    parameters = context.parameters  # type: ignore[attr-defined]
    silver_path = parameters["silver_path"]
    out_dir = parameters["out_dir"]
    os.makedirs(out_dir, exist_ok=True)

    with open(silver_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    revenue_by_customer: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["status"] == "completed":
            revenue_by_customer[row["customer_id"]] += float(row["amount"])

    with open(f"{out_dir}/revenue_by_customer.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["customer_id", "completed_revenue"])
        for customer_id, total in sorted(revenue_by_customer.items()):
            writer.writerow([customer_id, f"{total:.2f}"])

    by_status: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_status[row["status"]].append(float(row["amount"]))

    with open(f"{out_dir}/revenue_by_status.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["status", "order_count", "total_amount"])
        for status, amounts in sorted(by_status.items()):
            writer.writerow([status, len(amounts), f"{sum(amounts):.2f}"])
