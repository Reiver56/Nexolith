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
    INTERRUPTED = "interrupted"


class TaskRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Never ran because an upstream dependency failed -- distinct from
    # FAILED (the task itself ran and errored) and PENDING (still eligible
    # to run). Added in schema_version 2.
    SKIPPED = "skipped"
    # Never ran because the DAG's on_failure policy is 'block' and an
    # earlier task in the run ultimately failed -- distinct from SKIPPED,
    # which specifically means this task depends (directly or
    # transitively) on the failed one. A BLOCKED task may have no
    # dependency relationship to the failure at all. Added in
    # schema_version 3.
    BLOCKED = "blocked"


class TaskAttemptStatus(StrEnum):
    """A task_attempts row only ever exists once an attempt has actually
    started, so unlike TaskRunStatus there is no PENDING/SKIPPED/BLOCKED
    here -- an attempt is real execution or it doesn't exist yet.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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
    # The on_failure policy actually in effect for THIS run -- recorded at
    # run time (schema_version 3) rather than read from the DAG file's
    # current setting, so a past run's history stays accurate even if the
    # file's policy changes later. Defaulted here (not just in the
    # database) so existing positional construction of this dataclass
    # elsewhere in the codebase keeps working unchanged.
    on_failure: str = "skip"
    # The DAG's declared severity at the moment THIS run started
    # (schema_version 5, NXL-87) -- same reasoning and same technique as
    # on_failure above: snapshotted at run-start time, not read from the
    # DAG file's current setting, so a past run's history stays accurate
    # even if the file's severity changes later (unlike schedule/trigger/
    # priority, deliberately read fresh every time -- severity answers
    # "how serious was this failure", a question about a specific run in
    # the past, not "what should happen right now").
    severity: str = "medium"
    # Process that created this run (schema_version 6). A live owner can be
    # either the scheduler daemon or a foreground ``nexolith run`` process.
    # None identifies a legacy row created before ownership was recorded.
    owner_pid: int | None = None


@dataclass(frozen=True, slots=True)
class TaskRunRecord:
    dag_run_id: int
    task_name: str
    status: TaskRunStatus
    started_at: str | None
    ended_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TaskAttemptRecord:
    id: int
    dag_run_id: int
    task_name: str
    attempt_number: int
    status: TaskAttemptStatus
    started_at: str
    ended_at: str | None
    error: str | None
