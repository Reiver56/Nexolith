from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.cli.app import app
from nexolith.cli.render_context import RenderContext
from nexolith.cli.runs_render import render_run_detail, render_runs_list
from nexolith.cli.scheduler_render import SchedulerStatus, render_scheduler_status
from nexolith.dag import execute_dag
from nexolith.scheduler import (
    default_pidfile_path,
    is_process_alive,
    read_pidfile,
    stop_process,
    write_pidfile,
)
from nexolith.state import StateStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this file gets its own state directory -- never the
    real user's %LOCALAPPDATA%\\Nexolith.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("NEXOLITH_STATE_DIR", str(state_dir))
    return state_dir


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


# -- is_process_alive(): the liveness check itself, exercised for real -----


def test_is_process_alive_true_for_a_real_running_process() -> None:
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_false_for_a_pid_that_does_not_exist() -> None:
    assert is_process_alive(999999) is False


def test_is_process_alive_against_a_real_separate_child_process() -> None:
    """Not just this test's own PID -- a genuinely separate process,
    spawned and then torn down, matching how a real scheduler daemon in a
    different terminal would actually be observed.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        assert is_process_alive(proc.pid) is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert is_process_alive(proc.pid) is False


# -- stop_process(): tested for real -- it genuinely terminates -----------


def test_stop_process_genuinely_terminates_a_real_process() -> None:
    """stop_process() (os.kill(pid, SIGTERM)) was verified during this
    story's investigation to reliably terminate a real Windows process,
    even though it does not invoke the target's own graceful-shutdown
    code -- see nexolith.scheduler.pidfile's docstring. This test proves
    the part that's actually reliable: the process really goes away.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        assert is_process_alive(proc.pid) is True

        assert stop_process(proc.pid) is True

        proc.wait(timeout=5)
        assert is_process_alive(proc.pid) is False
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_process_returns_false_for_an_already_dead_process() -> None:
    assert stop_process(999999) is False


# -- scheduler status --------------------------------------------------


def test_status_reports_not_running_when_no_marker_file_exists(isolated_state_dir: Path) -> None:
    assert not default_pidfile_path().exists()

    result = runner.invoke(app, ["scheduler", "status"])

    assert result.exit_code == 0
    assert "not running" in result.output


def test_status_reports_running_with_accurate_info_for_a_real_live_process(
    isolated_state_dir: Path,
) -> None:
    """Not just trusting the marker file's existence: a real separate
    process is spawned, its actual PID is written to the marker, and
    status must consult the real liveness check (is_process_alive) to
    report it as running.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        write_pidfile(default_pidfile_path(), proc.pid, "2026-01-01T00:00:00+00:00")

        result = runner.invoke(app, ["scheduler", "status"])

        assert result.exit_code == 0
        assert "running" in result.output
        assert "not running" not in result.output
        assert str(proc.pid) in result.output
        assert "2026-01-01T00:00:00+00:00" in result.output
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_status_reports_not_running_and_self_heals_a_stale_marker(
    isolated_state_dir: Path,
) -> None:
    """A marker file naming a PID that is not actually alive (the process
    died some other way -- crashed, killed by Task Manager, power loss --
    without going through `scheduler stop`) must not make status lie.
    """
    pidfile_path = default_pidfile_path()
    write_pidfile(pidfile_path, 999999, "2026-01-01T00:00:00+00:00")
    assert pidfile_path.exists()

    result = runner.invoke(app, ["scheduler", "status"])

    assert result.exit_code == 0
    assert "not running" in result.output
    assert not pidfile_path.exists()  # self-healed


# -- scheduler start: marker lifecycle -------------------------------------


def test_start_writes_and_clean_exit_removes_the_marker_file(
    isolated_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scheduler_start() blocks forever in real use (Scheduler.run() with
    no stop condition), so this patches Scheduler.run to a fast stand-in
    that just confirms the marker was written before it was called --
    proving the write-then-run-then-remove sequence around the real,
    unmodified pidfile read/write/remove functions, without needing a
    real background process or thread for this specific test.
    """
    from nexolith.scheduler.daemon import Scheduler

    pidfile_path = default_pidfile_path()
    seen_during_run: dict[str, object] = {}

    def fake_run(self: Scheduler, **kwargs: object) -> None:
        record = read_pidfile(pidfile_path)
        seen_during_run["record"] = record
        seen_during_run["pid_matches_self"] = record is not None and record.pid == os.getpid()

    monkeypatch.setattr(Scheduler, "run", fake_run)

    assert not pidfile_path.exists()
    result = runner.invoke(app, ["scheduler", "start"])

    assert result.exit_code == 0
    assert seen_during_run["pid_matches_self"] is True
    assert not pidfile_path.exists()  # removed after run() returned


