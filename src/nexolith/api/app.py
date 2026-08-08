"""FastAPI adapter for Nexolith's typed read-only query service."""

import sqlite3
from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import FastAPI, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from nexolith import __version__
from nexolith.api import API_VERSION
from nexolith.api.models import (
    ApiErrorCode,
    ApiErrorDetail,
    ApiInfoResponse,
    DagDetailResponse,
    DagListResponse,
    ErrorResponse,
    RunDetailResponse,
    RunListResponse,
    SchedulerStatusResponse,
)
from nexolith.api.service import ApiQueryError, ApiQueryService, SchedulerStatusQuery
from nexolith.scheduler import query_scheduler_status
from nexolith.state import StateStore

StoreFactory = Callable[[], StateStore]
QueryResult = TypeVar("QueryResult")

_TAGS = [
    {"name": "meta", "description": "Stable API contract metadata."},
    {"name": "dags", "description": "Registered DAG configuration views."},
    {"name": "runs", "description": "Persisted DAG execution history."},
    {"name": "scheduler", "description": "Identity-verified scheduler status."},
]
_COMMON_ERRORS: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorResponse, "description": "Invalid request."},
    500: {"model": ErrorResponse, "description": "Internal service error."},
    503: {"model": ErrorResponse, "description": "State or scheduler identity unavailable."},
}


def _error_response(status_code: int, code: ApiErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(detail=ApiErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(
    *,
    store_factory: StoreFactory = StateStore,
    scheduler_status_query: SchedulerStatusQuery = query_scheduler_status,
) -> FastAPI:
    """Create an API without opening a database or inspecting the scheduler."""
    app = FastAPI(
        title="Nexolith Monitoring API",
        summary="Read-only DAG, run, and scheduler monitoring.",
        description=(
            "A versioned read-only contract for trusted local monitoring clients. "
            "NXL-111 provides no authentication and no mutation operations."
        ),
        version=__version__,
        openapi_tags=_TAGS,
    )

    async def execute_query(
        operation: Callable[[ApiQueryService], QueryResult],
    ) -> QueryResult:
        def run_query() -> QueryResult:
            try:
                store = store_factory()
            except (OSError, sqlite3.Error) as exc:
                raise ApiQueryError(
                    ApiErrorCode.STATE_UNAVAILABLE,
                    503,
                    "Nexolith state is unavailable.",
                ) from exc
            try:
                return operation(ApiQueryService(store, scheduler_status_query))
            finally:
                store.close()

        return await run_in_threadpool(run_query)

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
        return ApiInfoResponse(api_version=API_VERSION, package_version=__version__, read_only=True)

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
        dag_name: Annotated[str, Path(min_length=1, description="Registered DAG name.")],
    ) -> DagDetailResponse:
        return await execute_query(lambda service: service.get_dag(dag_name))

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
        limit: Annotated[
            int,
            Query(ge=1, le=100, description="Maximum number of recent runs to return."),
        ] = 20,
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
        run_id: Annotated[int, Path(ge=1, description="Persisted DAG run identifier.")],
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

    return app
