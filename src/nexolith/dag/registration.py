"""Registers a DAG's `dags` row directly, without executing it (NXL-103) --
the counterpart to `DagExecutor.run()`'s check-then-register side effect,
for enabling scheduled execution without a manual first run. The scheduler
daemon (`nexolith.scheduler.daemon.Scheduler.tick()`) only ever looks at
`StateStore.list_dags()`; it has no notion of "run once to get registered",
so this is the only other way a DAG becomes visible to it.
"""

from dataclasses import dataclass
from pathlib import Path

from nexolith.dag.models import DagConfig
from nexolith.dag.validator import load_dag
from nexolith.state import DagRecord, StateStore


@dataclass(frozen=True, slots=True)
class DagRegistrationResult:
    dag: DagConfig
    record: DagRecord
    created: bool
    updated: bool


def register_dag(path: Path, store: StateStore, *, force: bool = False) -> DagRegistrationResult:
    """Validate the DAG file at `path` (the same `load_dag()` every other DAG
    entrypoint uses -- structure, cycles, and every referenced pipeline) and
    create or update its `dags` row: `schedule` and `source_path` read from
    the file, `enabled=True` on first registration. No task is executed.

    An already-registered DAG is left untouched unless `force=True` --
    mirrors `DagExecutor.run()`'s own "never silently clobber a schedule"
    convention (see that method's docstring), applied here as an explicit
    opt-in rather than a blanket default: unlike a manual run, where
    registration is only an incidental side effect, re-registering *is*
    this function's entire declared purpose, so a caller that actually
    wants to sync a changed `schedule:` needs a way to ask for it. Even
    when forced, the DAG's current `enabled` state survives the update
    unchanged -- `force` re-syncs `schedule`/`source_path` from the file,
    it is not a way to silently re-enable a DAG something else disabled.
    """
    dag = load_dag(path)
    existing = store.get_dag(dag.name)

    if existing is None:
        store.register_dag(dag.name, path, schedule=dag.schedule)
        created_record = store.get_dag(dag.name)
        assert created_record is not None
        return DagRegistrationResult(dag, created_record, created=True, updated=False)

    if not force:
        return DagRegistrationResult(dag, existing, created=False, updated=False)

    store.register_dag(dag.name, path, schedule=dag.schedule, enabled=existing.enabled)
    updated_record = store.get_dag(dag.name)
    assert updated_record is not None
    return DagRegistrationResult(dag, updated_record, created=False, updated=True)
