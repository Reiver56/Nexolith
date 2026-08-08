"""Model B (NXL-88, ADR-7) -- autonomous script job steps, run as a DAG
task alternative to `pipeline:`. Unlike test_python_job.py (Model A,
in-process, one pipeline's single destination), these tests exercise the
validated use case Model A cannot fit at all: a single read that fans out
into multiple independently-filtered exports with its own I/O.
"""

import csv
import sqlite3
import sys
from pathlib import Path

import pytest

from nexolith.dag import execute_dag, load_dag
from nexolith.exceptions import ConfigurationError
from nexolith.state import DagRunStatus, StateStore, TaskRunStatus


def write_dag(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def make_customers_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE customers (id INTEGER, name TEXT, segment TEXT)")
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [
            (1, "Ada", "starter"),
            (2, "Grace", "pro"),
            (3, "Marie", "enterprise"),
            (4, "Rosalind", "pro"),
        ],
    )
    connection.commit()
    connection.close()


_FAN_OUT_SCRIPT = """
import csv
import sqlite3

def run(context):
    database = context.parameters["database"]
    out_dir = context.parameters["out_dir"]
    connection = sqlite3.connect(database)
    rows = connection.execute("SELECT id, name, segment FROM customers").fetchall()
    connection.close()

    by_segment = {"starter": [], "pro": [], "enterprise": []}
    for row_id, name, segment in rows:
        by_segment[segment].append((row_id, name, segment))

    for segment, segment_rows in by_segment.items():
        with open(f"{out_dir}/{segment}.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "name", "segment"])
            writer.writerows(segment_rows)
"""


def test_single_read_fans_out_to_multiple_exports_via_a_real_dag_run(tmp_path: Path) -> None:
    """The real validated use case: one script, one database read, three
    independently-filtered CSV exports -- a shape Model A's single-pipeline,
    single-destination contract cannot express at all."""
    database = tmp_path / "customers.db"
    make_customers_db(database)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    script = tmp_path / "fan_out.py"
    script.write_text(_FAN_OUT_SCRIPT, encoding="utf-8")

    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        f"""
name: fan_out_exports
tasks:
  - name: export_by_segment
    script: fan_out.py
    depends_on: []
    parameters:
      database: {database.as_posix()!r}
      out_dir: {out_dir.as_posix()!r}
""",
    )

    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
    finally:
        store.close()

    with (out_dir / "starter.csv").open(newline="", encoding="utf-8") as handle:
        starter_rows = list(csv.DictReader(handle))
    with (out_dir / "pro.csv").open(newline="", encoding="utf-8") as handle:
        pro_rows = list(csv.DictReader(handle))
    with (out_dir / "enterprise.csv").open(newline="", encoding="utf-8") as handle:
        enterprise_rows = list(csv.DictReader(handle))

    assert [row["name"] for row in starter_rows] == ["Ada"]
    assert {row["name"] for row in pro_rows} == {"Grace", "Rosalind"}
    assert [row["name"] for row in enterprise_rows] == ["Marie"]


