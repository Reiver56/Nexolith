from __future__ import annotations

import json
import sqlite3
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from nexolith.api.app import create_app
from nexolith.api.server import (
    ApiDependenciesUnavailable,
    ApiServerStartupError,
    run_api_server,
)
from nexolith.cli.app import app as cli_app
from nexolith.process_identity import (
    ProcessIdentity,
    ProcessIdentityLookup,
)
from nexolith.scheduler import (
    SchedulerQueryState,
    SchedulerStatusSnapshot,
    query_scheduler_status,
    write_pidfile,
)
from nexolith.state import StateStore

_IDENTITY = ProcessIdentity(pid=4242, create_time_ns=1_700_000_000_000_000_000)
_SECRET = "NXL_SENTINEL_PASSWORD_7e3a"


def _dag_document(*, schedule: str = "5m") -> str:
    return f"""
name: orders
schedule: {schedule}
priority: high
severity: critical
trigger:
  on_success_of: [ingest]
on_failure: block
tasks:
  - name: extract
    pipeline: {_SECRET}.yaml
    retries: 2
    retry_delay_seconds: 1
  - name: publish
    script: {_SECRET}.py
    depends_on: [extract]
"""


@pytest.fixture
def api_state(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    database_path = tmp_path / "state.db"
    dag_path = tmp_path / _SECRET / "orders.yaml"
    dag_path.parent.mkdir()
    dag_path.write_text(_dag_document(), encoding="utf-8")
    store = StateStore(database_path, process_identity=lambda: _IDENTITY)
    store.register_dag("orders", dag_path, "1h")
    store.close()
    yield database_path, dag_path


def _store_factory(database_path: Path) -> Callable[[], StateStore]:
    return lambda: StateStore(database_path, process_identity=lambda: _IDENTITY)


def _client(
    database_path: Path,
    *,
    scheduler_status_query: Callable[[], SchedulerStatusSnapshot] | None = None,
) -> TestClient:
    scheduler_query = scheduler_status_query or (
        lambda: SchedulerStatusSnapshot(SchedulerQueryState.NOT_RUNNING)
    )
    return TestClient(
        create_app(
            store_factory=_store_factory(database_path),
            scheduler_status_query=scheduler_query,
        )
    )


def test_application_creation_does_not_open_state_store() -> None:
    opened = False

    def fail_if_opened() -> StateStore:
        nonlocal opened
        opened = True
        raise AssertionError("store opened during app creation")

    create_app(store_factory=fail_if_opened)

    assert opened is False


def test_docs_metadata_and_semantic_openapi_contract(api_state: tuple[Path, Path]) -> None:
    database_path, _ = api_state
    application = create_app(store_factory=_store_factory(database_path))
    client = TestClient(application)

    assert client.get("/docs").status_code == 200
    metadata = client.get("/api/v1")
    assert metadata.status_code == 200
    assert metadata.json() == {
        "api_version": "v1",
        "package_version": "0.3.0",
        "read_only": True,
    }

    schema = client.get("/openapi.json").json()
    expected_operations = {
        ("/api/v1", "get"): "get_api_info",
        ("/api/v1/dags", "get"): "list_dags",
        ("/api/v1/dags/{dag_name}", "get"): "get_dag",
        ("/api/v1/runs", "get"): "list_runs",
        ("/api/v1/runs/{run_id}", "get"): "get_run",
        ("/api/v1/scheduler", "get"): "get_scheduler_status",
    }
    expected_success_schemas = {
        "/api/v1": {"$ref": "#/components/schemas/ApiInfoResponse"},
        "/api/v1/dags": {"$ref": "#/components/schemas/DagListResponse"},
        "/api/v1/dags/{dag_name}": {"$ref": "#/components/schemas/DagDetailResponse"},
        "/api/v1/runs": {"$ref": "#/components/schemas/RunListResponse"},
        "/api/v1/runs/{run_id}": {"$ref": "#/components/schemas/RunDetailResponse"},
        "/api/v1/scheduler": {"$ref": "#/components/schemas/SchedulerStatusResponse"},
    }
    assert {
        (path, method): operation["operationId"]
        for path, item in schema["paths"].items()
        for method, operation in item.items()
    } == expected_operations
    assert {tag["name"] for tag in schema["tags"]} == {"meta", "dags", "runs", "scheduler"}
    for path, method in expected_operations:
        operation = schema["paths"][path][method]
        assert (
            operation["responses"]["200"]["content"]["application/json"]["schema"]
            == (expected_success_schemas[path])
        )
        assert operation["tags"]
        for code, response in operation["responses"].items():
            if code != "200" and "content" in response:
                assert response["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorResponse"
                }

    limit_parameter = schema["paths"]["/api/v1/runs"]["get"]["parameters"][0]
    assert limit_parameter["name"] == "limit"
    assert limit_parameter["in"] == "query"
    assert limit_parameter["schema"] == {
        "type": "integer",
        "maximum": 100,
        "minimum": 1,
        "description": "Maximum number of recent runs to return.",
        "default": 20,
        "title": "Limit",
    }
    dag_parameter = schema["paths"]["/api/v1/dags/{dag_name}"]["get"]["parameters"][0]
    assert dag_parameter["name"] == "dag_name"
    assert dag_parameter["required"] is True
    assert dag_parameter["schema"]["type"] == "string"
    run_parameter = schema["paths"]["/api/v1/runs/{run_id}"]["get"]["parameters"][0]
    assert run_parameter["name"] == "run_id"
    assert run_parameter["required"] is True
    assert run_parameter["schema"]["type"] == "integer"

    mutating_methods = {"post", "put", "patch", "delete"}
    assert not any(mutating_methods.intersection(item) for item in schema["paths"].values())
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    schema_text = json.dumps(schema)
    component_text = json.dumps(schema["components"]["schemas"]).lower()
    assert _SECRET not in schema_text
    assert str(database_path) not in schema_text
    assert "examples" not in component_text
    forbidden_component_terms = {
        "source_path",
        "owner_pid",
        "owner_create_time_ns",
        "parameters",
        "connection_url",
        "password",
        "api_key",
        "token",
        "traceback",
        "exception",
    }
    assert all(term not in component_text for term in forbidden_component_terms)
    assert "c:\\users\\" not in component_text
    assert "/home/" not in component_text
    assert "/users/" not in component_text


def test_openapi_is_deterministic_for_equivalent_configuration(tmp_path: Path) -> None:
    first = create_app(store_factory=_store_factory(tmp_path / "first.db")).openapi()
    second = create_app(store_factory=_store_factory(tmp_path / "second.db")).openapi()

    assert first == second


def test_dag_list_and_detail_read_current_safe_configuration(
    api_state: tuple[Path, Path],
) -> None:
    database_path, dag_path = api_state
    client = _client(database_path)

    listed = client.get("/api/v1/dags")
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "name": "orders",
            "enabled": True,
            "registered_schedule": "1h",
            "current_schedule": "5m",
            "current_priority": "high",
            "current_severity": "critical",
            "trigger": {"on_success_of": ["ingest"]},
            "source_status": "available",
        }
    ]

    detail = client.get("/api/v1/dags/orders")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["declared_name"] == "orders"
    assert payload["on_failure"] == "block"
    assert payload["tasks"] == [
        {
            "name": "extract",
            "kind": "pipeline",
            "depends_on": [],
            "retries": 2,
            "retry_delay_seconds": 1.0,
            "retry_backoff_multiplier": 1.0,
        },
        {
            "name": "publish",
            "kind": "script",
            "depends_on": ["extract"],
            "retries": 0,
            "retry_delay_seconds": 0.0,
            "retry_backoff_multiplier": 1.0,
        },
    ]
    assert _SECRET not in detail.text
    assert str(dag_path) not in detail.text

    dag_path.write_text(_dag_document(schedule="30m"), encoding="utf-8")
    assert client.get("/api/v1/dags/orders").json()["current_schedule"] == "30m"