def test_start_refuses_when_a_real_live_process_already_holds_the_marker(
    isolated_state_dir: Path,
) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        time.sleep(0.3)
        write_pidfile(default_pidfile_path(), proc.pid, "2026-01-01T00:00:00+00:00")

        result = runner.invoke(app, ["scheduler", "start"])

        assert result.exit_code != 0
        assert "already" in result.output.lower()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_start_cleans_up_a_stale_marker_and_proceeds(
    isolated_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexolith.scheduler.daemon import Scheduler

    monkeypatch.setattr(Scheduler, "run", lambda self, **kwargs: None)
    pidfile_path = default_pidfile_path()
    write_pidfile(pidfile_path, 999999, "2026-01-01T00:00:00+00:00")  # stale: 999999 is dead

    result = runner.invoke(app, ["scheduler", "start"])

    assert result.exit_code == 0
    assert "already" not in result.output.lower()


# -- scheduler stop -------------------------------------------------------


def test_stop_reports_not_running_when_no_marker_exists(isolated_state_dir: Path) -> None:
    result = runner.invoke(app, ["scheduler", "stop"])

    assert result.exit_code == 0
    assert "not running" in result.output


def test_stop_genuinely_terminates_a_real_running_scheduler_and_removes_the_marker(
    isolated_state_dir: Path,
) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    pidfile_path = default_pidfile_path()
    try:
        time.sleep(0.3)
        write_pidfile(pidfile_path, proc.pid, "2026-01-01T00:00:00+00:00")

        result = runner.invoke(app, ["scheduler", "stop"])

        proc.wait(timeout=5)
        assert result.exit_code == 0
        assert is_process_alive(proc.pid) is False
        assert not pidfile_path.exists()
        if sys.platform == "win32":
            assert "forcibly" in result.output.lower()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_self_heals_a_stale_marker(isolated_state_dir: Path) -> None:
    pidfile_path = default_pidfile_path()
    write_pidfile(pidfile_path, 999999, "2026-01-01T00:00:00+00:00")

    result = runner.invoke(app, ["scheduler", "stop"])

    assert result.exit_code == 0
    assert "not running" in result.output
    assert not pidfile_path.exists()


# -- runs list --------------------------------------------------------------


def test_runs_list_with_mixed_success_failure_and_in_progress(
    isolated_state_dir: Path, tmp_path: Path
) -> None:
    store = StateStore()
    try:
        store.register_dag("etl-a", Path("a.yaml"), None)
        store.register_dag("etl-b", Path("b.yaml"), None)

        succeeded_id = store.start_dag_run("etl-a", ["t"], trigger_reason="manual")
        store.complete_dag_run(succeeded_id, success=True)

        failed_id = store.start_dag_run("etl-b", ["t"], trigger_reason="schedule")
        store.complete_dag_run(failed_id, success=False, error="boom")

        running_id = store.start_dag_run("etl-a", ["t"], trigger_reason="manual")

        result = runner.invoke(app, ["runs", "list"])

        assert result.exit_code == 0
        assert f"{succeeded_id}" in result.output
        assert f"{failed_id}" in result.output
        assert f"{running_id}" in result.output
        assert "succeeded" in result.output
        assert "failed" in result.output
        assert "running" in result.output
        assert "etl-a" in result.output
        assert "etl-b" in result.output
    finally:
        store.close()


def test_runs_list_with_no_runs(isolated_state_dir: Path) -> None:
    result = runner.invoke(app, ["runs", "list"])

    assert result.exit_code == 0
    assert "no dag runs" in result.output.lower()


def test_runs_list_filters_to_one_dag(isolated_state_dir: Path) -> None:
    store = StateStore()
    try:
        store.register_dag("etl-a", Path("a.yaml"), None)
        store.register_dag("etl-b", Path("b.yaml"), None)
        run_a = store.start_dag_run("etl-a", ["t"], trigger_reason="manual")
        store.complete_dag_run(run_a, success=True)
        run_b = store.start_dag_run("etl-b", ["t"], trigger_reason="manual")
        store.complete_dag_run(run_b, success=True)

        result = runner.invoke(app, ["runs", "list", "--dag", "etl-a"])

        assert result.exit_code == 0
        assert f"{run_a}" in result.output
        assert "etl-a" in result.output
        assert "etl-b" not in result.output
    finally:
        store.close()


# -- runs show, including a skipped task from story 3's failure propagation -


def test_runs_show_renders_skipped_tasks_from_real_dag_execution(
    isolated_state_dir: Path, tmp_path: Path
) -> None:
    """Uses the real story-3 executor (extract -> {transform (fails), side},
    transform -> load), not fabricated data, so the 'skipped' task actually
    comes from real failure-propagation, matching what runs show must
    render correctly.
    """
    write_pipeline(tmp_path / "extract.yaml", name="extract")
    write_failing_pipeline(tmp_path / "transform.yaml", name="transform")
    write_pipeline(tmp_path / "side.yaml", name="side")
    write_pipeline(tmp_path / "load.yaml", name="load")
    dag_path = tmp_path / "workflow.yaml"
    dag_path.write_text(
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
        encoding="utf-8",
    )
    store = StateStore()
    try:
        run_id = execute_dag(dag_path, store)
    finally:
        store.close()

    result = runner.invoke(app, ["runs", "show", str(run_id)])

    assert result.exit_code == 0
    assert "failed" in result.output
    assert "succeeded" in result.output
    assert "skipped" in result.output
    assert "extract" in result.output
    assert "transform" in result.output
    assert "side" in result.output
    assert "load" in result.output


def test_runs_show_reports_not_found_for_a_nonexistent_run(isolated_state_dir: Path) -> None:
    result = runner.invoke(app, ["runs", "show", "999"])

    assert result.exit_code != 0
    assert "no dag run found" in result.output.lower()


# -- plain-mode fallback: no color/panel leakage ---------------------------


_PLAIN_CONTEXT = RenderContext(is_tty=False, color_enabled=True, width=200)
_STYLED_CONTEXT = RenderContext(is_tty=True, color_enabled=True, width=200)


def test_render_scheduler_status_plain_mode_has_no_escape_codes() -> None:
    status = SchedulerStatus(True, 1234, "2026-01-01T00:00:00+00:00")
    output = render_scheduler_status(status, _PLAIN_CONTEXT)
    assert "\x1b[" not in output
    assert "running" in output


def test_render_scheduler_status_styled_mode_has_escape_codes() -> None:
    status = SchedulerStatus(True, 1234, "2026-01-01T00:00:00+00:00")
    output = render_scheduler_status(status, _STYLED_CONTEXT)
    assert "\x1b[" in output


def test_render_runs_list_plain_mode_has_no_escape_codes() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus

    runs = [
        DagRunRecord(1, "etl", DagRunStatus.SUCCEEDED, "manual", "t0", "t1", None),
    ]
    output = render_runs_list(runs, _PLAIN_CONTEXT)
    assert "\x1b[" not in output
    assert "succeeded" in output


def test_render_runs_list_styled_mode_has_escape_codes() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus

    runs = [
        DagRunRecord(1, "etl", DagRunStatus.SUCCEEDED, "manual", "t0", "t1", None),
    ]
    output = render_runs_list(runs, _STYLED_CONTEXT)
    assert "\x1b[" in output


_T0 = "2026-01-01T00:00:00+00:00"
_T1 = "2026-01-01T00:00:01+00:00"


def test_render_run_detail_plain_mode_has_no_escape_codes_and_ascii_border() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    tasks = [
        TaskRunRecord(1, "a", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
        TaskRunRecord(1, "b", TaskRunStatus.SKIPPED, None, _T1, None),
    ]
    output = render_run_detail(run, tasks, _PLAIN_CONTEXT)
    assert "\x1b[" not in output
    assert "+" in output  # ASCII border, not the Unicode box-drawing one
    assert "succeeded" in output
    assert "skipped" in output


def test_render_run_detail_styled_mode_has_escape_codes_and_unicode_border() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    tasks = [
        TaskRunRecord(1, "a", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
        TaskRunRecord(1, "b", TaskRunStatus.SKIPPED, None, _T1, None),
    ]
    output = render_run_detail(run, tasks, _STYLED_CONTEXT)
    assert "\x1b[" in output
    assert "╭" in output
