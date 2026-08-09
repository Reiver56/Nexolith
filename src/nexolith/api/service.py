"""UI-independent read queries backing the monitoring API."""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from nexolith.api.models import (
    ApiErrorCode,
    DagDetailResponse,
    DagGraphHistoricalTaskResponse,
    DagGraphResponse,
    DagGraphRunResponse,
    DagGraphTaskResponse,
    DagRegistrationResponse,
    DagRegistrationStatus,
    DagRunActionResponse,
    DagScheduleActionResponse,
    DagSourceStatus,
    DagSummaryResponse,
    DagTaskResponse,
    DagTaskSourceResponse,
    DagTriggerResponse,
    FailurePolicy,
    RunDetailResponse,
    RunSummaryResponse,
    SchedulerStatusResponse,
    Severity,
    TaskAttemptResponse,
    TaskRetryResponse,
    TaskRunResponse,
)
from nexolith.application.actions import (
    DagActionService,
    RegisteredDagConfigurationError,
    RegisteredDagNotFoundError,
    RegisteredDagSourceMissingError,
)
from nexolith.dag.models import DagConfig
from nexolith.dag.validator import read_dag_config
from nexolith.exceptions import ConfigurationError
from nexolith.scheduler import SchedulerQueryState, SchedulerStatusSnapshot
from nexolith.state import DagRecord, DagRunRecord, StateStore

SchedulerStatusQuery = Callable[[], SchedulerStatusSnapshot]
TASK_SOURCE_SIZE_LIMIT_BYTES = 256 * 1024


