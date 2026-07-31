from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class ExecutionResult:
    """Serializable execution metadata, ready for future persistence."""

    pipeline_name: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows_read: int = 0
    rows_written: int = 0
    error: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def start(self) -> None:
        self.status = ExecutionStatus.RUNNING
        self.started_at = datetime.now(UTC)

    def succeed(self, rows_written: int) -> None:
        self.status = ExecutionStatus.SUCCEEDED
        self.rows_written = rows_written
        self.finished_at = datetime.now(UTC)

    def fail(self, error: str) -> None:
        self.status = ExecutionStatus.FAILED
        self.error = error
        self.finished_at = datetime.now(UTC)
