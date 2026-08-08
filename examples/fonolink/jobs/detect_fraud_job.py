"""detect_fraud (Model A python_job, FonoLink stress test).

Threshold logic across two independent signal shapes (a single day's data
volume, a single day's call count) that reads more clearly as a short
Python function than as a single declarative filter condition -- and
`filter` only ever compares one column to one static value, it can't
express "flag if A > x OR B > y" with a distinct reason per branch.
"""

from datetime import UTC, datetime

from nexolith.jobs import JobContext
from nexolith.types import Rows

DATA_MB_THRESHOLD = 5000
VOICE_CALLS_THRESHOLD = 100


def run(rows: Rows, context: JobContext) -> Rows:
    detected_at = datetime.now(UTC).isoformat()
    flags: Rows = []
    for row in rows:
        data_mb = float(row["data_mb"])
        voice_calls = int(row["voice_calls"])
        if data_mb > DATA_MB_THRESHOLD:
            reason = f"excessive data volume in a single day ({data_mb:.0f} MB)"
        elif voice_calls > VOICE_CALLS_THRESHOLD:
            reason = f"excessive call volume in a single day ({voice_calls} calls)"
        else:
            continue
        flags.append(
            {
                "subscriber_id": row["subscriber_id"],
                "reason": reason,
                "detected_at": detected_at,
                "cdr_reference": row["sample_cdr_id"],
            }
        )
    return flags
