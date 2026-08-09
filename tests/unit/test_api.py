from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient
from typer.testing import CliRunner

from nexolith.api.app import create_app
from nexolith.api.server import (
    ApiDependenciesUnavailable,
    ApiServerStartupError,
    _is_loopback_bind,
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
_BASE_URL = "http://127.0.0.1"


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
            task_source_access=True,
        ),
        base_url=_BASE_URL,
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
    client = TestClient(application, base_url=_BASE_URL)

    assert client.get("/docs").status_code == 200
    metadata = client.get("/api/v1")
    assert metadata.status_code == 200
    assert metadata.json() == {
        "api_version": "v1",
        "package_version": "0.3.0",
        "read_only": False,
        "actions_enabled": True,
    }

    schema = client.get("/openapi.json").json()
    expected_operations = {
        ("/api/v1", "get"): "get_api_info",
        ("/api/v1/dags", "get"): "list_dags",
        ("/api/v1/dags/registrations", "post"): "register_dag",
        ("/api/v1/dags/{dag_name}/graph", "get"): "get_dag_graph",
        ("/api/v1/task-details", "get"): "get_dag_task_source",
        ("/api/v1/dags/{dag_name}", "get"): "get_dag",
        ("/api/v1/dags/{dag_name}/runs", "post"): "trigger_dag_run",
        ("/api/v1/dag-schedules/pause", "post"): "pause_dag_schedule",
        ("/api/v1/dag-schedules/resume", "post"): "resume_dag_schedule",
        ("/api/v1/runs", "get"): "list_runs",
        ("/api/v1/runs/{run_id}", "get"): "get_run",
        ("/api/v1/scheduler", "get"): "get_scheduler_status",
        ("/api/v1/scheduler/start", "post"): "start_scheduler",
        ("/api/v1/scheduler/stop", "post"): "stop_scheduler",
    }
    expected_success_schemas = {
        ("/api/v1", "get"): ("200", "ApiInfoResponse"),
        ("/api/v1/dags", "get"): ("200", "DagListResponse"),
        ("/api/v1/dags/registrations", "post"): ("200", "DagRegistrationResponse"),
        ("/api/v1/dags/{dag_name}/graph", "get"): ("200", "DagGraphResponse"),
        ("/api/v1/task-details", "get"): ("200", "DagTaskSourceResponse"),
        ("/api/v1/dags/{dag_name}", "get"): ("200", "DagDetailResponse"),
        ("/api/v1/dags/{dag_name}/runs", "post"): ("201", "DagRunActionResponse"),
        ("/api/v1/dag-schedules/pause", "post"): ("200", "DagScheduleActionResponse"),
        ("/api/v1/dag-schedules/resume", "post"): ("200", "DagScheduleActionResponse"),
        ("/api/v1/runs", "get"): ("200", "RunListResponse"),
        ("/api/v1/runs/{run_id}", "get"): ("200", "RunDetailResponse"),
        ("/api/v1/scheduler", "get"): ("200", "SchedulerStatusResponse"),
        ("/api/v1/scheduler/start", "post"): ("200", "SchedulerStartResponse"),
        ("/api/v1/scheduler/stop", "post"): ("200", "SchedulerStopResponse"),
    }
    assert {
        (path, method): operation["operationId"]
        for path, item in schema["paths"].items()
        for method, operation in item.items()
    } == expected_operations
    assert {tag["name"] for tag in schema["tags"]} == {"meta", "dags", "runs", "scheduler"}
    for path, method in expected_operations:
        operation = schema["paths"][path][method]
        success_code, component = expected_success_schemas[(path, method)]
        assert operation["responses"][success_code]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{component}"
        }
        assert operation["tags"]
        for code, response in operation["responses"].items():
            if code not in {"200", "201", "202"} and "content" in response:
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

    mutation_operations = {
        (path, method)
        for path, item in schema["paths"].items()
        for method in {"post", "put", "patch", "delete"}.intersection(item)
    }
    assert mutation_operations == {
        ("/api/v1/dags/registrations", "post"),
        ("/api/v1/dags/{dag_name}/runs", "post"),
        ("/api/v1/dag-schedules/pause", "post"),
        ("/api/v1/dag-schedules/resume", "post"),
        ("/api/v1/scheduler/start", "post"),
        ("/api/v1/scheduler/stop", "post"),
    }
    assert schema["paths"]["/api/v1/dags/registrations"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/DagRegistrationResponse"}
    assert schema["paths"]["/api/v1/scheduler/stop"]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/SchedulerStopResponse"}
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    schema_text = json.dumps(schema)
    component_text = json.dumps(schema["components"]["schemas"]).lower()
    assert _SECRET not in schema_text
    assert str(database_path) not in schema_text
    assert "examples" not in component_text
    forbidden_component_terms = {
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

    source_parameters = schema["paths"]["/api/v1/task-details"]["get"]["parameters"]
    assert [(parameter["name"], parameter["in"]) for parameter in source_parameters] == [
        ("dag_name", "query"),
        ("task_name", "query"),
    ]
    assert "source_path" not in json.dumps(source_parameters)


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


def test_dag_graph_combines_current_structure_with_latest_run_safely(
    api_state: tuple[Path, Path],
) -> None:
    database_path, dag_path = api_state
    client = _client(database_path)

    no_history = client.get("/api/v1/dags/orders/graph")
    assert no_history.status_code == 200
    assert no_history.json() == {
        "name": "orders",
        "enabled": True,
        "schedule": "1h",
        "trigger": {"on_success_of": ["ingest"]},
        "tasks": [
            {"name": "extract", "kind": "pipeline", "depends_on": [], "status": None},
            {"name": "publish", "kind": "script", "depends_on": ["extract"], "status": None},
        ],
        "latest_run": None,
        "unmapped_task_history": [],
    }

    store = StateStore(database_path, process_identity=lambda: _IDENTITY)
    run_id = store.start_dag_run(
        "orders",
        ["extract", "publish", "retired_task"],
        trigger_reason="manual",
    )
    store.start_task_run(run_id, "extract")
    store.complete_task_run(run_id, "extract", success=True)
    store.start_task_run(run_id, "publish")
    store.block_task_run(run_id, "retired_task")
    store.interrupt_abandoned_dag_runs(lambda _pid: ProcessIdentityLookup.not_found())
    store.close()

    graph = client.get("/api/v1/dags/orders/graph")
    assert graph.status_code == 200
    payload = graph.json()
    assert payload["enabled"] is True
    assert payload["schedule"] == "1h"
    assert payload["tasks"] == [
        {"name": "extract", "kind": "pipeline", "depends_on": [], "status": "succeeded"},
        {"name": "publish", "kind": "script", "depends_on": ["extract"], "status": "running"},
    ]
    assert payload["latest_run"]["id"] == run_id
    assert payload["latest_run"]["status"] == "interrupted"
    assert payload["unmapped_task_history"] == [{"name": "retired_task", "status": "blocked"}]
    assert _SECRET not in graph.text
    assert str(dag_path) not in graph.text


@pytest.mark.parametrize(
    "dag_name",
    ["percent%name", "slash/name", "space name", "caffè-東京", "query?#name"],
)
def test_dag_graph_route_round_trips_encoded_names(tmp_path: Path, dag_name: str) -> None:
    database_path = tmp_path / "state.db"
    dag_path = tmp_path / "encoded-name.yaml"
    document = _dag_document().replace(
        "name: orders",
        f"name: {json.dumps(dag_name, ensure_ascii=False)}",
    )
    dag_path.write_text(document, encoding="utf-8")
    store = StateStore(database_path, process_identity=lambda: _IDENTITY)
    store.register_dag(dag_name, dag_path, "1h")
    store.close()

    response = _client(database_path).get(f"/api/v1/dags/{quote(dag_name, safe='')}/graph")

    assert response.status_code == 200
    assert response.json()["name"] == dag_name
    assert response.json()["tasks"][0]["name"] == "extract"


def _task_source_state(
    tmp_path: Path,
    *,
    dag_name: str = "source-details",
    task_name: str = "python/task%東京",
) -> tuple[Path, Path, Path]:
    database_path = tmp_path / "state.db"
    dag_path = tmp_path / "dag.yaml"
    pipeline_path = tmp_path / "pipeline.yaml"
    script_path = tmp_path / "job.py"
    pipeline_path.write_text(
        "name: safe-pipeline\nsource:\n  type: csv\n  path: input.csv\n"
        "destination:\n  type: csv\n  path: output.csv\n",
        encoding="utf-8",
    )
    script_path.write_text("def run(context):\n    return None\n", encoding="utf-8")
    dag_path.write_text(
        "\n".join(
            [
                f"name: {json.dumps(dag_name, ensure_ascii=False)}",
                "tasks:",
                '  - name: "pipeline/task%東京"',
                "    pipeline: pipeline.yaml",
                "    retries: 2",
                "    retry_delay_seconds: 1.5",
                f"  - name: {json.dumps(task_name, ensure_ascii=False)}",
                "    script: job.py",
                '    depends_on: ["pipeline/task%東京"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    store = StateStore(database_path, process_identity=lambda: _IDENTITY)
    store.register_dag(dag_name, dag_path, None)
    run_id = store.start_dag_run(
        dag_name,
        ["pipeline/task%東京", task_name],
        trigger_reason="manual",
    )
    store.start_task_run(run_id, task_name)
    store.close()
    return database_path, pipeline_path, script_path


def test_task_source_details_for_script_and_pipeline(tmp_path: Path) -> None:
    database_path, pipeline_path, script_path = _task_source_state(tmp_path)
    client = _client(database_path)

    script = client.get(
        "/api/v1/task-details",
        params={"dag_name": "source-details", "task_name": "python/task%東京"},
    )
    assert script.status_code == 200
    assert script.json() == {
        "dag_name": "source-details",
        "task_name": "python/task%東京",
        "kind": "script",
        "depends_on": ["pipeline/task%東京"],
        "latest_status": "running",
        "retry": {
            "retries": 0,
            "retry_delay_seconds": 0.0,
            "retry_backoff_multiplier": 1.0,
        },
        "source_language": "python",
        "source": script_path.read_bytes().decode("utf-8"),
        "source_size_bytes": len(script_path.read_bytes()),
    }
    assert str(script_path) not in script.text

    pipeline = client.get(
        "/api/v1/task-details",
        params={"dag_name": "source-details", "task_name": "pipeline/task%東京"},
    )
    assert pipeline.status_code == 200
    assert pipeline.json()["kind"] == "pipeline"
    assert pipeline.json()["source_language"] == "yaml"
    assert pipeline.json()["source"] == pipeline_path.read_bytes().decode("utf-8")
    assert pipeline.json()["retry"]["retries"] == 2
    assert pipeline.json()["latest_status"] == "pending"
    assert str(pipeline_path) not in pipeline.text


def test_task_source_missing_names_and_arbitrary_paths_are_safe(tmp_path: Path) -> None:
    database_path, _, _ = _task_source_state(tmp_path)
    client = _client(database_path)

    missing_dag = client.get(
        "/api/v1/task-details", params={"dag_name": "missing", "task_name": "task"}
    )
    missing_task = client.get(
        "/api/v1/task-details",
        params={"dag_name": "source-details", "task_name": "../../private.txt"},
    )

    assert missing_dag.status_code == 404
    assert missing_dag.json()["detail"]["code"] == "dag_not_found"
    assert missing_task.status_code == 404
    assert missing_task.json() == {
        "detail": {"code": "task_not_found", "message": "Task not found."}
    }
    assert "private.txt" not in missing_task.text


@pytest.mark.parametrize(
    ("content", "expected_status", "expected_code"),
    [
        (b"\xff\xfe", 415, "task_source_invalid_encoding"),
        (b"safe\x00binary", 415, "task_source_binary"),
        (b"x" * (256 * 1024 + 1), 413, "task_source_too_large"),
    ],
    ids=["invalid-utf8", "binary", "oversized"],
)
def test_task_source_rejects_invalid_content_safely(
    tmp_path: Path,
    content: bytes,
    expected_status: int,
    expected_code: str,
) -> None:
    database_path, _, script_path = _task_source_state(tmp_path)
    script_path.write_bytes(content)

    response = _client(database_path).get(
        "/api/v1/task-details",
        params={"dag_name": "source-details", "task_name": "python/task%東京"},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert str(script_path) not in response.text


def test_task_source_rejects_unreadable_files_without_exception_leakage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, script_path = _task_source_state(tmp_path)
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> object:
        if path == script_path:
            raise PermissionError(_SECRET)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    response = _client(database_path).get(
        "/api/v1/task-details",
        params={"dag_name": "source-details", "task_name": "python/task%東京"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "task_source_unavailable"
    assert _SECRET not in response.text
    assert str(script_path) not in response.text


def test_task_source_is_disabled_for_non_loopback_server_configuration(tmp_path: Path) -> None:
    database_path, _, _ = _task_source_state(tmp_path)
    client = TestClient(
        create_app(
            store_factory=_store_factory(database_path),
            task_source_access=False,
            allowed_hosts=("example.test",),
        ),
        base_url="http://example.test",
    )

    response = client.get(
        "/api/v1/task-details",
        params={"dag_name": "source-details", "task_name": "python/task%東京"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "source_access_not_allowed"


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
    missing_graph = client.get("/api/v1/dags/orders/graph")
    assert missing_graph.status_code == 409
    assert missing_graph.json()["detail"]["code"] == "dag_source_missing"

    dag_path.write_text(f"not: a DAG\nsecret: {_SECRET}\n", encoding="utf-8")
    invalid = client.get("/api/v1/dags/orders")
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "dag_configuration_invalid"
    assert _SECRET not in invalid.text
    invalid_graph = client.get("/api/v1/dags/orders/graph")
    assert invalid_graph.status_code == 409
    assert invalid_graph.json()["detail"]["code"] == "dag_configuration_invalid"

    unknown = client.get("/api/v1/dags/unknown")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "dag_not_found"
    unknown_graph = client.get("/api/v1/dags/unknown/graph")
    assert unknown_graph.status_code == 404
    assert unknown_graph.json()["detail"]["code"] == "dag_not_found"


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

    client = TestClient(create_app(store_factory=factory), base_url=_BASE_URL)
    assert client.get("/api/v1/dags").status_code == 200
    assert client.get("/api/v1/dags").status_code == 200

    assert len(created) == 2
    assert len({id(store) for store in created}) == 2
    assert all(store.query_threads == [store.created_thread] for store in created)
    assert all(store.closed for store in created)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_slow_state_read_does_not_stall_event_loop(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    release = threading.Event()

    class SlowStore(StateStore):
        def __init__(self) -> None:
            super().__init__(database_path, process_identity=lambda: _IDENTITY)

        def list_dags(self) -> list[object]:  # type: ignore[override]
            release.wait(timeout=2)
            return []

    application = create_app(store_factory=SlowStore)
    transport = ASGITransport(app=application)
    timer = threading.Timer(1, release.set)
    timer.start()
    try:
        async with AsyncClient(transport=transport, base_url=_BASE_URL) as client:
            started_at = time.perf_counter()
            slow_request = asyncio.create_task(client.get("/api/v1/dags"))
            await asyncio.sleep(0.05)
            metadata = await client.get("/api/v1")
            elapsed = time.perf_counter() - started_at
            release.set()
            slow_response = await slow_request
    finally:
        release.set()
        timer.cancel()

    assert metadata.status_code == 200
    assert slow_response.status_code == 200
    assert elapsed < 0.5


@pytest.mark.anyio
async def test_task_source_filesystem_read_does_not_stall_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = _task_source_state(tmp_path)
    release = threading.Event()
    from nexolith.api import service as api_service

    original_read = api_service._read_task_source

    def slow_read(path: Path) -> tuple[str, int]:
        release.wait(timeout=2)
        return original_read(path)

    monkeypatch.setattr(api_service, "_read_task_source", slow_read)
    application = create_app(
        store_factory=_store_factory(database_path),
        task_source_access=True,
    )
    transport = ASGITransport(app=application)
    timer = threading.Timer(1, release.set)
    timer.start()
    try:
        async with AsyncClient(transport=transport, base_url=_BASE_URL) as client:
            started_at = time.perf_counter()
            source_request = asyncio.create_task(
                client.get(
                    "/api/v1/task-details",
                    params={
                        "dag_name": "source-details",
                        "task_name": "python/task%東京",
                    },
                )
            )
            await asyncio.sleep(0.05)
            metadata = await client.get("/api/v1")
            elapsed = time.perf_counter() - started_at
            release.set()
            source_response = await source_request
    finally:
        release.set()
        timer.cancel()

    assert metadata.status_code == 200
    assert source_response.status_code == 200
    assert elapsed < 0.5


def test_unavailable_state_returns_typed_safe_error() -> None:
    def unavailable() -> StateStore:
        raise sqlite3.OperationalError(_SECRET)

    response = TestClient(create_app(store_factory=unavailable), base_url=_BASE_URL).get(
        "/api/v1/dags"
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "state_unavailable"
    assert _SECRET not in response.text


def test_api_cli_delegates_defaults_and_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, tuple[str, ...]]] = []
    monkeypatch.setattr(
        sys.modules["nexolith.cli.app"],
        "run_api_server",
        lambda host, port, *, trusted_hosts=(): calls.append((host, port, trusted_hosts)),
    )
    runner = CliRunner()

    default = runner.invoke(cli_app, ["api", "start"])
    custom = runner.invoke(
        cli_app,
        [
            "api",
            "start",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--trusted-host",
            "nexolith.internal",
        ],
    )

    assert default.exit_code == 0
    assert custom.exit_code == 0
    assert calls == [
        ("127.0.0.1", 8765, ()),
        ("0.0.0.0", 9000, ("nexolith.internal",)),
    ]
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
    def fail(_host: str, _port: int, **_kwargs: object) -> None:
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


def test_api_server_wildcard_bind_requires_explicit_trusted_host() -> None:
    with pytest.raises(ApiServerStartupError, match="explicit trusted host"):
        run_api_server("0.0.0.0", 8765, runner=lambda _app, **_kwargs: None)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "::1", "localhost"])
def test_task_source_access_recognizes_loopback_binds(host: str) -> None:
    assert _is_loopback_bind(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "nexolith.internal"])
def test_task_source_access_rejects_non_loopback_binds(host: str) -> None:
    assert _is_loopback_bind(host) is False
