"""Cross-DAG triggers (NXL-85): a DAG's `trigger.on_success_of` names one
or more upstream DAGs; the scheduler reacts when any of them completes
successfully. Real `execute_dag()` calls throughout -- not mocked -- per
the story's own explicit ask; only the *upstream* pipelines are trivial
CSV passthroughs, kept minimal since what's under test is the trigger
mechanism, not pipeline logic.
"""

from pathlib import Path

import pytest

from nexolith.dag import execute_dag, load_dag
from nexolith.exceptions import ConfigurationError
from nexolith.scheduler import Scheduler
from nexolith.state import DagRunStatus, StateStore


def make_store(tmp_path: Path, name: str = "state.db") -> StateStore:
    return StateStore(tmp_path / name)


def write_pipeline(path: Path, *, name: str, fail: bool = False) -> None:
    """A trivial, real, executable pipeline -- succeeds by default; `fail`
    points its source at a file that doesn't exist, a genuine execution
    failure (matching the project's existing convention for "a real
    failure, not simulated" elsewhere in the test suite).
    """
    source = path.with_suffix(".csv")
    if not fail:
        source.write_text("id,status\n1,ready\n", encoding="utf-8")
    path.write_text(
        f"""
name: {name}
source:
  type: csv
  path: {(path.parent / "does_not_exist.csv").as_posix() if fail else source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(path.parent / f"{name}_out.csv").as_posix()}
""",
        encoding="utf-8",
    )


