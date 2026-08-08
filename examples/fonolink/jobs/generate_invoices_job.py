"""generate_invoices (Model A python_job, FonoLink stress test).

Real business logic per row -- assign an invoice status based on the
computed total -- that has no declarative-transform equivalent (no
computed/conditional column in select/rename/drop_nulls/filter).
"""

from nexolith.jobs import JobContext
from nexolith.types import Rows


def run(rows: Rows, context: JobContext) -> Rows:
    invoices: Rows = []
    for row in rows:
        total = float(row["total_amount"])
        status = "issued" if total > 0 else "void"
        invoices.append(
            {
                "subscriber_id": row["subscriber_id"],
                "period": row["period"],
                "total_amount": round(total, 2),
                "status": status,
            }
        )
    return invoices
