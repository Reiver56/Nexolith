"""Read-side records returned by `StateStore`. Plain, immutable dataclasses
-- not Pydantic -- matching `ExecutionResult`/`SelectedPipeline`'s existing
split: Pydantic is for parsing user-authored declarative YAML, plain
dataclasses are for internal runtime/query results. These are never parsed
from untrusted input, only constructed from trusted database rows.
"""

from dataclasses import dataclass
from enum import StrEnum


class DagRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Never ran because an upstream dependency failed -- distinct from
    # FAILED (the task itself ran and errored) and PENDING (still eligible
    # to run). Added in schema_version 2.
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DagRecord:
    name: str
    source_path: str
    schedule: str | None
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DagRunRecord:
    id: int
    dag_name: str
    status: DagRunStatus
    trigger_reason: str
    started_at: str
    ended_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TaskRunRecord:
    dag_run_id: int
    task_name: str
    status: TaskRunStatus
    started_at: str | None
    ended_at: str | None
    error: str | None
