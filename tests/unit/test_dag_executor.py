from pathlib import Path

from nexolith.application import PipelineApplication
from nexolith.dag import DagExecutor, execute_dag, load_dag
from nexolith.events import EventSink
from nexolith.models import ExecutionResult
from nexolith.state import DagRunStatus, StateStore, TaskRunRecord, TaskRunStatus


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

    def run_pipeline(self, path: Path, *, event_sink: EventSink | None = None) -> ExecutionResult:
        result = self._real.run_pipeline(path, event_sink=event_sink)
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
    """Simulates a crash: a task raises something DagExecutor does not
    catch (only ConfigurationError/ExecutionError are caught, matching
    PipelineApplication's real contract), propagating out of run()
    entirely -- and the store must already reflect real partial progress,
    not nothing, because nothing here is batched until the end.
    """
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
    store = StateStore(tmp_path / "state.db")
    try:
        dag = load_dag(dag_path)

        class _CrashingApplication:
            def __init__(self) -> None:
                self._real = PipelineApplication()
                self._calls = 0

            def run_pipeline(
                self, path: Path, *, event_sink: EventSink | None = None
            ) -> ExecutionResult:
                self._calls += 1
                if self._calls == 2:
                    raise RuntimeError("process died")
                return self._real.run_pipeline(path, event_sink=event_sink)

        executor = DagExecutor(store, _CrashingApplication())

        try:
            executor.run(dag, dag_path)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the simulated crash to propagate")

        run = store.latest_dag_run("crashes")
        assert run is not None
        assert run.status is DagRunStatus.RUNNING  # never got to finalize -- exactly the point
        assert run.ended_at is None

        tasks = {task.task_name: task for task in store.list_task_runs(run.id)}
        assert tasks["a"].status is TaskRunStatus.SUCCEEDED
        assert tasks["b"].status is TaskRunStatus.RUNNING
        assert tasks["b"].ended_at is None

        incomplete = store.list_incomplete_dag_runs()
        assert [incomplete_run.id for incomplete_run in incomplete] == [run.id]
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
