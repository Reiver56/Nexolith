import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest

from nexolith.application import PipelineApplication
from nexolith.cli.render_context import RenderContext
from nexolith.cli.runs_render import render_run_detail
from nexolith.dag import DagConfig, DagExecutor, DagTaskConfig, execute_dag, load_dag
from nexolith.events import EventSink
from nexolith.models import ExecutionResult
from nexolith.process_identity import ProcessIdentity, ProcessIdentityLookup
from nexolith.scheduler import Scheduler
from nexolith.state import (
    DagRunStatus,
    StateStore,
    TaskAttemptStatus,
    TaskRunRecord,
    TaskRunStatus,
)
from nexolith.types import Scalar


def write_pipeline(path: Path, *, name: str) -> None:
    """A real, executable CSV -> CSV pipeline (no mocking of the pipeline
    layer) -- matching the style already used for real /run execution
    tests elsewhere in this project.
    """
    source = path.with_suffix(".csv")
    source.write_text("id,status\n1,ready\n2,done\n", encoding="utf-8")
    path.write_text(
        f"""
name: {name}
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(path.parent / f"{name}_out.csv").as_posix()}
""",
        encoding="utf-8",
    )


def write_failing_pipeline(path: Path, *, name: str) -> None:
    """A structurally valid, individually-passing-`load_dag`-validation
    pipeline whose source file does not exist -- fails for real at
    extraction time (a genuine ConnectorError/ExecutionError from
    PipelineApplication.run_pipeline(), not a stand-in)."""
    path.write_text(
        f"""
name: {name}
source:
  type: csv
  path: {(path.parent / "does_not_exist.csv").as_posix()}
transformations: []
destination:
  type: csv
  path: {(path.parent / f"{name}_out.csv").as_posix()}
""",
        encoding="utf-8",
    )


