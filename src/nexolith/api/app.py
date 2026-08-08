"""FastAPI adapter for Nexolith's typed query and action services."""

import sqlite3
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlsplit

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nexolith import __version__
from nexolith.api import API_VERSION
from nexolith.api.models import (
    ApiErrorCode,
    ApiErrorDetail,
    ApiInfoResponse,
    ConfirmedActionRequest,
    DagDetailResponse,
    DagListResponse,
    DagRegistrationResponse,
    DagRegistrationStatus,
    DagRunActionResponse,
    ErrorResponse,
    RegisterDagRequest,
    RunDetailResponse,
    RunListResponse,
    SchedulerStartResponse,
    SchedulerStatusResponse,
    SchedulerStopResponse,
)
from nexolith.api.service import (
    ApiDagActionService,
    ApiQueryError,
    ApiQueryService,
    SchedulerStatusQuery,
)
from nexolith.scheduler import (
    SchedulerControlError,
    SchedulerStartResult,
    SchedulerStartState,
    SchedulerStopResult,
    SchedulerStopState,
    query_scheduler_status,
    start_scheduler_process,
    stop_scheduler_process,
)
from nexolith.state import StateStore

StoreFactory = Callable[[], StateStore]
OperationResult = TypeVar("OperationResult")
SchedulerStartAction = Callable[[], SchedulerStartResult]
SchedulerStopAction = Callable[[], SchedulerStopResult]

_ACTION_PATHS = {
    "/api/v1/dags/registrations",
    "/api/v1/scheduler/start",
    "/api/v1/scheduler/stop",
}
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
_TAGS = [
    {"name": "meta", "description": "Stable API contract metadata."},
    {"name": "dags", "description": "Registered DAG queries and explicit actions."},
    {"name": "runs", "description": "Persisted DAG execution history."},
    {"name": "scheduler", "description": "Identity-verified scheduler queries and actions."},
]
_COMMON_ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Host is not allowed."},
    422: {"model": ErrorResponse, "description": "Invalid request."},
    500: {"model": ErrorResponse, "description": "Internal service error."},
    503: {"model": ErrorResponse, "description": "State or scheduler identity unavailable."},
}
_ACTION_ERRORS: dict[int | str, dict[str, object]] = {
    **_COMMON_ERRORS,
    403: {"model": ErrorResponse, "description": "Browser origin is not allowed."},
    405: {"model": ErrorResponse, "description": "Mutation method is not allowed."},
    415: {"model": ErrorResponse, "description": "JSON request body required."},
}