class ApiQueryError(RuntimeError):
    def __init__(self, code: ApiErrorCode, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


class ApiQueryService:
    def __init__(self, store: StateStore, scheduler_status_query: SchedulerStatusQuery) -> None:
        self._store = store
        self._scheduler_status_query = scheduler_status_query

    def list_dags(self) -> list[DagSummaryResponse]:
        return [self._dag_summary(record) for record in self._store.list_dags()]

    def get_dag(self, dag_name: str) -> DagDetailResponse:
        record = self._store.get_dag(dag_name)
        if record is None:
            raise ApiQueryError(ApiErrorCode.DAG_NOT_FOUND, 404, "DAG not found.")
        config = self._required_dag_config(record)
        summary = self._summary_from_config(record, config)
        return DagDetailResponse(
            **summary.model_dump(),
            declared_name=config.name,
            on_failure=config.on_failure,
            tasks=[
                DagTaskResponse(
                    name=task.name,
                    kind="script" if task.script is not None else "pipeline",
                    depends_on=list(task.depends_on),
                    retries=task.retries,
                    retry_delay_seconds=task.retry_delay_seconds,
                    retry_backoff_multiplier=task.retry_backoff_multiplier,
                )
                for task in config.tasks
            ],
        )

    def get_dag_graph(self, dag_name: str) -> DagGraphResponse:
        record = self._store.get_dag(dag_name)
        if record is None:
            raise ApiQueryError(ApiErrorCode.DAG_NOT_FOUND, 404, "DAG not found.")
        config = self._required_dag_config(record)
        latest = self._store.latest_dag_run(dag_name)
        current_names = {task.name for task in config.tasks}
        task_history = self._store.list_task_runs(latest.id) if latest is not None else []
        statuses = {task.task_name: task.status for task in task_history}
        trigger = (
            DagTriggerResponse(on_success_of=list(config.trigger.on_success_of))
            if config.trigger is not None
            else None
        )
        return DagGraphResponse(
            name=record.name,
            enabled=record.enabled,
            schedule=record.schedule,
            trigger=trigger,
            tasks=[
                DagGraphTaskResponse(
                    name=task.name,
                    kind="script" if task.script is not None else "pipeline",
                    depends_on=list(task.depends_on),
                    status=statuses.get(task.name),
                )
                for task in config.tasks
            ],
            latest_run=(
                DagGraphRunResponse(
                    id=latest.id,
                    status=latest.status,
                    started_at=_timestamp(latest.started_at),
                    ended_at=_optional_timestamp(latest.ended_at),
                )
                if latest is not None
                else None
            ),
            unmapped_task_history=[
                DagGraphHistoricalTaskResponse(name=task.task_name, status=task.status)
                for task in task_history
                if task.task_name not in current_names
            ],
        )

    def get_task_source(self, dag_name: str, task_name: str) -> DagTaskSourceResponse:
        record = self._store.get_dag(dag_name)
        if record is None:
            raise ApiQueryError(ApiErrorCode.DAG_NOT_FOUND, 404, "DAG not found.")
        config = self._required_dag_config(record)
        task = next((candidate for candidate in config.tasks if candidate.name == task_name), None)
        if task is None:
            raise ApiQueryError(ApiErrorCode.TASK_NOT_FOUND, 404, "Task not found.")

        dag_path = Path(record.source_path)
        reference = task.script if task.script is not None else task.pipeline
        assert reference is not None
        source_path = Path(reference)
        if not source_path.is_absolute():
            source_path = dag_path.parent / source_path
        source, source_size = _read_task_source(source_path)

        latest = self._store.latest_dag_run(dag_name)
        latest_status = None
        if latest is not None:
            latest_status = next(
                (
                    persisted.status
                    for persisted in self._store.list_task_runs(latest.id)
                    if persisted.task_name == task_name
                ),
                None,
            )
        is_script = task.script is not None
        return DagTaskSourceResponse(
            dag_name=record.name,
            task_name=task.name,
            kind="script" if is_script else "pipeline",
            depends_on=list(task.depends_on),
            latest_status=latest_status,
            retry=TaskRetryResponse(
                retries=task.retries,
                retry_delay_seconds=task.retry_delay_seconds,
                retry_backoff_multiplier=task.retry_backoff_multiplier,
            ),
            source_language="python" if is_script else "yaml",
            source=source,
            source_size_bytes=source_size,
        )

    def list_runs(self, limit: int) -> list[RunSummaryResponse]:
        return [self._run_summary(run) for run in self._store.list_recent_dag_runs(limit)]

    def get_run(self, run_id: int) -> RunDetailResponse:
        run = self._store.get_dag_run(run_id)
        if run is None:
            raise ApiQueryError(ApiErrorCode.RUN_NOT_FOUND, 404, "Run not found.")
        attempts_by_task: dict[str, list[TaskAttemptResponse]] = defaultdict(list)
        for attempt in self._store.list_run_attempts(run_id):
            attempts_by_task[attempt.task_name].append(
                TaskAttemptResponse(
                    attempt_number=attempt.attempt_number,
                    status=attempt.status,
                    started_at=_timestamp(attempt.started_at),
                    ended_at=_optional_timestamp(attempt.ended_at),
                    error_summary=_safe_error(attempt.error, "Task attempt failed."),
                )
            )
        summary = self._run_summary(run)
        return RunDetailResponse(
            **summary.model_dump(),
            on_failure=cast(FailurePolicy, run.on_failure),
            error_summary=_safe_error(run.error, "DAG execution did not complete successfully."),
            tasks=[
                TaskRunResponse(
                    name=task.task_name,
                    status=task.status,
                    started_at=_optional_timestamp(task.started_at),
                    ended_at=_optional_timestamp(task.ended_at),
                    error_summary=_safe_error(task.error, "Task execution failed."),
                    attempts=attempts_by_task[task.task_name],
                )
                for task in self._store.list_task_runs(run_id)
            ],
        )

    def get_scheduler_status(self) -> SchedulerStatusResponse:
        status = self._scheduler_status_query()
        if status.state is SchedulerQueryState.UNKNOWN:
            raise ApiQueryError(
                ApiErrorCode.SCHEDULER_IDENTITY_UNAVAILABLE,
                503,
                "Scheduler identity cannot be verified.",
            )
        if status.state is SchedulerQueryState.NOT_RUNNING:
            return SchedulerStatusResponse(running=False, pid=None, started_at=None)
        return SchedulerStatusResponse(
            running=True,
            pid=status.pid,
            started_at=_optional_timestamp(status.started_at),
        )

    def _dag_summary(self, record: DagRecord) -> DagSummaryResponse:
        path = Path(record.source_path)
        try:
            config = read_dag_config(path)
        except ConfigurationError:
            return DagSummaryResponse(
                name=record.name,
                enabled=record.enabled,
                registered_schedule=record.schedule,
                current_schedule=None,
                current_priority=None,
                current_severity=None,
                trigger=None,
                source_status=(
                    DagSourceStatus.MISSING if not path.is_file() else DagSourceStatus.INVALID
                ),
            )
        return self._summary_from_config(record, config)

    @staticmethod
    def _summary_from_config(record: DagRecord, config: DagConfig) -> DagSummaryResponse:
        trigger = (
            DagTriggerResponse(on_success_of=list(config.trigger.on_success_of))
            if config.trigger is not None
            else None
        )
        return DagSummaryResponse(
            name=record.name,
            enabled=record.enabled,
            registered_schedule=record.schedule,
            current_schedule=config.schedule,
            current_priority=config.priority,
            current_severity=config.severity,
            trigger=trigger,
            source_status=DagSourceStatus.AVAILABLE,
        )

    @staticmethod
    def _required_dag_config(record: DagRecord) -> DagConfig:
        path = Path(record.source_path)
        if not path.is_file():
            raise ApiQueryError(
                ApiErrorCode.DAG_SOURCE_MISSING,
                409,
                "The registered DAG source is unavailable.",
            )
        try:
            return read_dag_config(path)
        except ConfigurationError as exc:
            raise ApiQueryError(
                ApiErrorCode.DAG_CONFIGURATION_INVALID,
                409,
                "The registered DAG configuration is invalid.",
            ) from exc

    @staticmethod
    def _run_summary(run: DagRunRecord) -> RunSummaryResponse:
        return RunSummaryResponse(
            id=run.id,
            dag_name=run.dag_name,
            status=run.status,
            trigger_reason=run.trigger_reason,
            severity=cast(Severity, run.severity),
            started_at=_timestamp(run.started_at),
            ended_at=_optional_timestamp(run.ended_at),
        )


class ApiDagActionService:
    """Map shared DAG actions onto stable, secret-safe HTTP representations."""

    def __init__(self, store: StateStore) -> None:
        self._actions = DagActionService(store)

    def register(self, source_path: str, *, force: bool) -> DagRegistrationResponse:
        try:
            result = self._actions.register(Path(source_path), force=force)
        except ConfigurationError as exc:
            raise ApiQueryError(
                ApiErrorCode.DAG_CONFIGURATION_INVALID,
                409,
                "The DAG configuration is invalid.",
            ) from exc
        status = (
            DagRegistrationStatus.CREATED
            if result.created
            else DagRegistrationStatus.UPDATED
            if result.updated
            else DagRegistrationStatus.UNCHANGED
        )
        return DagRegistrationResponse(
            dag_name=result.record.name,
            status=status,
            enabled=result.record.enabled,
            schedule=result.record.schedule,
        )

    def trigger(self, dag_name: str) -> DagRunActionResponse:
        try:
            result = self._actions.trigger(dag_name)
        except RegisteredDagNotFoundError as exc:
            raise ApiQueryError(ApiErrorCode.DAG_NOT_FOUND, 404, "DAG not found.") from exc
        except RegisteredDagSourceMissingError as exc:
            raise ApiQueryError(
                ApiErrorCode.DAG_SOURCE_MISSING,
                409,
                "The registered DAG source is unavailable.",
            ) from exc
        except RegisteredDagConfigurationError as exc:
            raise ApiQueryError(
                ApiErrorCode.DAG_CONFIGURATION_INVALID,
                409,
                "The registered DAG configuration is invalid.",
            ) from exc
        return DagRunActionResponse(
            run_id=result.run_id,
            dag_name=result.dag_name,
            status=result.status,
        )

    def set_schedule_enabled(self, dag_name: str, *, enabled: bool) -> DagScheduleActionResponse:
        try:
            result = self._actions.set_schedule_enabled(dag_name, enabled=enabled)
        except RegisteredDagNotFoundError as exc:
            raise ApiQueryError(ApiErrorCode.DAG_NOT_FOUND, 404, "DAG not found.") from exc
        return DagScheduleActionResponse(
            dag_name=result.dag_name,
            schedule_status="scheduled" if result.enabled else "paused",
            enabled=result.enabled,
        )


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_timestamp(value: str | None) -> datetime | None:
    return _timestamp(value) if value is not None else None


def _safe_error(value: str | None, summary: str) -> str | None:
    return summary if value else None


def _read_task_source(path: Path) -> tuple[str, int]:
    try:
        with path.open("rb") as source_file:
            content = source_file.read(TASK_SOURCE_SIZE_LIMIT_BYTES + 1)
    except OSError as exc:
        raise ApiQueryError(
            ApiErrorCode.TASK_SOURCE_UNAVAILABLE,
            409,
            "The task source is unavailable.",
        ) from exc
    if len(content) > TASK_SOURCE_SIZE_LIMIT_BYTES:
        raise ApiQueryError(
            ApiErrorCode.TASK_SOURCE_TOO_LARGE,
            413,
            "The task source exceeds the 256 KiB display limit.",
        )
    if b"\x00" in content:
        raise ApiQueryError(
            ApiErrorCode.TASK_SOURCE_BINARY,
            415,
            "The task source is not plain text.",
        )
    try:
        return content.decode("utf-8"), len(content)
    except UnicodeDecodeError as exc:
        raise ApiQueryError(
            ApiErrorCode.TASK_SOURCE_INVALID_ENCODING,
            415,
            "The task source is not valid UTF-8.",
        ) from exc
