"""DAG severity classification (NXL-87): the Step 2 design question this
story had to resolve deliberately -- snapshot into dag_runs at run-start
time, like on_failure (NXL-79), rather than read fresh from the file every
time, like schedule/trigger/priority. These tests exercise exactly that
decision: a run's recorded severity must reflect what the file said *at
the time that run started*, immune to a later edit of the same file.
"""

from pathlib import Path

from nexolith.dag import execute_dag
from nexolith.state import StateStore


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


def write_dag(path: Path, *, severity: str | None) -> None:
    severity_line = f"severity: {severity}\n" if severity is not None else ""
    path.write_text(
        f"""
name: severity_dag
{severity_line}tasks:
  - name: only
    pipeline: pipeline.yaml
    depends_on: []
""",
        encoding="utf-8",
    )


def make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def test_declared_severity_is_recorded_with_the_run(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "pipeline.yaml", name="pipeline")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(dag_path, severity="critical")

    store = make_store(tmp_path)
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.severity == "critical"
    finally:
        store.close()


def test_editing_the_files_severity_does_not_change_a_past_runs_recorded_value(
    tmp_path: Path,
) -> None:
    """The exact Step 2 scenario: run once at severity: critical, edit the
    file to severity: low, run again -- the ORIGINAL run must still report
    critical (a snapshot, not a live read), while the new run reports low.
    """
    write_pipeline(tmp_path / "pipeline.yaml", name="pipeline")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(dag_path, severity="critical")

    store = make_store(tmp_path)
    try:
        first_run_id = execute_dag(dag_path, store)

        write_dag(dag_path, severity="low")
        second_run_id = execute_dag(dag_path, store)

        first_run = store.get_dag_run(first_run_id)
        second_run = store.get_dag_run(second_run_id)
        assert first_run is not None
        assert second_run is not None
        assert first_run.severity == "critical"  # unaffected by the later edit
        assert second_run.severity == "low"
    finally:
        store.close()


def test_a_dag_with_no_declared_severity_records_the_default(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "pipeline.yaml", name="pipeline")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(dag_path, severity=None)

    store = make_store(tmp_path)
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.severity == "medium"
    finally:
        store.close()


def test_severity_is_present_in_every_recorded_run_query(tmp_path: Path) -> None:
    """The store's every read path for dag_runs (get/list/latest/recent)
    goes through the same _dag_run_record() helper -- confirm severity
    survives all of them, not just get_dag_run().
    """
    write_pipeline(tmp_path / "pipeline.yaml", name="pipeline")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(dag_path, severity="high")

    store = make_store(tmp_path)
    try:
        run_id = execute_dag(dag_path, store)

        assert store.get_dag_run(run_id).severity == "high"  # type: ignore[union-attr]
        assert store.list_dag_runs("severity_dag")[0].severity == "high"
        assert store.list_recent_dag_runs()[0].severity == "high"
        assert store.latest_dag_run("severity_dag").severity == "high"  # type: ignore[union-attr]
    finally:
        store.close()
