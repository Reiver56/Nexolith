"""confirm_activation (Model B script, FonoLink stress test).

Why a script, not a python_job or a declarative pipeline: this needs an
UPDATE against existing sim_cards rows (flip 'pending' to 'active'), not an
append. Nexolith's SQL destination has exactly three modes -- append,
replace, fail -- and none of them is "update matching rows in place":
append would insert duplicate rows, replace drops and recreates the whole
table from the incoming rows' inferred types (destroying sim_cards' own
schema: its PK, its FK to subscribers, its CHECK constraint). A script
doing its own real UPDATE is the only way to express this at all today.

Standard library plus psycopg only -- no `nexolith` import, since a script
may run under a completely separate interpreter/venv.
"""

import csv


def run(context: object) -> None:
    import psycopg

    parameters = context.parameters  # type: ignore[attr-defined]
    database_url = parameters["database_url"]
    confirmations_path = parameters["confirmations_path"]

    with open(confirmations_path, newline="", encoding="utf-8") as handle:
        confirmations = list(csv.DictReader(handle))

    updated = 0
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        for row in confirmations:
            cur.execute(
                """
                    UPDATE sim_cards
                    SET activation_status = 'active', linked_at = %s
                    WHERE iccid = %s AND activation_status = 'pending'
                    """,
                (row["confirmed_at"], row["iccid"]),
            )
            updated += cur.rowcount

    print(
        f"confirm_activation: {updated} sim_cards activated "
        f"out of {len(confirmations)} confirmations"
    )
    # Zero matched is not itself an error -- a legitimate re-run with
    # nothing left in 'pending' looks exactly like this. Only a real
    # exception (a bad database_url, a missing confirmations_path) should
    # fail this task; that already propagates on its own.