def write_dag(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def write_sqlite_db_with_items(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE items (id INTEGER, status TEXT)")
    connection.executemany(
        "INSERT INTO items VALUES (?, ?)",
        [(1, "active"), (2, "active"), (3, "inactive")],
    )
    connection.commit()
    connection.close()


def write_parameterized_pipeline(path: Path, *, name: str, database: Path) -> None:
    """A required parameter (`status: null`) with no static value -- only a
    DAG task's own `parameters:` override (NXL-82) can resolve it."""
    path.write_text(
        f"""
name: {name}
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  query: "SELECT id, status FROM items WHERE status = :status"
  parameters:
    status: null
transformations: []
destination:
  type: csv
  path: {(path.parent / f"{name}_out.csv").as_posix()}
""",
        encoding="utf-8",
    )


class _SequencedApplication:
    def __init__(self, outcomes: list[Exception | None]) -> None:
        self._outcomes = outcomes
        self.calls: list[str] = []

    def run_pipeline(
        self,
        path: Path,
        *,
        parameter_overrides: Mapping[str, Scalar] | None = None,
        event_sink: EventSink | None = None,
    ) -> ExecutionResult:
        self.calls.append(path.name)
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return ExecutionResult(path.stem)


def single_task_dag(*, retries: int = 0) -> DagConfig:
    return DagConfig(
        name="unexpected-task",
        tasks=[
            DagTaskConfig(
                name="only",
                pipeline="only.yaml",
                retries=retries,
            )
        ],
    )


def test_dag_task_parameter_override_reaches_and_affects_the_real_query(tmp_path: Path) -> None:
    """NXL-82 end-to-end: the pipeline declares a required parameter with no
    static value; only the DAG task's own `parameters:` block can resolve
    it. Two DAGs supplying different values for the same pipeline must
    produce genuinely different real query output -- not just "doesn't
    crash" -- proving the value actually reached the database query.
    """
    database = tmp_path / "items.db"
    write_sqlite_db_with_items(database)
    write_parameterized_pipeline(tmp_path / "filtered.yaml", name="filtered", database=database)

    active_dag = tmp_path / "active.yaml"
    write_dag(
        active_dag,
        """
name: active_only
tasks:
  - name: filtered
    pipeline: filtered.yaml
    depends_on: []
    parameters:
      status: active
""",
    )
    inactive_dag = tmp_path / "inactive.yaml"
    write_dag(
        inactive_dag,
        """
name: inactive_only
tasks:
  - name: filtered
    pipeline: filtered.yaml
    depends_on: []
    parameters:
      status: inactive
""",
    )

    import csv

    output_path = tmp_path / "filtered_out.csv"

    store = StateStore(tmp_path / "state.db")
    try:
        active_run_id = execute_dag(active_dag, store)
        active_run = store.get_dag_run(active_run_id)
        assert active_run is not None
        assert active_run.status is DagRunStatus.SUCCEEDED

        # Both DAGs point at the same pipeline (and thus the same
        # destination file) -- read it immediately after each run, before
        # the next run's own write overwrites it.
        with output_path.open(newline="", encoding="utf-8") as handle:
            active_rows = list(csv.DictReader(handle))

        inactive_run_id = execute_dag(inactive_dag, store)
        inactive_run = store.get_dag_run(inactive_run_id)
        assert inactive_run is not None
        assert inactive_run.status is DagRunStatus.SUCCEEDED

        with output_path.open(newline="", encoding="utf-8") as handle:
            inactive_rows = list(csv.DictReader(handle))
    finally:
        store.close()

    assert {row["status"] for row in active_rows} == {"active"}
    assert len(active_rows) == 2
    assert {row["status"] for row in inactive_rows} == {"inactive"}
    assert len(inactive_rows) == 1


def test_dag_validation_catches_a_missing_task_parameter_before_any_run(tmp_path: Path) -> None:
    """A DAG referencing a pipeline with a required parameter, but never
    supplying it via the task's own `parameters:` block, must fail
    `load_dag`'s validation -- the same load-time guarantee story 1
    established for a missing `query_file`, extended to NXL-82."""
    database = tmp_path / "items.db"
    write_sqlite_db_with_items(database)
    write_parameterized_pipeline(tmp_path / "filtered.yaml", name="filtered", database=database)

    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: missing_param
tasks:
  - name: filtered
    pipeline: filtered.yaml
    depends_on: []
""",
    )

    from nexolith.exceptions import ConfigurationError

    try:
        load_dag(dag_path)
    except ConfigurationError as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("expected load_dag to reject the missing parameter")


def test_fully_successful_linear_dag(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    write_pipeline(tmp_path / "c.yaml", name="c")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: linear
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: b
    pipeline: b.yaml
    depends_on: [a]
  - name: c
    pipeline: c.yaml
    depends_on: [b]
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag_run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(dag_run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
        assert run.ended_at is not None
        assert run.error is None

        tasks = {task.task_name: task for task in store.list_task_runs(dag_run_id)}
        assert tasks.keys() == {"a", "b", "c"}
        for task in tasks.values():
            assert task.status is TaskRunStatus.SUCCEEDED
            assert task.started_at is not None
            assert task.ended_at is not None
            assert task.error is None

        # Real execution actually happened -- not a stand-in for the
        # pipeline layer: every destination file exists with real rows.
        for name in ("a", "b", "c"):
            destination = tmp_path / f"{name}_out.csv"
            assert destination.is_file()
            rows = destination.read_text(encoding="utf-8").strip().splitlines()
            assert rows == ["id,status", "1,ready", "2,done"]
    finally:
        store.close()


def test_fully_successful_branching_and_merging_dag(tmp_path: Path) -> None:
    for name in ("extract", "transform_x", "transform_y", "merge"):
        write_pipeline(tmp_path / f"{name}.yaml", name=name)
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: fan-out-fan-in
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
  - name: transform_x
    pipeline: transform_x.yaml
    depends_on: [extract]
  - name: transform_y
    pipeline: transform_y.yaml
    depends_on: [extract]
  - name: merge
    pipeline: merge.yaml
    depends_on: [transform_x, transform_y]
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag_run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(dag_run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED

        tasks = {task.task_name: task for task in store.list_task_runs(dag_run_id)}
        assert all(task.status is TaskRunStatus.SUCCEEDED for task in tasks.values())
    finally:
        store.close()


def test_middle_task_failure_skips_downstream_but_not_independent_branches(
    tmp_path: Path,
) -> None:
    """extract -> {transform (fails), side} ; transform -> load
    'side' has no path from 'transform' and must still run and succeed;
    'load' depends on 'transform' and must be skipped.
    """
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    write_failing_pipeline(tmp_path / "transform.yaml", name="transform")
    write_pipeline(tmp_path / "side.yaml", name="side")
    write_pipeline(tmp_path / "load.yaml", name="load")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: middle-failure
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
  - name: transform
    pipeline: transform.yaml
    depends_on: [extract]
  - name: side
    pipeline: side.yaml
    depends_on: [extract]
  - name: load
    pipeline: load.yaml
    depends_on: [transform]
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag_run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(dag_run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        assert run.error is not None
        assert "transform" in run.error

        tasks = {task.task_name: task for task in store.list_task_runs(dag_run_id)}
        assert tasks["extract"].status is TaskRunStatus.SUCCEEDED
        assert tasks["transform"].status is TaskRunStatus.FAILED
        assert tasks["transform"].error is not None
        assert tasks["side"].status is TaskRunStatus.SUCCEEDED
        assert tasks["load"].status is TaskRunStatus.SKIPPED
        assert tasks["load"].started_at is None
        assert tasks["load"].ended_at is not None
    finally:
        store.close()


def test_first_task_failure_skips_everything_downstream(tmp_path: Path) -> None:
    write_failing_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    write_pipeline(tmp_path / "c.yaml", name="c")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: first-fails
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: b
    pipeline: b.yaml
    depends_on: [a]
  - name: c
    pipeline: c.yaml
    depends_on: [b]
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag_run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(dag_run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED

        tasks = {task.task_name: task for task in store.list_task_runs(dag_run_id)}
        assert tasks["a"].status is TaskRunStatus.FAILED
        assert tasks["b"].status is TaskRunStatus.SKIPPED
        assert tasks["c"].status is TaskRunStatus.SKIPPED
    finally:
        store.close()


class _ProbingApplication:
    """Wraps a real PipelineApplication; after each task's real
    run_pipeline() call returns, snapshots the store's task_runs for the
    current DAG run -- proving intermediate state is visible in the store
    mid-run, not only after DagExecutor.run() returns as a whole. Uses the
    real PipelineApplication underneath (not a stand-in for it), so tasks
    genuinely execute.
    """

    def __init__(self, store: StateStore, dag_name: str) -> None:
        self._real = PipelineApplication()
        self._store = store
        self._dag_name = dag_name
        self.snapshots: list[dict[str, TaskRunRecord]] = []

    def run_pipeline(
        self,
        path: Path,
        *,
        parameter_overrides: Mapping[str, Scalar] | None = None,
        event_sink: EventSink | None = None,
    ) -> ExecutionResult:
        result = self._real.run_pipeline(
            path, parameter_overrides=parameter_overrides, event_sink=event_sink
        )
        run = self._store.latest_dag_run(self._dag_name)
        assert run is not None
        self.snapshots.append({task.task_name: task for task in self._store.list_task_runs(run.id)})
        return result


def test_live_incremental_state_recording(tmp_path: Path) -> None:
    """Not just checking final state: a probe fires from inside each task's
    real run_pipeline() call, mid-DagExecutor.run(), and reads the store
    right then. If results were only written at the end (batched), every
    snapshot would show the pre-run 'pending' state; because they're
    written as each task actually happens, the snapshot taken during task
    B already shows A as succeeded and C still untouched.
    """
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    write_pipeline(tmp_path / "c.yaml", name="c")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: linear
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: b
    pipeline: b.yaml
    depends_on: [a]
  - name: c
    pipeline: c.yaml
    depends_on: [b]
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag = load_dag(dag_path)
        probe = _ProbingApplication(store, dag.name)
        executor = DagExecutor(store, probe)

        executor.run(dag, dag_path)

        assert len(probe.snapshots) == 3

        during_a = probe.snapshots[0]
        assert during_a["a"].status is TaskRunStatus.RUNNING
        assert during_a["b"].status is TaskRunStatus.PENDING
        assert during_a["c"].status is TaskRunStatus.PENDING

        during_b = probe.snapshots[1]
        assert during_b["a"].status is TaskRunStatus.SUCCEEDED
        assert during_b["b"].status is TaskRunStatus.RUNNING
        assert during_b["c"].status is TaskRunStatus.PENDING

        during_c = probe.snapshots[2]
        assert during_c["a"].status is TaskRunStatus.SUCCEEDED
        assert during_c["b"].status is TaskRunStatus.SUCCEEDED
        assert during_c["c"].status is TaskRunStatus.RUNNING
    finally:
        store.close()


def test_abrupt_stop_mid_run_leaves_an_accurate_partial_record(tmp_path: Path) -> None:
    """A process-level interruption propagates and stays reconcilable."""
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: crashes
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: b
    pipeline: b.yaml
    depends_on: [a]
""",
    )
    owner = ProcessIdentity(pid=999999, create_time_ns=1_000_000_000)
    store = StateStore(tmp_path / "state.db", process_identity=lambda: owner)
    try:
        dag = load_dag(dag_path)

        class _SimulatedProcessInterruption(BaseException):
            pass

        class _CrashingApplication:
            def __init__(self) -> None:
                self._real = PipelineApplication()
                self._calls = 0

            def run_pipeline(
                self,
                path: Path,
                *,
                parameter_overrides: Mapping[str, Scalar] | None = None,
                event_sink: EventSink | None = None,
            ) -> ExecutionResult:
                self._calls += 1
                if self._calls == 2:
                    raise _SimulatedProcessInterruption()
                return self._real.run_pipeline(
                    path, parameter_overrides=parameter_overrides, event_sink=event_sink
                )

        executor = DagExecutor(store, _CrashingApplication())

        with pytest.raises(_SimulatedProcessInterruption):
            executor.run(dag, dag_path)

        run = store.latest_dag_run("crashes")
        assert run is not None
        assert run.status is DagRunStatus.RUNNING  # never got to finalize -- exactly the point
        assert run.ended_at is None

        tasks = {task.task_name: task for task in store.list_task_runs(run.id)}
        assert tasks["a"].status is TaskRunStatus.SUCCEEDED
        assert tasks["b"].status is TaskRunStatus.RUNNING
        assert tasks["b"].ended_at is None

        attempts = store.list_task_attempts(run.id, "b")
        assert len(attempts) == 1
        assert attempts[0].status is TaskAttemptStatus.RUNNING
        assert attempts[0].ended_at is None

        incomplete = store.list_incomplete_dag_runs()
        assert [incomplete_run.id for incomplete_run in incomplete] == [run.id]

        scheduler = Scheduler(
            store,
            process_identity_lookup=lambda pid: ProcessIdentityLookup.not_found(),
        )
        assert scheduler.tick() == []
        reconciled = store.get_dag_run(run.id)
        assert reconciled is not None
        assert reconciled.status is DagRunStatus.INTERRUPTED
    finally:
        store.close()


@pytest.mark.parametrize(
    "failure",
    [ValueError("unexpected value"), OSError("unexpected operating-system error")],
    ids=["value-error", "os-error"],
)
def test_unexpected_ordinary_exception_records_terminal_failed_history(
    tmp_path: Path,
    failure: Exception,
) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = DagExecutor(store, _SequencedApplication([failure])).run(
            single_task_dag(),
            tmp_path / "workflow.yaml",
        )

        expected_error = f"Unexpected task failure ({type(failure).__name__})."
        attempts = store.list_task_attempts(run_id, "only")
        assert len(attempts) == 1
        assert attempts[0].status is TaskAttemptStatus.FAILED
        assert attempts[0].ended_at is not None
        assert attempts[0].error == expected_error

        task = store.list_task_runs(run_id)[0]
        assert task.status is TaskRunStatus.FAILED
        assert task.ended_at is not None
        assert task.error == expected_error

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        assert run.ended_at is not None
        assert run.error == f"Task 'only' failed: {expected_error}"
    finally:
        store.close()


def test_unexpected_exception_retry_can_succeed(tmp_path: Path) -> None:
    application = _SequencedApplication([ValueError("transient"), None])
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = DagExecutor(store, application).run(
            single_task_dag(retries=1),
            tmp_path / "workflow.yaml",
        )

        attempts = store.list_task_attempts(run_id, "only")
        assert [attempt.status for attempt in attempts] == [
            TaskAttemptStatus.FAILED,
            TaskAttemptStatus.SUCCEEDED,
        ]
        assert attempts[0].error == "Unexpected task failure (ValueError)."
        assert attempts[1].error is None
        assert store.list_task_runs(run_id)[0].status is TaskRunStatus.SUCCEEDED
        assert store.get_dag_run(run_id).status is DagRunStatus.SUCCEEDED  # type: ignore[union-attr]
    finally:
        store.close()


def test_unexpected_exception_exhausts_every_configured_retry(tmp_path: Path) -> None:
    application = _SequencedApplication([OSError("first"), OSError("second"), OSError("third")])
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = DagExecutor(store, application).run(
            single_task_dag(retries=2),
            tmp_path / "workflow.yaml",
        )

        attempts = store.list_task_attempts(run_id, "only")
        assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]
        assert all(attempt.status is TaskAttemptStatus.FAILED for attempt in attempts)
        assert all(attempt.ended_at is not None for attempt in attempts)
        assert {attempt.error for attempt in attempts} == {"Unexpected task failure (OSError)."}
        assert store.list_task_runs(run_id)[0].status is TaskRunStatus.FAILED
        assert store.get_dag_run(run_id).status is DagRunStatus.FAILED  # type: ignore[union-attr]
    finally:
        store.close()


