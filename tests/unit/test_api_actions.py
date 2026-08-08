from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from nexolith.api.app import create_app
from nexolith.process_identity import ProcessIdentity
from nexolith.scheduler import (
    SchedulerControlError,
    SchedulerControlFailure,
    SchedulerStartResult,
    SchedulerStartState,
    SchedulerStopResult,
    SchedulerStopState,
)
from nexolith.state import StateStore

_BASE_URL = "http://127.0.0.1"
_IDENTITY = ProcessIdentity(4242, 1_700_000_000_000_000_000)
_SECRET = "NXL_ACTION_SENTINEL_09ad"


def _store_factory(database_path: Path) -> Callable[[], StateStore]:
    return lambda: StateStore(database_path, process_identity=lambda: _IDENTITY)


def _client(
    database_path: Path,
    *,
    start: Callable[[], SchedulerStartResult] | None = None,
    stop: Callable[[], SchedulerStopResult] | None = None,
) -> TestClient:
    kwargs: dict[str, object] = {"store_factory": _store_factory(database_path)}
    if start is not None:
        kwargs["scheduler_start_action"] = start
    if stop is not None:
        kwargs["scheduler_stop_action"] = stop
    return TestClient(create_app(**kwargs), base_url=_BASE_URL)


def _write_pipeline(path: Path, *, failing: bool = False) -> None:
    source = path.parent / ("missing.csv" if failing else "input.csv")
    if not failing:
        source.write_text("id,status\n1,ready\n", encoding="utf-8")
    path.write_text(
        f"""
name: pipeline
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(path.parent / "output.csv").as_posix()}
""",
        encoding="utf-8",
    )


def _write_dag(path: Path, *, schedule: str = "5m", failing: bool = False) -> None:
    pipeline = path.parent / "pipeline.yaml"
    _write_pipeline(pipeline, failing=failing)
    path.write_text(
        f"""
name: orders
schedule: {schedule}
tasks:
  - name: extract
    pipeline: {pipeline.name}
""",
        encoding="utf-8",
    )


