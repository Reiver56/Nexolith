"""Typed DAG actions shared by presentation adapters."""

from dataclasses import dataclass
from pathlib import Path

from nexolith.dag import DagRegistrationResult, execute_dag, register_dag
from nexolith.exceptions import ConfigurationError
from nexolith.state import DagRunStatus, StateStore


class RegisteredDagNotFoundError(LookupError):
    """The requested registered DAG does not exist."""


class RegisteredDagSourceMissingError(RuntimeError):
    """The persisted source path no longer names a file."""


class RegisteredDagConfigurationError(RuntimeError):
    """The persisted source no longer validates as the registered DAG."""


@dataclass(frozen=True, slots=True)
class DagRunActionResult:
    run_id: int
    dag_name: str
    status: DagRunStatus


class DagActionService:
    """Run explicit DAG mutations without depending on CLI or HTTP types."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    def register(self, path: Path, *, force: bool = False) -> DagRegistrationResult:
        return register_dag(path, self._store, force=force)

    def trigger(self, dag_name: str) -> DagRunActionResult:
        record = self._store.get_dag(dag_name)
        if record is None:
            raise RegisteredDagNotFoundError(dag_name)
        source_path = Path(record.source_path)
        if not source_path.is_file():
            raise RegisteredDagSourceMissingError(dag_name)
        try:
            run_id = execute_dag(
                source_path,
                self._store,
                trigger_reason="api",
                expected_name=dag_name,
            )
        except ConfigurationError as exc:
            raise RegisteredDagConfigurationError(dag_name) from exc
        run = self._store.get_dag_run(run_id)
        if run is None:
            raise RuntimeError("DAG action completed without persisted run history")
        return DagRunActionResult(run_id=run.id, dag_name=run.dag_name, status=run.status)