def test_unexpected_failure_skip_policy_preserves_independent_branch(tmp_path: Path) -> None:
    dag = DagConfig(
        name="skip-unexpected",
        on_failure="skip",
        tasks=[
            DagTaskConfig(name="fails", pipeline="fails.yaml"),
            DagTaskConfig(
                name="dependent",
                pipeline="dependent.yaml",
                depends_on=["fails"],
            ),
            DagTaskConfig(name="independent", pipeline="independent.yaml"),
        ],
    )
    application = _SequencedApplication([ValueError("unexpected"), None])
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = DagExecutor(store, application).run(dag, tmp_path / "workflow.yaml")

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["fails"].status is TaskRunStatus.FAILED
        assert tasks["dependent"].status is TaskRunStatus.SKIPPED
        assert tasks["independent"].status is TaskRunStatus.SUCCEEDED
        assert application.calls == ["fails.yaml", "independent.yaml"]
        assert store.get_dag_run(run_id).status is DagRunStatus.FAILED  # type: ignore[union-attr]
    finally:
        store.close()


def test_unexpected_failure_block_policy_blocks_all_remaining_tasks(tmp_path: Path) -> None:
    dag = DagConfig(
        name="block-unexpected",
        on_failure="block",
        tasks=[
            DagTaskConfig(name="fails", pipeline="fails.yaml"),
            DagTaskConfig(name="independent", pipeline="independent.yaml"),
        ],
    )
    application = _SequencedApplication([ValueError("unexpected")])
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = DagExecutor(store, application).run(dag, tmp_path / "workflow.yaml")

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["fails"].status is TaskRunStatus.FAILED
        assert tasks["independent"].status is TaskRunStatus.BLOCKED
        assert application.calls == ["fails.yaml"]
        assert store.get_dag_run(run_id).status is DagRunStatus.FAILED  # type: ignore[union-attr]
    finally:
        store.close()