def write_dag(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def register_downstream(
    store: StateStore, name: str, path: Path, *, schedule: str | None = None
) -> None:
    """Downstream DAGs need a `dags` row before the scheduler's `tick()`
    (which iterates `list_dags()`) can ever evaluate them -- today's only
    way to get one is `register_dag()` itself (what `DagExecutor.run()`
    calls automatically on a DAG's own first execution) or running the DAG
    once manually first. Calling it directly here is exactly what a real
    downstream DAG needs before the scheduler can discover it; a real,
    pre-existing bootstrapping requirement, not a test shortcut around one.
    """
    store.register_dag(name, path, schedule, enabled=True)


# -- basic trigger + no-double-trigger --------------------------------------


def test_downstream_triggers_when_upstream_completes_successfully(tmp_path: Path) -> None:
    upstream_path = tmp_path / "upstream.yaml"
    write_pipeline(upstream_path, name="upstream_pipeline")
    write_dag(
        tmp_path / "upstream_dag.yaml",
        """
name: upstream
tasks:
  - name: only
    pipeline: upstream.yaml
    depends_on: []
""",
    )
    upstream_dag_path = tmp_path / "upstream_dag.yaml"

    downstream_path = tmp_path / "downstream.yaml"
    write_pipeline(downstream_path, name="downstream_pipeline")
    downstream_dag_path = tmp_path / "downstream_dag.yaml"
    write_dag(
        downstream_dag_path,
        """
name: downstream
trigger:
  on_success_of: [upstream]
tasks:
  - name: only
    pipeline: downstream.yaml
    depends_on: []
""",
    )

    store = make_store(tmp_path)
    try:
        register_downstream(store, "downstream", downstream_dag_path)
        scheduler = Scheduler(store)

        # No upstream completion yet: not due.
        assert scheduler.tick() == []
        assert store.latest_dag_run("downstream") is None

        upstream_run_id = execute_dag(upstream_dag_path, store)
        upstream_run = store.get_dag_run(upstream_run_id)
        assert upstream_run is not None
        assert upstream_run.status is DagRunStatus.SUCCEEDED

        triggered = scheduler.tick()

        assert len(triggered) == 1
        downstream_run = store.get_dag_run(triggered[0])
        assert downstream_run is not None
        assert downstream_run.dag_name == "downstream"
        assert downstream_run.status is DagRunStatus.SUCCEEDED
        assert downstream_run.trigger_reason == "schedule"
    finally:
        store.close()


def test_downstream_does_not_retrigger_off_the_same_upstream_completion_twice(
    tmp_path: Path,
) -> None:
    upstream_path = tmp_path / "upstream.yaml"
    write_pipeline(upstream_path, name="upstream_pipeline")
    upstream_dag_path = tmp_path / "upstream_dag.yaml"
    write_dag(
        upstream_dag_path,
        """
name: upstream
tasks:
  - name: only
    pipeline: upstream.yaml
    depends_on: []
""",
    )
    downstream_path = tmp_path / "downstream.yaml"
    write_pipeline(downstream_path, name="downstream_pipeline")
    downstream_dag_path = tmp_path / "downstream_dag.yaml"
    write_dag(
        downstream_dag_path,
        """
name: downstream
trigger:
  on_success_of: [upstream]
tasks:
  - name: only
    pipeline: downstream.yaml
    depends_on: []
""",
    )

    store = make_store(tmp_path)
    try:
        register_downstream(store, "downstream", downstream_dag_path)
        scheduler = Scheduler(store)

        execute_dag(upstream_dag_path, store)
        first_tick = scheduler.tick()
        assert len(first_tick) == 1

        # Same upstream completion, no new one since: must not re-trigger.
        second_tick = scheduler.tick()
        third_tick = scheduler.tick()

        assert second_tick == []
        assert third_tick == []
        assert len(store.list_dag_runs("downstream")) == 1
    finally:
        store.close()


# -- multiple upstream sources -----------------------------------------------


def test_downstream_with_multiple_upstreams_triggers_on_either_one(tmp_path: Path) -> None:
    """Each declared upstream is independently capable of triggering the
    downstream -- confirmed with two separate ticks, one per upstream's own
    completion, each producing its own downstream run."""
    for name in ("a", "b"):
        write_pipeline(tmp_path / f"{name}.yaml", name=f"{name}_pipeline")
        write_dag(
            tmp_path / f"{name}_dag.yaml",
            f"""
name: {name}
tasks:
  - name: only
    pipeline: {name}.yaml
    depends_on: []
""",
        )
    downstream_path = tmp_path / "downstream.yaml"
    write_pipeline(downstream_path, name="downstream_pipeline")
    downstream_dag_path = tmp_path / "downstream_dag.yaml"
    write_dag(
        downstream_dag_path,
        """
name: downstream
trigger:
  on_success_of: [a, b]
tasks:
  - name: only
    pipeline: downstream.yaml
    depends_on: []
""",
    )

    store = make_store(tmp_path)
    try:
        register_downstream(store, "downstream", downstream_dag_path)
        scheduler = Scheduler(store)

        execute_dag(tmp_path / "a_dag.yaml", store)
        assert len(scheduler.tick()) == 1
        assert len(store.list_dag_runs("downstream")) == 1

        # b hasn't completed yet -- no new trigger from a alone again.
        assert scheduler.tick() == []

        execute_dag(tmp_path / "b_dag.yaml", store)
        assert len(scheduler.tick()) == 1
        assert len(store.list_dag_runs("downstream")) == 2
    finally:
        store.close()


def test_simultaneous_multi_upstream_completion_does_not_crash_or_duplicate(
    tmp_path: Path,
) -> None:
    """Both upstream DAGs complete before the next tick ever runs -- the
    real "close together" scenario the story asks to confirm doesn't crash
    or double-trigger. One tick must produce exactly one downstream run
    (not two), and both upstream reactions must be recorded so a further
    tick doesn't force an immediate, redundant second run for whichever one
    "would have" been evaluated second.
    """
    for name in ("a", "b"):
        write_pipeline(tmp_path / f"{name}.yaml", name=f"{name}_pipeline")
        write_dag(
            tmp_path / f"{name}_dag.yaml",
            f"""
name: {name}
tasks:
  - name: only
    pipeline: {name}.yaml
    depends_on: []
""",
        )
    downstream_path = tmp_path / "downstream.yaml"
    write_pipeline(downstream_path, name="downstream_pipeline")
    downstream_dag_path = tmp_path / "downstream_dag.yaml"
    write_dag(
        downstream_dag_path,
        """
name: downstream
trigger:
  on_success_of: [a, b]
tasks:
  - name: only
    pipeline: downstream.yaml
    depends_on: []
""",
    )

    store = make_store(tmp_path)
    try:
        register_downstream(store, "downstream", downstream_dag_path)
        scheduler = Scheduler(store)

        execute_dag(tmp_path / "a_dag.yaml", store)
        execute_dag(tmp_path / "b_dag.yaml", store)  # both complete before any tick

        triggered = scheduler.tick()  # must not raise

        assert len(triggered) == 1  # exactly one downstream run, not two
        assert len(store.list_dag_runs("downstream")) == 1

        # Neither upstream is "still unreacted" -- no redundant re-trigger.
        assert scheduler.tick() == []
        assert len(store.list_dag_runs("downstream")) == 1
    finally:
        store.close()


# -- on_success_of and failure -----------------------------------------------


def test_on_success_of_does_not_trigger_when_upstream_fails(tmp_path: Path) -> None:
    upstream_path = tmp_path / "upstream.yaml"
    write_pipeline(upstream_path, name="upstream_pipeline", fail=True)
    upstream_dag_path = tmp_path / "upstream_dag.yaml"
    write_dag(
        upstream_dag_path,
        """
name: upstream
tasks:
  - name: only
    pipeline: upstream.yaml
    depends_on: []
""",
    )
    downstream_path = tmp_path / "downstream.yaml"
    write_pipeline(downstream_path, name="downstream_pipeline")
    downstream_dag_path = tmp_path / "downstream_dag.yaml"
    write_dag(
        downstream_dag_path,
        """
name: downstream
trigger:
  on_success_of: [upstream]
tasks:
  - name: only
    pipeline: downstream.yaml
    depends_on: []
""",
    )

    store = make_store(tmp_path)
    try:
        register_downstream(store, "downstream", downstream_dag_path)
        scheduler = Scheduler(store)

        upstream_run_id = execute_dag(upstream_dag_path, store)
        upstream_run = store.get_dag_run(upstream_run_id)
        assert upstream_run is not None
        assert upstream_run.status is DagRunStatus.FAILED

        triggered = scheduler.tick()

        assert triggered == []
        assert store.latest_dag_run("downstream") is None
    finally:
        store.close()


# -- interval + cross-DAG together -------------------------------------------


def test_dag_with_both_interval_and_cross_dag_trigger_configured(tmp_path: Path) -> None:
    """Either condition alone is enough: interval-due with no upstream
    completion still triggers, and (separately) an upstream completion
    still triggers even when the interval isn't due yet.
    """
    upstream_path = tmp_path / "upstream.yaml"
    write_pipeline(upstream_path, name="upstream_pipeline")
    upstream_dag_path = tmp_path / "upstream_dag.yaml"
    write_dag(
        upstream_dag_path,
        """
name: upstream
tasks:
  - name: only
    pipeline: upstream.yaml
    depends_on: []
""",
    )
    downstream_path = tmp_path / "downstream.yaml"
    write_pipeline(downstream_path, name="downstream_pipeline")
    downstream_dag_path = tmp_path / "downstream_dag.yaml"
    write_dag(
        downstream_dag_path,
        """
name: downstream
trigger:
  on_success_of: [upstream]
tasks:
  - name: only
    pipeline: downstream.yaml
    depends_on: []
""",
    )

    store = make_store(tmp_path)
    try:
        # A long interval ("1h"): due only on its very first tick (no prior
        # run), exactly like any other interval-only DAG.
        register_downstream(store, "downstream", downstream_dag_path, schedule="1h")
        scheduler = Scheduler(store)

        triggered_by_interval = scheduler.tick()
        assert len(triggered_by_interval) == 1  # interval alone, no upstream completion yet

        # Not due again by interval so soon; but a fresh upstream
        # completion still triggers via the cross-DAG condition.
        assert scheduler.tick() == []
        execute_dag(upstream_dag_path, store)
        triggered_by_upstream = scheduler.tick()
        assert len(triggered_by_upstream) == 1
        assert len(store.list_dag_runs("downstream")) == 2
    finally:
        store.close()


# -- config validation --------------------------------------------------


def test_dag_cannot_list_itself_as_its_own_trigger_source(tmp_path: Path) -> None:
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: self_trigger
trigger:
  on_success_of: [self_trigger]
tasks:
  - name: only
    pipeline: pipeline.yaml
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError, match="cannot list itself"):
        load_dag(dag_path)


def test_trigger_requires_at_least_one_upstream_name(tmp_path: Path) -> None:
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: empty_trigger
trigger:
  on_success_of: []
tasks:
  - name: only
    pipeline: pipeline.yaml
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError, match="Invalid DAG configuration"):
        load_dag(dag_path)
