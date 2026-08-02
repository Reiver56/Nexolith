"""Runs a validated DAG: each task's pipeline through the existing
`PipelineApplication`, in dependency order, recording live progress to a
`StateStore`. Orchestration only -- no new execution engine, no scheduler
loop, no parallelism. `execute_dag()` is the simple, complete trigger a
later CLI/scheduler story calls; `DagExecutor` is its lower-level building
block, useful directly in tests.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from nexolith.application import PipelineApplication
from nexolith.dag.models import DagConfig, DagTaskConfig
from nexolith.dag.validator import load_dag
from nexolith.events import EventSink
from nexolith.exceptions import ConfigurationError, ExecutionError
from nexolith.models import ExecutionResult
from nexolith.state import StateStore


class PipelineRunnerApplication(Protocol):
    """Duck-typed to `PipelineApplication`'s `run_pipeline()` -- matching
    `interactive.py`'s own `InteractiveApplication` Protocol pattern for
    dependency injection, rather than requiring an actual `PipelineApplication`
    subclass. `DagExecutor`'s default is still a real `PipelineApplication`.
    """

    def run_pipeline(
        self, path: Path, *, event_sink: EventSink | None = None
    ) -> ExecutionResult: ...


def _topological_order(tasks: list[DagTaskConfig]) -> list[DagTaskConfig]:
    """A valid execution order honoring every `depends_on` edge. Among
    tasks that are simultaneously ready, the deterministic tie-break is
    declaration order in the DAG file -- reproducible run history, not an
    arbitrary or set-iteration-order-dependent pick.

    Assumes `tasks` is acyclic. Raises `ConfigurationError` if it isn't --
    a defensive check, not a substitute for story 1's own cycle detection
    (`load_dag()` already runs that on every DAG this module is meant to
    receive); this only guards against a `DagConfig` built by hand and
    handed to `DagExecutor` directly, bypassing `load_dag()`.
    """
    index_by_name = {task.name: index for index, task in enumerate(tasks)}
    by_name = {task.name: task for task in tasks}
    ordered: list[DagTaskConfig] = []
    ordered_names: set[str] = set()

    while len(ordered) < len(tasks):
        ready = [
            name
            for name in by_name
            if name not in ordered_names and set(by_name[name].depends_on) <= ordered_names
        ]
        if not ready:
            remaining = sorted(set(by_name) - ordered_names)
            raise ConfigurationError(
                "Cannot compute an execution order -- a cycle exists among: " + ", ".join(remaining)
            )
        chosen = min(ready, key=lambda name: index_by_name[name])
        ordered.append(by_name[chosen])
        ordered_names.add(chosen)
    return ordered


def _transitive_dependents(tasks: list[DagTaskConfig], failed: set[str]) -> set[str]:
    """Every task name that depends, directly or transitively, on any name
    in `failed` -- an explicit BFS over the reverse of `depends_on` (i.e.
    'depended on by') edges, not inferred or approximated from anything
    else. Independent branches sharing no such path are never included.
    """
    dependents_of: dict[str, list[str]] = {task.name: [] for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            dependents_of[dependency].append(task.name)

    to_skip: set[str] = set()
    queue = list(failed)
    while queue:
        name = queue.pop()
        for dependent in dependents_of.get(name, []):
            if dependent not in to_skip and dependent not in failed:
                to_skip.add(dependent)
                queue.append(dependent)
    return to_skip


def _resolve_pipeline_path(task: DagTaskConfig, base_dir: Path) -> Path:
    pipeline_path = Path(task.pipeline)
    if not pipeline_path.is_absolute():
        pipeline_path = base_dir / pipeline_path
    return pipeline_path


class DagExecutor:
    """Executes one validated `DagConfig` against a `StateStore`, calling
    `PipelineApplication.run_pipeline()` per task -- the same entrypoint the
    CLI's `/run` and classic `run` command already use. Sequential only:
    independent branches run one after another, not in parallel (explicitly
    out of scope for this story).

    Because execution is strictly sequential, at most one task is ever "in
    progress" at a time -- so honoring `on_failure="block"`'s requirement
    to let an already-in-progress task finish rather than aborting it
    mid-flight needs no special handling: the failing task's own retries
    already run to completion (see `_run_task_with_retries`) before the
    block decision is ever evaluated, by construction.
    """

    def __init__(
        self,
        store: StateStore,
        application: PipelineRunnerApplication | None = None,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._store = store
        self._application = application or PipelineApplication()
        # Injectable so tests never sleep for real wall-clock seconds
        # waiting out a retry delay; a real DagExecutor uses real time.sleep.
        # No thread/timer here either way -- a retry is "try again", not
        # "try again on a timer in the background".
        self._sleep = sleep or time.sleep

    def run(self, dag: DagConfig, path: Path, *, trigger_reason: str = "manual") -> int:
        """`path` is the DAG file itself (the same path `load_dag()` was
        given) -- its directory is where `pipeline:` references resolve
        from, and the path itself is what gets registered in `dags` if this
        DAG name hasn't been registered yet.
        """
        base_dir = path.parent
        if self._store.get_dag(dag.name) is None:
            # First time this DAG has been executed under this name: register
            # it so dag_runs' foreign key to dags is satisfiable. An already
            # -registered DAG (e.g. with a real schedule set by a later CLI
            # story) is left untouched -- this must never clobber it back to
            # an unscheduled registration on every run.
            self._store.register_dag(dag.name, path, schedule=None)

        ordered = _topological_order(dag.tasks)
        dag_run_id = self._store.start_dag_run(
            dag.name,
            [task.name for task in ordered],
            trigger_reason=trigger_reason,
            on_failure=dag.on_failure,
        )

        failed_tasks: set[str] = set()
        dag_failed = False
        first_error: str | None = None
        # Once True (on_failure="block" and some task ultimately failed),
        # every remaining task is blocked outright -- independent branches
        # included, not just tasks transitively dependent on the failure.
        # With on_failure="skip" (the default) this never becomes True, so
        # behavior is identical to before this story: only the
        # _transitive_dependents() skip check below ever applies.
        blocked = False

        for task in ordered:
            if blocked:
                self._store.block_task_run(dag_run_id, task.name)
                continue
            if task.name in _transitive_dependents(dag.tasks, failed_tasks):
                self._store.skip_task_run(dag_run_id, task.name)
                continue

            self._store.start_task_run(dag_run_id, task.name)
            pipeline_path = _resolve_pipeline_path(task, base_dir)
            success, error = self._run_task_with_retries(dag_run_id, task, pipeline_path)
            self._store.complete_task_run(dag_run_id, task.name, success=success, error=error)

            if not success:
                failed_tasks.add(task.name)
                dag_failed = True
                if first_error is None:
                    first_error = f"Task '{task.name}' failed: {error}"
                if dag.on_failure == "block":
                    blocked = True

        self._store.complete_dag_run(dag_run_id, success=not dag_failed, error=first_error)
        return dag_run_id

    def _run_task_with_retries(
        self, dag_run_id: int, task: DagTaskConfig, pipeline_path: Path
    ) -> tuple[bool, str | None]:
        """Try `task` up to `1 + task.retries` times (retries=0, the
        default, means exactly one attempt -- today's exact pre-existing
        behavior). Each attempt is a real call to `PipelineApplication.
        run_pipeline()`, recorded to the store as its own task_attempts row
        the moment it starts and the moment it finishes -- never batched,
        never simulated, satisfying "no silent retries" directly rather
        than by convention.
        """
        max_attempts = 1 + task.retries
        last_error: str | None = None
        for attempt_number in range(1, max_attempts + 1):
            if attempt_number > 1:
                delay = task.retry_delay_seconds * (
                    task.retry_backoff_multiplier ** (attempt_number - 2)
                )
                if delay > 0:
                    self._sleep(delay)

            attempt_id = self._store.start_task_attempt(dag_run_id, task.name, attempt_number)
            try:
                self._application.run_pipeline(pipeline_path)
            except (ConfigurationError, ExecutionError) as exc:
                last_error = str(exc)
                self._store.complete_task_attempt(attempt_id, success=False, error=last_error)
                continue
            else:
                self._store.complete_task_attempt(attempt_id, success=True)
                return True, None
        return False, last_error


def execute_dag(
    path: Path,
    store: StateStore,
    application: PipelineRunnerApplication | None = None,
    *,
    trigger_reason: str = "manual",
) -> int:
    """Load, fully validate (via `load_dag()` -- structure, cycles, and
    every referenced pipeline), and execute the DAG at `path`. The one
    entrypoint a later CLI command or scheduler daemon needs to trigger a
    run; guarantees an unvalidated `DagConfig` never reaches the executor.
    Returns the new `dag_runs` row's id.
    """
    dag = load_dag(path)
    return DagExecutor(store, application).run(dag, path, trigger_reason=trigger_reason)