def test_unexpected_failure_diagnostic_redacts_arbitrary_exception_text(tmp_path: Path) -> None:
    sensitive_values = (
        "nxl122-sentinel-password",
        "nxl122-sentinel-token",
        "postgresql://private-user:private-password@db.internal/private",
        "/private/home/sentinel-user/workflow.py",
    )
    failure_text = " | ".join(sensitive_values)
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = DagExecutor(
            store,
            _SequencedApplication([ValueError(failure_text), ValueError(failure_text)]),
        ).run(
            single_task_dag(retries=1),
            tmp_path / "workflow.yaml",
        )
        run = store.get_dag_run(run_id)
        assert run is not None
        tasks = store.list_task_runs(run_id)
        attempts = store.list_run_attempts(run_id)
        rendered = render_run_detail(
            run,
            tasks,
            RenderContext(is_tty=False, color_enabled=False, width=120),
            attempts,
        )
        persisted_and_rendered = "\n".join(
            [
                run.error or "",
                *(task.error or "" for task in tasks),
                *(attempt.error or "" for attempt in attempts),
                rendered,
            ]
        )
        if any(value in persisted_and_rendered for value in sensitive_values):
            pytest.fail("unexpected task diagnostics exposed a sentinel value", pytrace=False)
        assert persisted_and_rendered.count("Unexpected task failure (ValueError).") >= 4
    finally:
        store.close()


