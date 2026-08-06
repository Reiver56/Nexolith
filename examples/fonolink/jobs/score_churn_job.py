"""score_churn (Model A python_job, FonoLink stress test).

The genuine cross-DAG fan-in: the pipeline's own source query already
joins usage_daily and support_tickets (ingestion DAG output) with invoices
(billing DAG output) -- real data written by two upstream DAGs, read here
only because the risk DAG's cross-DAG trigger guarantees billing has
already completed. The weighted score itself is a real formula (usage
decline + open tickets + overdue invoices) that reads far more clearly as
Python than as one large CASE expression, and needs to clamp/combine three
differently-scaled signals into one 0-1 number -- a job, not a query.
"""

from datetime import UTC, datetime

from nexolith.jobs import JobContext
from nexolith.types import Rows

DECLINE_WEIGHT = 0.5
TICKET_WEIGHT = 0.3
OVERDUE_WEIGHT = 0.2


def run(rows: Rows, context: JobContext) -> Rows:
    computed_at = datetime.now(UTC).isoformat()
    scores: Rows = []
    for row in rows:
        recent = float(row["recent_data_mb"])
        early = float(row["early_data_mb"])
        tickets = int(row["ticket_count"])
        overdue = int(row["overdue_invoices"])

        decline_ratio = max(0.0, (early - recent) / early) if early > 0 else 0.0
        decline_component = min(1.0, decline_ratio) * DECLINE_WEIGHT
        ticket_component = min(1.0, tickets / 3) * TICKET_WEIGHT
        overdue_component = min(1.0, overdue / 1) * OVERDUE_WEIGHT
        score = round(decline_component + ticket_component + overdue_component, 3)

        factors = []
        if decline_component > 0:
            factors.append(f"usage declined {decline_ratio * 100:.0f}%")
        if tickets:
            factors.append(f"{tickets} open/escalated ticket(s)")
        if overdue:
            factors.append(f"{overdue} overdue invoice(s)")
        contributing_factors = "; ".join(factors) if factors else "no significant risk factors"

        scores.append(
            {
                "subscriber_id": row["subscriber_id"],
                "score": score,
                "computed_at": computed_at,
                "contributing_factors": contributing_factors,
            }
        )
    return scores
