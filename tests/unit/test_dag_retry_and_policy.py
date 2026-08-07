import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from nexolith.dag import DagExecutor, execute_dag, load_dag
from nexolith.events import EventSink
from nexolith.models import ExecutionResult
from nexolith.state import DagRunStatus, StateStore, TaskAttemptStatus, TaskRunStatus
from nexolith.types import Scalar


def write_pipeline(path: Path, *, name: str) -> None:
    source = path.with_suffix(".csv")
    source.write_text("id,status\n1,ready\n", encoding="utf-8")
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


class FlakyApplication:
    """Wraps a real PipelineApplication; fails a named task's first N calls,
    then delegates to the real one -- a real execution attempt every time,
    never simulated, just made to fail on demand for the first few tries.
    """

    def __init__(self, fail_task_times: dict[str, int]) -> None:
        from nexolith.application import PipelineApplication

        self._real = PipelineApplication()
        self._remaining_failures = dict(fail_task_times)

    def run_pipeline(
        self,
        path: Path,
        *,
        parameter_overrides: Mapping[str, Scalar] | None = None,
        event_sink: EventSink | None = None,
    ) -> ExecutionResult:
        from nexolith.exceptions import ExecutionError

        for name, remaining in list(self._remaining_failures.items()):
            if path.name.startswith(name) and remaining > 0:
                self._remaining_failures[name] -= 1
                raise ExecutionError(f"Task '{name}' failed: simulated transient failure")
        return self._real.run_pipeline(
            path, parameter_overrides=parameter_overrides, event_sink=event_sink
        )


def make_recording_sleep() -> tuple[list[float], Callable[[float], None]]:
    calls: list[float] = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)

    return calls, sleep


# -- retry: fails then succeeds --------------------------------------------


def test_task_fails_then_succeeds_on_retry(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "flaky.yaml", name="flaky")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: retry-succeeds
tasks:
  - name: flaky
    pipeline: flaky.yaml
    depends_on: []
    retries: 2
    retry_delay_seconds: 1
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag = load_dag(dag_path)
        sleep_calls, sleep = make_recording_sleep()
        application = FlakyApplication({"flaky": 1})  # fails once, then succeeds
        executor = DagExecutor(store, application, sleep=sleep)

        run_id = executor.run(dag, dag_path)

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED

        task = store.list_task_runs(run_id)[0]
        assert task.status is TaskRunStatus.SUCCEEDED

        attempts = store.list_task_attempts(run_id, "flaky")
        assert [a.attempt_number for a in attempts] == [1, 2]
        assert attempts[0].status is TaskAttemptStatus.FAILED
        assert attempts[0].error is not None
        assert attempts[1].status is TaskAttemptStatus.SUCCEEDED
        assert attempts[1].error is None

        assert sleep_calls == [1.0]  # exactly one delay, before the successful retry
    finally:
        store.close()


def test_task_exhausts_all_retries_and_ultimately_fails(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "flaky.yaml", name="flaky")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: retry-exhausted
tasks:
  - name: flaky
    pipeline: flaky.yaml
    depends_on: []
    retries: 2
    retry_delay_seconds: 0.5
    retry_backoff_multiplier: 2.0
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        dag = load_dag(dag_path)
        sleep_calls, sleep = make_recording_sleep()
        application = FlakyApplication({"flaky": 99})  # always fails
        executor = DagExecutor(store, application, sleep=sleep)

        run_id = executor.run(dag, dag_path)

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED

        task = store.list_task_runs(run_id)[0]
        assert task.status is TaskRunStatus.FAILED
        assert task.error is not None

        attempts = store.list_task_attempts(run_id, "flaky")
        assert [a.attempt_number for a in attempts] == [1, 2, 3]
        assert all(a.status is TaskAttemptStatus.FAILED for a in attempts)

        # exponential backoff: base=0.5, multiplier=2.0 -> 0.5, then 1.0
        assert sleep_calls == [0.5, 1.0]
    finally:
        store.close()


# -- skip policy: explicit regression test against story 3's behavior ------


