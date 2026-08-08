"""Explicit, stable HTTP representations for the monitoring API."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from nexolith.state import DagRunStatus, TaskAttemptStatus, TaskRunStatus

FailurePolicy = Literal["skip", "block"]
Severity = Literal["low", "medium", "high", "critical"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorCode(StrEnum):
    DAG_NOT_FOUND = "dag_not_found"
    RUN_NOT_FOUND = "run_not_found"
    DAG_SOURCE_MISSING = "dag_source_missing"
    DAG_CONFIGURATION_INVALID = "dag_configuration_invalid"
    REQUEST_VALIDATION_ERROR = "request_validation_error"
    STATE_UNAVAILABLE = "state_unavailable"
    SCHEDULER_IDENTITY_UNAVAILABLE = "scheduler_identity_unavailable"
    INTERNAL_ERROR = "internal_error"


class ApiErrorDetail(ApiModel):
    code: ApiErrorCode
    message: str


class ErrorResponse(ApiModel):
    detail: ApiErrorDetail


class ApiInfoResponse(ApiModel):
    api_version: Literal["v1"]
    package_version: str
    read_only: Literal[True]


class DagSourceStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    INVALID = "invalid"


class DagTriggerResponse(ApiModel):
    on_success_of: list[str]


class DagSummaryResponse(ApiModel):
    name: str
    enabled: bool
    registered_schedule: str | None
    current_schedule: str | None
    current_priority: Literal["low", "normal", "high", "critical"] | None
    current_severity: Severity | None
    trigger: DagTriggerResponse | None
    source_status: DagSourceStatus


class DagListResponse(RootModel[list[DagSummaryResponse]]):
    pass


class DagTaskResponse(ApiModel):
    name: str
    kind: Literal["pipeline", "script"]
    depends_on: list[str]
    retries: int = Field(ge=0)
    retry_delay_seconds: float = Field(ge=0)
    retry_backoff_multiplier: float = Field(ge=1)


class DagDetailResponse(DagSummaryResponse):
    declared_name: str
    on_failure: FailurePolicy
    tasks: list[DagTaskResponse]


class RunSummaryResponse(ApiModel):
    id: int
    dag_name: str
    status: DagRunStatus
    trigger_reason: str
    severity: Severity
    started_at: datetime
    ended_at: datetime | None


class RunListResponse(RootModel[list[RunSummaryResponse]]):
    pass


class TaskAttemptResponse(ApiModel):
    attempt_number: int = Field(ge=1)
    status: TaskAttemptStatus
    started_at: datetime
    ended_at: datetime | None
    error_summary: str | None


class TaskRunResponse(ApiModel):
    name: str
    status: TaskRunStatus
    started_at: datetime | None
    ended_at: datetime | None
    error_summary: str | None
    attempts: list[TaskAttemptResponse]


class RunDetailResponse(RunSummaryResponse):
    on_failure: FailurePolicy
    error_summary: str | None
    tasks: list[TaskRunResponse]


class SchedulerStatusResponse(ApiModel):
    running: bool
    pid: int | None
    started_at: datetime | None
