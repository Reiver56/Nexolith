from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.cli.app import app
from nexolith.state import DagRunStatus, StateStore, TaskRunStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("NEXOLITH_STATE_DIR", str(state_dir))
    return state_dir


def write_pipeline(path: Path, *, name: str = "cli_pipeline") -> None:
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


def write_dag(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_validate_real_dag_through_cli() -> None:
    repository_root = Path(__file__).parents[2]

    result = runner.invoke(app, ["validate", str(repository_root / "examples/medallion/dag.yaml")])

    assert result.exit_code == 0
    assert result.stdout == "DAG 'medallion_orders' is valid (3 tasks).\n"
    assert result.stderr == ""


def test_validate_cyclic_dag_through_cli(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    dag = tmp_path / "cycle.yaml"
    write_dag(
        dag,
        """
name: cycle
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: [b]
  - name: b
    pipeline: b.yaml
    depends_on: [a]
""",
    )

    result = runner.invoke(app, ["validate", str(dag)])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error [configuration]: Cycle detected in DAG 'cycle': a -> b -> a" in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_dag_with_missing_task_parameter_through_cli(tmp_path: Path) -> None:
    pipeline = tmp_path / "parameterized.yaml"
    pipeline.write_text(
        f"""
name: parameterized
source:
  type: sqlite
  connection_url: sqlite:///{(tmp_path / "items.db").as_posix()}
  query: "SELECT id FROM items WHERE status = :status"
  parameters:
    status: null
destination:
  type: csv
  path: {(tmp_path / "output.csv").as_posix()}
""",
        encoding="utf-8",
    )
    dag = tmp_path / "missing-parameter.yaml"
    write_dag(
        dag,
        """
name: missing_parameter
tasks:
  - name: query
    pipeline: parameterized.yaml
    depends_on: []
""",
    )

    result = runner.invoke(app, ["validate", str(dag)])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Task 'query' references an invalid pipeline" in result.stderr
    assert "Missing required parameter(s): status" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_real_dag_through_cli_records_and_renders_run(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    dag = tmp_path / "workflow.yaml"
    write_dag(
        dag,
        """
name: cli_dag
schedule: 5m
trigger:
  on_success_of: [upstream_dag]
priority: critical
severity: high
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
""",
    )

    result = runner.invoke(app, ["run", str(dag)])

    assert result.exit_code == 0
    assert "Status: succeeded" in result.stdout
    assert "DAG: cli_dag" in result.stdout
    assert "Trigger: manual" in result.stdout
    assert "Severity: high" in result.stdout
    assert "Tasks:" in result.stdout
    assert "extract" in result.stdout
    assert result.stderr == ""

    store = StateStore()
    try:
        runs = store.list_dag_runs("cli_dag")
        assert len(runs) == 1
        assert runs[0].status is DagRunStatus.SUCCEEDED
        tasks = store.list_task_runs(runs[0].id)
        assert len(tasks) == 1
        assert tasks[0].status is TaskRunStatus.SUCCEEDED
        registered = store.get_dag("cli_dag")
        assert registered is not None
        assert registered.schedule == "5m"
    finally:
        store.close()


def test_failed_dag_run_through_cli_is_recorded_rendered_and_nonzero(tmp_path: Path) -> None:
    pipeline = tmp_path / "failing.yaml"
    write_pipeline(pipeline, name="failing")
    pipeline.with_suffix(".csv").unlink()
    dag = tmp_path / "workflow.yaml"
    write_dag(
        dag,
        """
name: failing_dag
tasks:
  - name: failing
    pipeline: failing.yaml
    depends_on: []
""",
    )

    result = runner.invoke(app, ["run", str(dag)])

    assert result.exit_code == 3
    assert "Status: failed" in result.stdout
    assert "DAG: failing_dag" in result.stdout
    assert "[FAIL] failing" in result.stdout
    assert "Traceback" not in result.output

    store = StateStore()
    try:
        runs = store.list_dag_runs("failing_dag")
        assert len(runs) == 1
        assert runs[0].status is DagRunStatus.FAILED
    finally:
        store.close()


def test_classic_pipeline_cli_output_remains_unchanged(tmp_path: Path) -> None:
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(pipeline)

    validation = runner.invoke(app, ["validate", str(pipeline)])
    execution = runner.invoke(app, ["run", str(pipeline)])

    assert validation.exit_code == 0
    assert validation.stdout == "Pipeline 'cli_pipeline' is valid.\n"
    assert validation.stderr == ""
    lines = execution.stdout.splitlines()
    assert lines[:4] == [
        "Pipeline: cli_pipeline",
        "Status: succeeded",
        "Rows read: 2",
        "Rows written: 2",
    ]
    assert len(lines) == 5
    assert re.fullmatch(r"Duration: \d+\.\d{3}s", lines[4])
    assert execution.stderr == ""


def test_dag_register_creates_a_new_registration_without_running_any_task(tmp_path: Path) -> None:
    """NXL-103: `nexolith dag register` creates the `dags` row (schedule,
    enabled) straight from the file -- confirmed by checking the row
    directly, not just the command's own echoed confirmation -- and, unlike
    `run`, never executes a task (no dag_runs row at all).
    """
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    dag = tmp_path / "workflow.yaml"
    write_dag(
        dag,
        """
name: register_only_dag
schedule: 10m
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
""",
    )

    result = runner.invoke(app, ["dag", "register", str(dag)])

    assert result.exit_code == 0
    assert result.stdout == "DAG 'register_only_dag' registered (schedule: 10m, enabled).\n"
    assert result.stderr == ""

    store = StateStore()
    try:
        registered = store.get_dag("register_only_dag")
        assert registered is not None
        assert registered.schedule == "10m"
        assert registered.enabled is True
        assert registered.source_path == str(dag)
        assert store.list_dag_runs("register_only_dag") == []  # never run
    finally:
        store.close()


def test_dag_register_an_already_registered_dag_is_a_no_op_without_force(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    dag = tmp_path / "workflow.yaml"
    write_dag(
        dag,
        """
name: idempotent_dag
schedule: 5m
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
""",
    )
    first = runner.invoke(app, ["dag", "register", str(dag)])
    assert first.exit_code == 0

    # Edit the file's schedule after the first registration -- re-running
    # register without --force must not pick this up (the same
    # never-clobber-a-schedule convention DagExecutor.run() already applies
    # to a manual run, deliberately extended here to this command too).
    write_dag(
        dag,
        """
name: idempotent_dag
schedule: 30m
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
""",
    )

    second = runner.invoke(app, ["dag", "register", str(dag)])

    assert second.exit_code == 0
    assert "already registered" in second.stdout
    assert "schedule: 5m" in second.stdout
    assert "--force" in second.stdout

    store = StateStore()
    try:
        registered = store.get_dag("idempotent_dag")
        assert registered is not None
        assert registered.schedule == "5m"  # unchanged
    finally:
        store.close()


def test_dag_register_with_force_updates_schedule_but_preserves_enabled_state(
    tmp_path: Path,
) -> None:
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    dag = tmp_path / "workflow.yaml"
    write_dag(
        dag,
        """
name: forced_dag
schedule: 5m
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
""",
    )
    first = runner.invoke(app, ["dag", "register", str(dag)])
    assert first.exit_code == 0

    store = StateStore()
    try:
        store.set_dag_enabled("forced_dag", False)  # simulate an earlier explicit disable
    finally:
        store.close()

    write_dag(
        dag,
        """
name: forced_dag
schedule: 30m
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
""",
    )

    forced = runner.invoke(app, ["dag", "register", str(dag), "--force"])

    assert forced.exit_code == 0
    assert forced.stdout == "DAG 'forced_dag' registration updated (schedule: 30m, disabled).\n"

    store = StateStore()
    try:
        registered = store.get_dag("forced_dag")
        assert registered is not None
        assert registered.schedule == "30m"  # synced from the file
        assert registered.enabled is False  # the earlier disable survives --force
    finally:
        store.close()


def test_dag_register_invalid_dag_file_fails_validation_without_creating_a_row(
    tmp_path: Path,
) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    dag = tmp_path / "cycle.yaml"
    write_dag(
        dag,
        """
name: cycle
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: [b]
  - name: b
    pipeline: b.yaml
    depends_on: [a]
""",
    )

    result = runner.invoke(app, ["dag", "register", str(dag)])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error [configuration]: Cycle detected in DAG 'cycle': a -> b -> a" in result.stderr
    assert "Traceback" not in result.stderr

    store = StateStore()
    try:
        assert store.get_dag("cycle") is None
    finally:
        store.close()