def test_state_store_failure_is_not_mislabeled_as_a_task_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path / "state.db")

    def fail_attempt_persistence(
        attempt_id: int,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        raise sqlite3.OperationalError("controlled persistence failure")

    monkeypatch.setattr(store, "complete_task_attempt", fail_attempt_persistence)
    try:
        with pytest.raises(sqlite3.OperationalError, match="controlled persistence failure"):
            DagExecutor(store, _SequencedApplication([ValueError("task failed")])).run(
                single_task_dag(),
                tmp_path / "workflow.yaml",
            )

        run = store.latest_dag_run("unexpected-task")
        assert run is not None
        assert run.status is DagRunStatus.RUNNING
        assert run.error is None
        task = store.list_task_runs(run.id)[0]
        assert task.status is TaskRunStatus.RUNNING
        assert task.error is None
        attempt = store.list_task_attempts(run.id, "only")[0]
        assert attempt.status is TaskAttemptStatus.RUNNING
        assert attempt.error is None
    finally:
        store.close()


def test_executing_an_already_registered_dag_does_not_clobber_its_schedule(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: scheduled
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        store.register_dag("scheduled", dag_path, "0 2 * * *", enabled=True)

        execute_dag(dag_path, store)

        dag_record = store.get_dag("scheduled")
        assert dag_record is not None
        assert dag_record.schedule == "0 2 * * *"
        assert dag_record.enabled is True
    finally:
        store.close()
