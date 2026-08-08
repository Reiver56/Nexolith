"""Read-only scheduler status queries shared by CLI and HTTP adapters."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from nexolith.process_identity import ProcessIdentityProvider, lookup_process_identity
from nexolith.scheduler.pidfile import (
    PidFileOwnerStatus,
    default_pidfile_path,
    pidfile_owner_status,
    read_pidfile,
)


class SchedulerQueryState(StrEnum):
    RUNNING = "running"
    NOT_RUNNING = "not_running"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SchedulerStatusSnapshot:
    state: SchedulerQueryState
    pid: int | None = None
    started_at: str | None = None
    reason: str | None = None


def query_scheduler_status(
    path: Path | None = None,
    *,
    identity_provider: ProcessIdentityProvider = lookup_process_identity,
) -> SchedulerStatusSnapshot:
    """Inspect scheduler ownership without signalling or changing the pidfile."""
    pidfile_path = path or default_pidfile_path()
    record = read_pidfile(pidfile_path)
    if record is None:
        if pidfile_path.exists():
            return SchedulerStatusSnapshot(SchedulerQueryState.UNKNOWN, reason="corrupt")
        return SchedulerStatusSnapshot(SchedulerQueryState.NOT_RUNNING)

    owner_status = pidfile_owner_status(record, identity_provider=identity_provider)
    if owner_status is PidFileOwnerStatus.MATCHING:
        return SchedulerStatusSnapshot(
            SchedulerQueryState.RUNNING,
            pid=int(record.pid),
            started_at=record.started_at,
        )
    if owner_status in {PidFileOwnerStatus.DEAD, PidFileOwnerStatus.REUSED}:
        return SchedulerStatusSnapshot(SchedulerQueryState.NOT_RUNNING)
    return SchedulerStatusSnapshot(SchedulerQueryState.UNKNOWN, reason=owner_status.value)
