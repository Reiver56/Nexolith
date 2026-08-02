"""Interval-only schedule format: `<positive integer><unit>`, e.g. `30s`,
`5m`, `2h`, `1d`. No cron syntax -- see the story's own dependency
decision: cron would need a new runtime dependency (e.g. `croniter`),
which requires explicit authorization before being added, consistent with
every prior dependency in this project. Interval-only needs no new
dependency and covers the dominant real use case (recurring pipelines);
wall-clock-anchored schedules (e.g. "daily at 2am") are a deliberate,
documented gap left for a future cron-support story.
"""

import re
from datetime import timedelta

from nexolith.exceptions import ConfigurationError

_INTERVAL_PATTERN = re.compile(r"^(\d+)(s|m|h|d)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(schedule: str) -> timedelta:
    """Parse a schedule string into a `timedelta`. Raises `ConfigurationError`
    (matching every other user-facing config parsing error in this project)
    for anything that doesn't match `<positive integer><s|m|h|d>` -- an
    empty match, a zero value, or an unrecognized unit.
    """
    match = _INTERVAL_PATTERN.match(schedule.strip())
    if match is None:
        raise ConfigurationError(
            f"Invalid schedule '{schedule}'. Expected an interval like '30s', "
            "'5m', '2h', or '1d' (seconds/minutes/hours/days)."
        )
    amount = int(match.group(1))
    if amount == 0:
        raise ConfigurationError(
            f"Invalid schedule '{schedule}'. The interval amount must be positive."
        )
    unit = match.group(2)
    return timedelta(seconds=amount * _UNIT_SECONDS[unit])