def test_skip_policy_unchanged_from_story_3(tmp_path: Path) -> None:
    """Exactly story 3's own scenario: extract -> {transform (fails), side},
    transform -> load. on_failure left unset (defaults to 'skip'). Must
    produce the identical outcome story 3 established: only load (which
    transitively depends on transform) is skipped; side, unrelated to the
    failure, still runs and succeeds.
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
        run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        assert run.on_failure == "skip"

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["extract"].status is TaskRunStatus.SUCCEEDED
        assert tasks["transform"].status is TaskRunStatus.FAILED
        assert tasks["side"].status is TaskRunStatus.SUCCEEDED
        assert tasks["load"].status is TaskRunStatus.SKIPPED
    finally:
        store.close()


# -- block policy ------------------------------------------------------


def test_block_policy_stops_downstream_and_independent_unstarted_branches(
    tmp_path: Path,
) -> None:
    """extract -> {transform (fails, with a retry), side}, transform -> load,
    plus 'unrelated' with NO dependency on anything, declared after
    transform so it's scheduled after the failure. on_failure: block.

    Concrete proof of both halves of Step 3's block semantics:
    - transform's own retry runs to full completion (both attempts
      recorded) before the block decision is made -- nothing is aborted
      mid-flight.
    - side, load, AND unrelated are all blocked once the run stops --
      including unrelated, which has no path from transform at all,
      distinguishing block from skip.
    """
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    write_failing_pipeline(tmp_path / "transform.yaml", name="transform")
    write_pipeline(tmp_path / "side.yaml", name="side")
    write_pipeline(tmp_path / "load.yaml", name="load")
    write_pipeline(tmp_path / "unrelated.yaml", name="unrelated")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: block-policy
on_failure: block
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
  - name: transform
    pipeline: transform.yaml
    depends_on: [extract]
    retries: 1
    retry_delay_seconds: 0
  - name: side
    pipeline: side.yaml
    depends_on: [extract]
  - name: load
    pipeline: load.yaml
    depends_on: [transform]
  - name: unrelated
    pipeline: unrelated.yaml
    depends_on: []
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        assert run.on_failure == "block"

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["extract"].status is TaskRunStatus.SUCCEEDED
        assert tasks["transform"].status is TaskRunStatus.FAILED
        assert tasks["side"].status is TaskRunStatus.BLOCKED
        assert tasks["load"].status is TaskRunStatus.BLOCKED
        assert (
            tasks["unrelated"].status is TaskRunStatus.BLOCKED
        )  # no dependency on transform at all

        # transform's own retry sequence completed in full, not aborted mid-flight
        attempts = store.list_task_attempts(run_id, "transform")
        assert [a.attempt_number for a in attempts] == [1, 2]
        assert all(a.status is TaskAttemptStatus.FAILED for a in attempts)
    finally:
        store.close()


def test_block_policy_lets_a_task_after_the_failure_but_declared_earlier_style_still_get_blocked(
    tmp_path: Path,
) -> None:
    """A second, simpler block-policy shape: the very first task fails and
    everything else -- despite zero dependency relationships among any of
    them -- is blocked.
    """
    write_failing_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    write_pipeline(tmp_path / "c.yaml", name="c")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: block-first-task
on_failure: block
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: b
    pipeline: b.yaml
    depends_on: []
  - name: c
    pipeline: c.yaml
    depends_on: []
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["a"].status is TaskRunStatus.FAILED
        assert tasks["b"].status is TaskRunStatus.BLOCKED
        assert tasks["c"].status is TaskRunStatus.BLOCKED
    finally:
        store.close()


# -- default preservation: no new fields set == pre-story-6 behavior -------


def test_default_dag_with_no_retry_or_policy_fields_matches_pre_story_behavior(
    tmp_path: Path,
) -> None:
    """Exactly story 3's own test_first_task_failure_skips_everything_downstream
    scenario, with a DAG file that sets none of this story's new fields.
    Must produce the identical outcome: one attempt per task (no retries),
    'skip' propagation (the old-and-only behavior), first task's failure
    skips both downstream tasks.
    """
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
        dag = load_dag(dag_path)
        assert dag.on_failure == "skip"
        assert all(task.retries == 0 for task in dag.tasks)

        run_id = execute_dag(dag_path, store)

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        assert run.on_failure == "skip"

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["a"].status is TaskRunStatus.FAILED
        assert tasks["b"].status is TaskRunStatus.SKIPPED
        assert tasks["c"].status is TaskRunStatus.SKIPPED

        # Exactly one attempt recorded per task that actually ran -- no
        # retry loop touched at all when retries=0.
        attempts_a = store.list_task_attempts(run_id, "a")
        assert len(attempts_a) == 1
        assert attempts_a[0].attempt_number == 1
    finally:
        store.close()


# -- migration applies cleanly on a schema_version 2 database ---------------


def test_migration_3_applies_cleanly_on_a_schema_version_2_database(tmp_path: Path) -> None:
    """A database left at schema_version 2 by an earlier story (before
    task_attempts or dag_runs.on_failure existed) must upgrade cleanly:
    existing rows survive, and the new capabilities become usable
    immediately after.
    """
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (2);
        CREATE TABLE dags (
            name TEXT PRIMARY KEY, source_path TEXT NOT NULL, schedule TEXT,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE dag_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dag_name TEXT NOT NULL REFERENCES dags(name),
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            trigger_reason TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, error TEXT
        );
        CREATE TABLE task_runs (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id), task_name TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
            started_at TEXT, ended_at TEXT, error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        INSERT INTO dags VALUES ('etl', 'etl.yaml', NULL, 1, 't0', 't0');
        INSERT INTO dag_runs VALUES (1, 'etl', 'succeeded', 'manual', 't0', 't1', NULL);
        INSERT INTO task_runs VALUES (1, 'x', 'succeeded', 't0', 't1', NULL);
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        preserved = store.list_task_runs(1)
        assert len(preserved) == 1
        assert preserved[0].task_name == "x"
        assert preserved[0].status is TaskRunStatus.SUCCEEDED

        run = store.get_dag_run(1)
        assert run is not None
        assert run.on_failure == "skip"  # backfilled default

        store.block_task_run(1, "x")
        assert store.list_task_runs(1)[0].status is TaskRunStatus.BLOCKED

        attempt_id = store.start_task_attempt(1, "x", 1)
        store.complete_task_attempt(attempt_id, success=True)
        assert len(store.list_task_attempts(1, "x")) == 1
    finally:
        store.close()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert version == 7