def test_missing_invalid_and_unknown_dags_return_safe_errors(
    api_state: tuple[Path, Path],
) -> None:
    database_path, dag_path = api_state
    client = _client(database_path)

    dag_path.unlink()
    listed_missing = client.get("/api/v1/dags").json()[0]
    assert listed_missing["source_status"] == "missing"
    assert _SECRET not in json.dumps(listed_missing)
    missing = client.get("/api/v1/dags/orders")
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "dag_source_missing"
    assert _SECRET not in missing.text

    dag_path.write_text(f"not: a DAG\nsecret: {_SECRET}\n", encoding="utf-8")
    invalid = client.get("/api/v1/dags/orders")
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "dag_configuration_invalid"
    assert _SECRET not in invalid.text

    unknown = client.get("/api/v1/dags/unknown")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "dag_not_found"


def test_run_list_detail_statuses_attempts_and_redaction(api_state: tuple[Path, Path]) -> None:
    database_path, _ = api_state
    store = StateStore(database_path, process_identity=lambda: _IDENTITY)
    succeeded = store.start_dag_run("orders", ["task"], trigger_reason="manual")
    store.start_task_run(succeeded, "task")
    store.complete_task_run(succeeded, "task", success=True)
    store.complete_dag_run(succeeded, success=True)

    failed = store.start_dag_run(
        "orders",
        ["failed", "skipped", "blocked"],
        trigger_reason="schedule:5m",
        on_failure="block",
        severity="critical",
    )
    store.start_task_run(failed, "failed")
    attempt = store.start_task_attempt(failed, "failed", 1)
    store.complete_task_attempt(attempt, success=False, error=f"token={_SECRET}")
    store.complete_task_run(failed, "failed", success=False, error=f"C:\\Users\\{_SECRET}")
    store.skip_task_run(failed, "skipped")
    store.block_task_run(failed, "blocked")
    store.complete_dag_run(failed, success=False, error=f"postgres://{_SECRET}@host/db")

    interrupted = store.start_dag_run("orders", ["task"], trigger_reason="manual")
    reconciliation = store.interrupt_abandoned_dag_runs(
        lambda _pid: ProcessIdentityLookup.not_found()
    )
    assert reconciliation.interrupted_run_ids == (interrupted,)
    running = store.start_dag_run("orders", ["task"], trigger_reason="manual")
    store.close()

    client = _client(database_path)
    listed = client.get("/api/v1/runs?limit=2")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [running, interrupted]
    assert [item["status"] for item in listed.json()] == ["running", "interrupted"]
    all_statuses = {item["status"] for item in client.get("/api/v1/runs?limit=10").json()}
    assert all_statuses == {"running", "interrupted", "failed", "succeeded"}

    detail = client.get(f"/api/v1/runs/{failed}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["status"] == "failed"
    assert payload["error_summary"] == "DAG execution did not complete successfully."
    assert {task["status"] for task in payload["tasks"]} == {"failed", "skipped", "blocked"}
    failed_task = next(task for task in payload["tasks"] if task["name"] == "failed")
    assert failed_task["attempts"][0]["status"] == "failed"
    assert failed_task["attempts"][0]["error_summary"] == "Task attempt failed."
    assert _SECRET not in detail.text

    assert client.get("/api/v1/runs/999999").status_code == 404
    invalid_limit = client.get("/api/v1/runs?limit=101")
    assert invalid_limit.status_code == 422
    assert invalid_limit.json()["detail"]["code"] == "request_validation_error"


@pytest.mark.parametrize("owner_state", ["dead", "reused"])
def test_scheduler_status_fails_closed_without_changing_pidfile(
    tmp_path: Path, owner_state: str
) -> None:
    database_path = tmp_path / "state.db"
    pidfile = tmp_path / "scheduler.pid"
    write_pidfile(
        pidfile,
        4242,
        "2026-08-08T00:00:00+00:00",
        identity_provider=lambda _pid: ProcessIdentityLookup.found(_IDENTITY),
    )
    before = pidfile.read_bytes()
    lookup = (
        (lambda _pid: ProcessIdentityLookup.not_found())
        if owner_state == "dead"
        else (
            lambda _pid: ProcessIdentityLookup.found(
                ProcessIdentity(pid=4242, create_time_ns=_IDENTITY.create_time_ns + 1)
            )
        )
    )

    def scheduler_query() -> SchedulerStatusSnapshot:
        return query_scheduler_status(pidfile, identity_provider=lookup)

    response = _client(database_path, scheduler_status_query=scheduler_query).get(
        "/api/v1/scheduler"
    )

    assert response.status_code == 200
    assert response.json() == {"running": False, "pid": None, "started_at": None}
    assert pidfile.read_bytes() == before


def test_scheduler_status_reports_only_identity_verified_owner(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    pidfile = tmp_path / "scheduler.pid"
    write_pidfile(
        pidfile,
        4242,
        "2026-08-08T00:00:00+00:00",
        identity_provider=lambda _pid: ProcessIdentityLookup.found(_IDENTITY),
    )

    def matching_status() -> SchedulerStatusSnapshot:
        return query_scheduler_status(
            pidfile,
            identity_provider=lambda _pid: ProcessIdentityLookup.found(_IDENTITY),
        )

    before = pidfile.read_bytes()
    client = _client(database_path, scheduler_status_query=matching_status)

    assert client.get("/api/v1/scheduler").json() == {
        "running": True,
        "pid": 4242,
        "started_at": "2026-08-08T00:00:00Z",
    }
    assert pidfile.read_bytes() == before

    def unknown_status() -> SchedulerStatusSnapshot:
        return query_scheduler_status(
            pidfile,
            identity_provider=lambda _pid: ProcessIdentityLookup.access_denied(),
        )

    unknown_client = _client(
        tmp_path / "other.db",
        scheduler_status_query=unknown_status,
    )
    unavailable = unknown_client.get("/api/v1/scheduler")
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "scheduler_identity_unavailable"
    assert "access_denied" not in unavailable.text
    assert pidfile.read_bytes() == before


def test_request_scoped_store_stays_on_one_thread_and_closes(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"

    class TrackingStore(StateStore):
        def __init__(self) -> None:
            super().__init__(database_path, process_identity=lambda: _IDENTITY)
            self.created_thread = threading.get_ident()
            self.query_threads: list[int] = []
            self.closed = False

        def list_dags(self) -> list[object]:  # type: ignore[override]
            self.query_threads.append(threading.get_ident())
            return []

        def close(self) -> None:
            self.closed = True
            super().close()

    created: list[TrackingStore] = []

    def factory() -> StateStore:
        store = TrackingStore()
        created.append(store)
        return store

    client = TestClient(create_app(store_factory=factory))
    assert client.get("/api/v1/dags").status_code == 200
    assert client.get("/api/v1/dags").status_code == 200

    assert len(created) == 2
    assert len({id(store) for store in created}) == 2
    assert all(store.query_threads == [store.created_thread] for store in created)
    assert all(store.closed for store in created)


def test_unavailable_state_returns_typed_safe_error() -> None:
    def unavailable() -> StateStore:
        raise sqlite3.OperationalError(_SECRET)

    response = TestClient(create_app(store_factory=unavailable)).get("/api/v1/dags")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "state_unavailable"
    assert _SECRET not in response.text


def test_api_cli_delegates_defaults_and_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        sys.modules["nexolith.cli.app"],
        "run_api_server",
        lambda host, port: calls.append((host, port)),
    )
    runner = CliRunner()

    default = runner.invoke(cli_app, ["api", "start"])
    custom = runner.invoke(cli_app, ["api", "start", "--host", "0.0.0.0", "--port", "9000"])

    assert default.exit_code == 0
    assert custom.exit_code == 0
    assert calls == [("127.0.0.1", 8765), ("0.0.0.0", 9000)]
    assert "no authentication" in custom.stderr


@pytest.mark.parametrize(
    "error",
    [
        ApiDependenciesUnavailable("API dependencies are unavailable."),
        ApiServerStartupError("API address is already in use."),
    ],
)
def test_api_cli_expected_startup_errors_are_clean(
    monkeypatch: pytest.MonkeyPatch, error: RuntimeError
) -> None:
    def fail(_host: str, _port: int) -> None:
        raise error

    monkeypatch.setattr(sys.modules["nexolith.cli.app"], "run_api_server", fail)

    result = CliRunner().invoke(cli_app, ["api", "start"])

    assert result.exit_code == 1
    assert str(error) in result.stderr
    assert "Traceback" not in result.stderr


def test_api_server_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> object:
        error = ModuleNotFoundError("No module named 'uvicorn'")
        error.name = "uvicorn"
        raise error

    monkeypatch.setattr("nexolith.api.server.importlib.import_module", missing)
    with pytest.raises(ApiDependenciesUnavailable, match="'api' extra"):
        run_api_server("127.0.0.1", 8765)


def test_api_server_redacts_bind_error() -> None:

    def bind_error(_application: object, *, host: str, port: int) -> None:
        raise OSError(_SECRET)

    with pytest.raises(ApiServerStartupError, match="address may already be in use") as caught:
        run_api_server(
            "127.0.0.1",
            8765,
            runner=bind_error,
            app_loader=object,
        )
    assert _SECRET not in str(caught.value)