def _error_response(status_code: int, code: ApiErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(detail=ApiErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _is_action_path(path: str) -> bool:
    return path in _ACTION_PATHS or (path.startswith("/api/v1/dags/") and path.endswith("/runs"))


def _origin_matches_host(origin: str, host: str, scheme: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == scheme
            and parsed.netloc.casefold() == host.casefold()
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def create_app(
    *,
    store_factory: StoreFactory = StateStore,
    scheduler_status_query: SchedulerStatusQuery = query_scheduler_status,
    scheduler_start_action: SchedulerStartAction = start_scheduler_process,
    scheduler_stop_action: SchedulerStopAction = stop_scheduler_process,
    allowed_hosts: tuple[str, ...] = _DEFAULT_ALLOWED_HOSTS,
) -> FastAPI:
    """Create an API without opening state or inspecting process ownership."""
    app = FastAPI(
        title="Nexolith API",
        summary="Typed DAG, run, and scheduler queries and explicit actions.",
        description=(
            "A versioned contract for trusted local clients. NXL-112 adds explicit POST "
            "actions but no authentication or authorization; bind only to localhost or a "
            "trusted network. Action requests are never retried automatically."
        ),
        version=__version__,
        openapi_tags=_TAGS,
    )

    async def execute_store(operation: Callable[[StateStore], OperationResult]) -> OperationResult:
        def run_operation() -> OperationResult:
            try:
                store = store_factory()
            except (OSError, sqlite3.Error) as exc:
                raise ApiQueryError(
                    ApiErrorCode.STATE_UNAVAILABLE,
                    503,
                    "Nexolith state is unavailable.",
                ) from exc
            try:
                return operation(store)
            finally:
                store.close()

        return await run_in_threadpool(run_operation)

    async def execute_query(
        operation: Callable[[ApiQueryService], OperationResult],
    ) -> OperationResult:
        return await execute_store(
            lambda store: operation(ApiQueryService(store, scheduler_status_query))
        )

    @app.middleware("http")
    async def protect_local_actions(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        host_name = request.url.hostname
        if host_name is None or host_name.casefold() not in {
            allowed.casefold() for allowed in allowed_hosts
        }:
            return _error_response(400, ApiErrorCode.HOST_NOT_ALLOWED, "Host is not allowed.")
        if request.method == "POST" and _is_action_path(request.url.path):
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json" and not media_type.endswith("+json"):
                return _error_response(
                    415,
                    ApiErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    "Action requests require a JSON body.",
                )
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin is not None and not _origin_matches_host(origin, host, request.url.scheme):
                return _error_response(
                    403,
                    ApiErrorCode.ORIGIN_NOT_ALLOWED,
                    "Browser origin is not allowed.",
                )
        return await call_next(request)

    @app.exception_handler(ApiQueryError)
    async def handle_query_error(_request: Request, exc: ApiQueryError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            422,
            ApiErrorCode.REQUEST_VALIDATION_ERROR,
            "Request parameters are invalid.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 405:
            return _error_response(405, ApiErrorCode.METHOD_NOT_ALLOWED, "Method not allowed.")
        return _error_response(
            exc.status_code,
            ApiErrorCode.RESOURCE_NOT_FOUND,
            "Resource not found.",
        )

    @app.exception_handler(sqlite3.Error)
    async def handle_state_error(_request: Request, _exc: sqlite3.Error) -> JSONResponse:
        return _error_response(
            503, ApiErrorCode.STATE_UNAVAILABLE, "Nexolith state is unavailable."
        )

    @app.exception_handler(Exception)
    async def handle_internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(500, ApiErrorCode.INTERNAL_ERROR, "Internal service error.")

    @app.get(
        "/api/v1",
        response_model=ApiInfoResponse,
        responses=_COMMON_ERRORS,
        tags=["meta"],
        operation_id="get_api_info",
        summary="Get API contract metadata",
        description="Returns stable API and package versions without host-specific data.",
    )
    async def get_api_info() -> ApiInfoResponse:
        return ApiInfoResponse(
            api_version=API_VERSION,
            package_version=__version__,
            read_only=False,
            actions_enabled=True,
        )

    @app.get(
        "/api/v1/dags",
        response_model=DagListResponse,
        responses=_COMMON_ERRORS,
        tags=["dags"],
        operation_id="list_dags",
        summary="List registered DAGs",
        description="Lists registered DAGs with safe current declarative metadata.",
    )
    async def list_dags() -> DagListResponse:
        return DagListResponse(await execute_query(lambda service: service.list_dags()))

    @app.post(
        "/api/v1/dags/registrations",
        response_model=DagRegistrationResponse,
        responses={
            **_ACTION_ERRORS,
            201: {"model": DagRegistrationResponse, "description": "DAG registered."},
            409: {"model": ErrorResponse, "description": "DAG configuration invalid."},
        },
        tags=["dags"],
        operation_id="register_dag",
        summary="Register a DAG",
        description="Validates and registers a DAG without executing any task.",
    )
    async def register_dag_action(
        payload: RegisterDagRequest,
        response: Response,
    ) -> DagRegistrationResponse:
        result = await execute_store(
            lambda store: ApiDagActionService(store).register(
                payload.source_path, force=payload.force
            )
        )
        if result.status is DagRegistrationStatus.CREATED:
            response.status_code = 201
        return result

    @app.get(
        "/api/v1/dags/registrations",
        response_model=ErrorResponse,
        status_code=405,
        include_in_schema=False,
    )
    async def reject_registration_get() -> ErrorResponse:
        return ErrorResponse(
            detail=ApiErrorDetail(
                code=ApiErrorCode.METHOD_NOT_ALLOWED,
                message="Method not allowed.",
            )
        )

    @app.get(
        "/api/v1/dags/{dag_name}",
        response_model=DagDetailResponse,
        responses={
            **_COMMON_ERRORS,
            404: {"model": ErrorResponse, "description": "DAG not found."},
            409: {"model": ErrorResponse, "description": "DAG source unavailable or invalid."},
        },
        tags=["dags"],
        operation_id="get_dag",
        summary="Get a registered DAG",
        description="Reads the current registered DAG source and returns its safe structure.",
    )
    async def get_dag(
        dag_name: str = Path(min_length=1, description="Registered DAG name."),
    ) -> DagDetailResponse:
        return await execute_query(lambda service: service.get_dag(dag_name))

    @app.post(
        "/api/v1/dags/{dag_name}/runs",
        response_model=DagRunActionResponse,
        status_code=201,
        responses={
            **_ACTION_ERRORS,
            404: {"model": ErrorResponse, "description": "DAG not found."},
            409: {"model": ErrorResponse, "description": "DAG source unavailable or invalid."},
        },
        tags=["runs"],
        operation_id="trigger_dag_run",
        summary="Trigger a registered DAG run",
        description=(
            "Executes synchronously in a worker thread and returns the persisted terminal run. "
            "A retry after a lost response can create another run."
        ),
    )
    async def trigger_dag_run(
        payload: ConfirmedActionRequest,
        dag_name: str = Path(min_length=1, description="Registered DAG name."),
    ) -> DagRunActionResponse:
        del payload
        return await execute_store(lambda store: ApiDagActionService(store).trigger(dag_name))

    @app.get(
        "/api/v1/runs",
        response_model=RunListResponse,
        responses=_COMMON_ERRORS,
        tags=["runs"],
        operation_id="list_runs",
        summary="List recent DAG runs",
        description="Returns a bounded newest-first view of persisted DAG runs.",
    )
    async def list_runs(
        limit: int = Query(
            default=20,
            ge=1,
            le=100,
            description="Maximum number of recent runs to return.",
        ),
    ) -> RunListResponse:
        return RunListResponse(await execute_query(lambda service: service.list_runs(limit)))

    @app.get(
        "/api/v1/runs/{run_id}",
        response_model=RunDetailResponse,
        responses={
            **_COMMON_ERRORS,
            404: {"model": ErrorResponse, "description": "Run not found."},
        },
        tags=["runs"],
        operation_id="get_run",
        summary="Get DAG run history",
        description="Returns the persisted run, task, and retry-attempt history.",
    )
    async def get_run(
        run_id: int = Path(ge=1, description="Persisted DAG run identifier."),
    ) -> RunDetailResponse:
        return await execute_query(lambda service: service.get_run(run_id))

    @app.get(
        "/api/v1/scheduler",
        response_model=SchedulerStatusResponse,
        responses=_COMMON_ERRORS,
        tags=["scheduler"],
        operation_id="get_scheduler_status",
        summary="Get scheduler status",
        description="Checks PID and creation-time ownership without changing scheduler state.",
    )
    async def get_scheduler_status() -> SchedulerStatusResponse:
        return await execute_query(lambda service: service.get_scheduler_status())

    @app.post(
        "/api/v1/scheduler/start",
        response_model=SchedulerStartResponse,
        responses={
            **_ACTION_ERRORS,
            409: {"model": ErrorResponse, "description": "Scheduler already running."},
        },
        tags=["scheduler"],
        operation_id="start_scheduler",
        summary="Start the scheduler process",
        description="Starts and verifies a detached scheduler using the foreground CLI entrypoint.",
    )
    async def start_scheduler(payload: ConfirmedActionRequest) -> SchedulerStartResponse:
        del payload
        try:
            result = await run_in_threadpool(scheduler_start_action)
        except SchedulerControlError as exc:
            raise ApiQueryError(
                ApiErrorCode.SCHEDULER_CONTROL_UNAVAILABLE,
                503,
                "Scheduler control is unavailable.",
            ) from exc
        if result.state is SchedulerStartState.ALREADY_RUNNING:
            raise ApiQueryError(
                ApiErrorCode.SCHEDULER_ALREADY_RUNNING,
                409,
                "Scheduler is already running.",
            )
        return SchedulerStartResponse(state="started", pid=result.pid)

    @app.post(
        "/api/v1/scheduler/stop",
        response_model=SchedulerStopResponse,
        responses={
            **_ACTION_ERRORS,
            202: {"model": SchedulerStopResponse, "description": "Termination remains uncertain."},
        },
        tags=["scheduler"],
        operation_id="stop_scheduler",
        summary="Stop the scheduler process",
        description=(
            "Terminates only the identity-verified pidfile owner and never unlinks as observer."
        ),
    )
    async def stop_scheduler(
        payload: ConfirmedActionRequest,
        response: Response,
    ) -> SchedulerStopResponse:
        del payload
        try:
            result = await run_in_threadpool(scheduler_stop_action)
        except SchedulerControlError as exc:
            raise ApiQueryError(
                ApiErrorCode.SCHEDULER_CONTROL_UNAVAILABLE,
                503,
                "Scheduler control is unavailable.",
            ) from exc
        if result.state is SchedulerStopState.UNCERTAIN:
            response.status_code = 202
        return SchedulerStopResponse(
            state=result.state.value,
            pid=result.pid,
            forced=result.forced,
        )

    return app
