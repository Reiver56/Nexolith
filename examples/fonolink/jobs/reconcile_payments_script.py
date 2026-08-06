"""reconcile_payments (Model B script, FonoLink stress test).

Touches money -- the flagship reason the billing DAG carries priority:
critical / severity: critical. Genuinely needs Model B, not just prefers
it: it reads TWO sources (the real invoices table and an external bank-
settlements feed CSV) and performs two kinds of writes (insert new
payments rows, update matching invoices' status) -- a single Nexolith
pipeline has exactly one source and one destination, and no UPDATE
destination mode exists at all. Settlements name only (subscriber_id,
period), never an amount: the real amount only exists once
generate_invoices has actually run, looked up here at run time rather than
predicted ahead of time in the seed data.
"""

import csv


def run(context: object) -> None:
    import psycopg

    parameters = context.parameters  # type: ignore[attr-defined]
    database_url = parameters["database_url"]
    settlements_path = parameters["settlements_path"]

    with open(settlements_path, newline="", encoding="utf-8") as handle:
        settlements = list(csv.DictReader(handle))

    paid = 0
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        for row in settlements:
            cur.execute(
                "SELECT id, total_amount FROM invoices WHERE subscriber_id = %s AND period = %s",
                (row["subscriber_id"], row["period"]),
            )
            match = cur.fetchone()
            if match is None:
                continue
            invoice_id, total_amount = match
            cur.execute(
                """
                    INSERT INTO payments (invoice_id, amount, paid_at, status)
                    VALUES (%s, %s, now(), 'completed')
                    """,
                (invoice_id, total_amount),
            )
            cur.execute("UPDATE invoices SET status = 'paid' WHERE id = %s", (invoice_id,))
            paid += 1

        # Every invoice never settled is genuinely overdue -- this is
        # the whole billing period's real cutoff, not a partial batch.
        cur.execute("UPDATE invoices SET status = 'overdue' WHERE status = 'issued'")
        overdue = cur.rowcount

    print(f"reconcile_payments: {paid} invoices paid, {overdue} marked overdue")