def test_registration_create_noop_force_and_disabled_preservation(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    dag_path = tmp_path / "orders.yaml"
    _write_dag(dag_path)
    client = _client(database)

    created = client.post(
        "/api/v1/dags/registrations",
        json={"source_path": str(dag_path), "force": False},
    )
    assert created.status_code == 201
    assert created.json() == {
        "dag_name": "orders",
        "status": "created",
        "enabled": True,
        "schedule": "5m",
    }
    assert str(dag_path.resolve()) not in created.text
    assert not (tmp_path / "output.csv").exists()

    unchanged = client.post(
        "/api/v1/dags/registrations",
        json={"source_path": str(dag_path), "force": False},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "unchanged"

    store = StateStore(database, process_identity=lambda: _IDENTITY)
    store.set_dag_enabled("orders", False)
    store.close()
    _write_dag(dag_path, schedule="30m")
    updated = client.post(
        "/api/v1/dags/registrations",
        json={"source_path": str(dag_path), "force": True},
    )
    assert updated.status_code == 200
    assert updated.json() == {
        "dag_name": "orders",
        "status": "updated",
        "enabled": False,
        "schedule": "30m",
    }

    store = StateStore(database, process_identity=lambda: _IDENTITY)
    assert store.list_recent_dag_runs() == []
    store.close()


def test_invalid_registration_creates_no_row_and_redacts_error(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    source = tmp_path / f"{_SECRET}.yaml"
    source.write_text(f"invalid: {_SECRET}\n", encoding="utf-8")

    response = _client(database).post(
        "/api/v1/dags/registrations",
        json={"source_path": str(source), "force": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "dag_configuration_invalid"
    assert _SECRET not in response.text
    store = StateStore(database, process_identity=lambda: _IDENTITY)
    assert store.list_dags() == []
    store.close()


@pytest.mark.parametrize("failing", [False, True])
def test_trigger_executes_registered_dag_and_persists_history(
    tmp_path: Path, failing: bool
) -> None:
    database = tmp_path / "state.db"
    dag_path = tmp_path / "orders.yaml"
    _write_dag(dag_path, failing=failing)
    client = _client(database)
    assert (
        client.post(
            "/api/v1/dags/registrations",
            json={"source_path": str(dag_path), "force": False},
        ).status_code
        == 201
    )

    response = client.post("/api/v1/dags/orders/runs", json={"confirm": True})

    assert response.status_code == 201
    payload = response.json()
    assert payload["run_id"] > 0
    assert payload["dag_name"] == "orders"
    assert payload["status"] == ("failed" if failing else "succeeded")
    store = StateStore(database, process_identity=lambda: _IDENTITY)
    run = store.get_dag_run(payload["run_id"])
    assert run is not None
    assert run.trigger_reason == "api"
    assert [task.task_name for task in store.list_task_runs(run.id)] == ["extract"]
    assert [attempt.attempt_number for attempt in store.list_run_attempts(run.id)] == [1]
    store.close()


def test_trigger_missing_and_changed_sources_fail_safely(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    dag_path = tmp_path / "orders.yaml"
    _write_dag(dag_path)
    client = _client(database)
    assert (
        client.post(
            "/api/v1/dags/registrations",
            json={"source_path": str(dag_path), "force": False},
        ).status_code
        == 201
    )

    unknown = client.post("/api/v1/dags/unknown/runs", json={"confirm": True})
    assert unknown.status_code == 404
    dag_path.unlink()
    missing = client.post("/api/v1/dags/orders/runs", json={"confirm": True})
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "dag_source_missing"

    dag_path.write_text("name: renamed\ntasks: []\n", encoding="utf-8")
    invalid = client.post("/api/v1/dags/orders/runs", json={"confirm": True})
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "dag_configuration_invalid"


def test_trigger_accepts_encoded_dag_names_with_path_separators(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    dag_path = tmp_path / "encoded.yaml"
    _write_dag(dag_path)
    dag_name = "orders / percent% 東京"
    dag_path.write_text(
        dag_path.read_text(encoding="utf-8").replace("name: orders", f"name: {dag_name}"),
        encoding="utf-8",
    )
    client = _client(database)
    assert (
        client.post(
            "/api/v1/dags/registrations",
            json={"source_path": str(dag_path), "force": False},
        ).status_code
        == 201
    )

    response = client.post(
        f"/api/v1/dags/{quote(dag_name, safe='')}/runs",
        json={"confirm": True},
    )

    assert response.status_code == 201
    assert response.json()["dag_name"] == dag_name


def test_unexpected_task_exception_remains_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "state.db"
    dag_path = tmp_path / "orders.yaml"
    _write_dag(dag_path)
    client = _client(database)
    assert (
        client.post(
            "/api/v1/dags/registrations",
            json={"source_path": str(dag_path), "force": False},
        ).status_code
        == 201
    )

    class UnexpectedApplication:
        def run_pipeline(self, path: Path, **_kwargs: object) -> object:
            raise ValueError(_SECRET)

    monkeypatch.setattr(
        sys.modules["nexolith.dag.executor"], "PipelineApplication", UnexpectedApplication
    )
    response = client.post("/api/v1/dags/orders/runs", json={"confirm": True})

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert _SECRET not in response.text


def test_action_store_lifecycle_and_event_loop_responsiveness(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    dag_path = tmp_path / "orders.yaml"
    _write_dag(dag_path)
    release = threading.Event()

    class TrackingStore(StateStore):
        instances: list[TrackingStore] = []

        def __init__(self) -> None:
            super().__init__(database, process_identity=lambda: _IDENTITY)
            self.created_thread = threading.get_ident()
            self.used_threads: list[int] = []
            self.closed_thread: int | None = None
            self.instances.append(self)

        def get_dag(self, name: str) -> object:  # type: ignore[override]
            self.used_threads.append(threading.get_ident())
            release.wait(timeout=2)
            return super().get_dag(name)

        def close(self) -> None:
            self.closed_thread = threading.get_ident()
            super().close()

    application = create_app(store_factory=TrackingStore)

    async def exercise() -> tuple[int, int, float]:
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url=_BASE_URL) as client:
            started = time.perf_counter()
            action = asyncio.create_task(
                client.post(
                    "/api/v1/dags/registrations",
                    json={"source_path": str(dag_path), "force": False},
                )
            )
            await asyncio.sleep(0.05)
            metadata = await client.get("/api/v1")
            elapsed = time.perf_counter() - started
            release.set()
            return (await action).status_code, metadata.status_code, elapsed

    try:
        action_status, metadata_status, elapsed = asyncio.run(exercise())
    finally:
        release.set()

    assert (action_status, metadata_status) == (201, 200)
    assert elapsed < 0.5
    assert len(TrackingStore.instances) == 1
    store = TrackingStore.instances[0]
    assert store.used_threads
    assert set(store.used_threads) == {store.created_thread}
    assert store.closed_thread == store.created_thread


def test_scheduler_action_responses_and_safe_failures(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    started = _client(
        database,
        start=lambda: SchedulerStartResult(SchedulerStartState.STARTED, 1234),
        stop=lambda: SchedulerStopResult(SchedulerStopState.UNCERTAIN, 1234, forced=True),
    )
    start_response = started.post("/api/v1/scheduler/start", json={"confirm": True})
    stop_response = started.post("/api/v1/scheduler/stop", json={"confirm": True})
    assert start_response.status_code == 200
    assert start_response.json() == {"state": "started", "pid": 1234}
    assert stop_response.status_code == 202
    assert stop_response.json() == {"state": "uncertain", "pid": 1234, "forced": True}

    not_running = _client(
        database,
        stop=lambda: SchedulerStopResult(SchedulerStopState.NOT_RUNNING),
    ).post("/api/v1/scheduler/stop", json={"confirm": True})
    assert not_running.status_code == 200
    assert not_running.json() == {
        "state": "not_running",
        "pid": None,
        "forced": False,
    }

    already = _client(
        database,
        start=lambda: SchedulerStartResult(SchedulerStartState.ALREADY_RUNNING, 4321),
    ).post("/api/v1/scheduler/start", json={"confirm": True})
    assert already.status_code == 409
    assert already.json()["detail"]["code"] == "scheduler_already_running"

    def unavailable() -> SchedulerStartResult:
        raise SchedulerControlError(SchedulerControlFailure.IDENTITY_UNAVAILABLE, _SECRET)

    failure = _client(database, start=unavailable).post(
        "/api/v1/scheduler/start", json={"confirm": True}
    )
    assert failure.status_code == 503
    assert _SECRET not in failure.text


def test_action_browser_boundary_json_and_methods(tmp_path: Path) -> None:
    client = _client(tmp_path / "state.db")
    paths = [
        "/api/v1/dags/registrations",
        "/api/v1/dags/orders/runs",
        "/api/v1/scheduler/start",
        "/api/v1/scheduler/stop",
    ]
    for path in paths:
        assert client.get(path).status_code == 405
        non_json = client.post(
            path,
            content="confirm=true",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert non_json.status_code == 415
        cross_origin = client.post(
            path,
            json={"confirm": True},
            headers={"origin": "https://attacker.invalid"},
        )
        assert cross_origin.status_code == 403

    bad_host = TestClient(create_app(), base_url="http://attacker.invalid").get("/api/v1")
    assert bad_host.status_code == 400
    same_origin = client.post(
        "/api/v1/dags/orders/runs",
        json={"confirm": True},
        headers={"origin": _BASE_URL},
    )
    assert same_origin.status_code == 404
    schema = client.get("/openapi.json").json()
    assert not any(
        key.casefold() == "access-control-allow-origin"
        for path in schema["paths"].values()
        for operation in path.values()
        for response in operation.get("responses", {}).values()
        for key in response.get("headers", {})
    )
    assert _SECRET not in json.dumps(schema)


def test_state_failure_during_action_is_typed_and_redacted() -> None:
    def unavailable() -> StateStore:
        raise sqlite3.OperationalError(_SECRET)

    response = TestClient(create_app(store_factory=unavailable), base_url=_BASE_URL).post(
        "/api/v1/dags/registrations",
        json={"source_path": _SECRET, "force": False},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "state_unavailable"
    assert _SECRET not in response.text
