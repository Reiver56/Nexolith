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
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    HOST_NOT_ALLOWED = "host_not_allowed"
    RESOURCE_NOT_FOUND = "resource_not_found"
    SCHEDULER_ALREADY_RUNNING = "scheduler_already_running"
    SCHEDULER_CONTROL_UNAVAILABLE = "scheduler_control_unavailable"
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
    read_only: bool
    actions_enabled: Literal[True]


class ConfirmedActionRequest(ApiModel):
    confirm: Literal[True]


class RegisterDagRequest(ApiModel):
    source_path: str = Field(min_length=1)
    force: bool = False


class DagRegistrationStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class DagRegistrationResponse(ApiModel):
    dag_name: str
    status: DagRegistrationStatus
    enabled: bool
    schedule: str | None


class DagRunActionResponse(ApiModel):
    run_id: int = Field(ge=1)
    dag_name: str
    status: DagRunStatus


class SchedulerStartResponse(ApiModel):
    state: Literal["started"]
    pid: int = Field(ge=1)


class SchedulerStopResponse(ApiModel):
    state: Literal["stopped", "not_running", "uncertain"]
    pid: int | None = Field(default=None, ge=1)
    forced: bool


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


class DagGraphTaskResponse(ApiModel):
    name: str
    depends_on: list[str]
    status: TaskRunStatus | None


class DagGraphHistoricalTaskResponse(ApiModel):
    name: str
    status: TaskRunStatus


class DagGraphRunResponse(ApiModel):
    id: int = Field(ge=1)
    status: DagRunStatus
    started_at: datetime
    ended_at: datetime | None


class DagGraphResponse(ApiModel):
    name: str
    trigger: DagTriggerResponse | None
    tasks: list[DagGraphTaskResponse]
    latest_run: DagGraphRunResponse | None
    unmapped_task_history: list[DagGraphHistoricalTaskResponse]


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