def test_script_parameters_reach_the_script(tmp_path: Path) -> None:
    script = tmp_path / "job.py"
    script.write_text(
        "def run(context):\n"
        "    with open(context.parameters['out'], 'w', encoding='utf-8') as handle:\n"
        "        handle.write(str(context.parameters['value']))\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "result.txt"
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        f"""
name: params_reach
tasks:
  - name: write_value
    script: job.py
    depends_on: []
    parameters:
      out: {out_file.as_posix()!r}
      value: 42
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
    finally:
        store.close()

    assert out_file.read_text(encoding="utf-8") == "42"


def test_script_raising_marks_the_dag_task_failed_and_captures_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    script = tmp_path / "job.py"
    script.write_text(
        "def run(context):\n"
        '    raise ValueError("failed connecting to postgresql://admin:hunter2@db/prod")\n',
        encoding="utf-8",
    )
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: script_fails
tasks:
  - name: broken
    script: job.py
    depends_on: []
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        with caplog.at_level("ERROR"):
            run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        tasks = store.list_task_runs(run_id)
        assert tasks[0].status is TaskRunStatus.FAILED
        # The safe, structural error recorded in state must never contain
        # the script's own raw exception text.
        assert tasks[0].error is not None
        assert "hunter2" not in tasks[0].error
        assert "postgresql://" not in tasks[0].error
        assert "exited with code" in tasks[0].error
    finally:
        store.close()

    # Captured stderr is logged for real debugging -- honestly NOT redacted
    # (Nexolith cannot inspect/scrub arbitrary subprocess output), unlike
    # the safe state-store error above. Both properties are asserted here.
    logged = "\n".join(record.message for record in caplog.records)
    assert "ValueError" in logged
    assert "failed connecting to postgresql://admin:hunter2@db/prod" in logged


def test_script_exiting_nonzero_without_raising_is_also_a_failure(tmp_path: Path) -> None:
    script = tmp_path / "job.py"
    script.write_text("import sys\n\ndef run(context):\n    sys.exit(3)\n", encoding="utf-8")
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: script_exits
tasks:
  - name: exiter
    script: job.py
    depends_on: []
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
    finally:
        store.close()


def test_missing_script_file_caught_at_validation(tmp_path: Path) -> None:
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: missing_script
tasks:
  - name: broken
    script: does_not_exist.py
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError, match="script that does not exist"):
        load_dag(dag_path)


def test_pipeline_and_script_together_on_one_task_is_rejected(tmp_path: Path) -> None:
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: ambiguous_task
tasks:
  - name: both
    pipeline: a.yaml
    script: b.py
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        load_dag(dag_path)


def test_neither_pipeline_nor_script_is_rejected(tmp_path: Path) -> None:
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: empty_task
tasks:
  - name: nothing
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError, match="either 'pipeline' or 'script' is required"):
        load_dag(dag_path)


def test_explicit_interpreter_path_is_genuinely_used_not_ignored(tmp_path: Path) -> None:
    """Honest scope note: this does not spin up a second real venv (slow,
    and not needed to prove the point). It proves the `interpreter:` value
    is actually threaded into the subprocess launch -- not silently
    defaulted to `sys.executable` -- by showing a bogus interpreter path
    changes the outcome from success to a clear failure. If `interpreter:`
    were ignored, this run would succeed regardless of the value given.
    """
    script = tmp_path / "job.py"
    script.write_text("def run(context):\n    pass\n", encoding="utf-8")

    dag_ok = tmp_path / "ok.yaml"
    write_dag(
        dag_ok,
        f"""
name: explicit_real_interpreter
tasks:
  - name: task
    script: job.py
    interpreter: {sys.executable!r}
    depends_on: []
""",
    )
    dag_bad = tmp_path / "bad.yaml"
    write_dag(
        dag_bad,
        """
name: explicit_bogus_interpreter
tasks:
  - name: task
    script: job.py
    interpreter: this-interpreter-does-not-exist-anywhere
    depends_on: []
""",
    )

    store = StateStore(tmp_path / "state.db")
    try:
        ok_run_id = execute_dag(dag_ok, store)
        ok_run = store.get_dag_run(ok_run_id)
        assert ok_run is not None
        assert ok_run.status is DagRunStatus.SUCCEEDED

        bad_run_id = execute_dag(dag_bad, store)
        bad_run = store.get_dag_run(bad_run_id)
        assert bad_run is not None
        assert bad_run.status is DagRunStatus.FAILED
    finally:
        store.close()


def test_script_task_retries_through_the_same_mechanism_as_pipeline_tasks(
    tmp_path: Path,
) -> None:
    """Proves script tasks share DagExecutor's real retry machinery (not a
    separate, untested code path): a script that fails on attempt 1 and
    succeeds on attempt 2, using a marker file to tell attempts apart
    (a fresh subprocess has no memory of the previous attempt)."""
    marker = tmp_path / "attempts.json"
    marker.write_text("0", encoding="utf-8")
    script = tmp_path / "flaky.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "\n"
        "def run(context):\n"
        f"    marker = {str(marker.as_posix())!r}\n"
        "    count = int(open(marker, encoding='utf-8').read()) + 1\n"
        "    open(marker, 'w', encoding='utf-8').write(str(count))\n"
        "    if count < 2:\n"
        "        sys.exit(1)\n",
        encoding="utf-8",
    )
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: flaky_script
tasks:
  - name: flaky
    script: flaky.py
    depends_on: []
    retries: 1
    retry_delay_seconds: 0
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
        attempts = store.list_run_attempts(run_id)
        assert len(attempts) == 2
    finally:
        store.close()


def test_mixed_pipeline_and_script_tasks_in_one_dag(tmp_path: Path) -> None:
    """A script task and a pipeline task in the same DAG, with a real
    dependency edge between them -- confirms the two task kinds compose
    through the same executor without special-casing breaking either."""
    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,ready\n2,ready\n", encoding="utf-8")
    pipeline_path = tmp_path / "extract.yaml"
    pipeline_path.write_text(
        f"""
name: extract
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(tmp_path / "extract_out.csv").as_posix()}
""",
        encoding="utf-8",
    )
    script = tmp_path / "post.py"
    marker = tmp_path / "post_ran.txt"
    script.write_text(
        f"def run(context):\n    open({str(marker.as_posix())!r}, 'w').write('done')\n",
        encoding="utf-8",
    )
    dag_path = tmp_path / "workflow.yaml"
    write_dag(
        dag_path,
        """
name: mixed
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
  - name: post_process
    script: post.py
    depends_on: [extract]
""",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(dag_path, store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
    finally:
        store.close()

    assert marker.read_text(encoding="utf-8") == "done"
