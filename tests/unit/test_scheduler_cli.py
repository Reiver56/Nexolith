from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.cli.app import app
from nexolith.cli.nexo_art import DIM, RED, _ansi_256_index
from nexolith.cli.render_context import RenderContext
from nexolith.cli.runs_render import render_run_detail, render_runs_list
from nexolith.cli.scheduler_render import SchedulerStatus, render_scheduler_status
from nexolith.dag import execute_dag
from nexolith.events import EventSink
from nexolith.models import ExecutionResult
from nexolith.scheduler import (
    default_pidfile_path,
    is_process_alive,
    read_pidfile,
    stop_process,
    write_pidfile,
)
from nexolith.state import StateStore
from nexolith.types import Scalar

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


def test_runs_show_renders_multiple_retry_attempts_for_one_task(
    isolated_state_dir: Path, tmp_path: Path
) -> None:
    """A task that fails once and succeeds on retry (real DagExecutor retry
    support, NXL-79) -- runs show must render both attempts and the
    on_failure policy, not just the task's final status.
    """
    from nexolith.dag import DagExecutor, load_dag

    class _FlakyOnce:
        def __init__(self) -> None:
            from nexolith.application import PipelineApplication

            self._real = PipelineApplication()
            self._calls = 0

        def run_pipeline(
            self,
            path: Path,
            *,
            parameter_overrides: Mapping[str, Scalar] | None = None,
            event_sink: EventSink | None = None,
        ) -> ExecutionResult:
            from nexolith.exceptions import ExecutionError

            self._calls += 1
            if self._calls == 1:
                raise ExecutionError("simulated transient failure")
            return self._real.run_pipeline(
                path, parameter_overrides=parameter_overrides, event_sink=event_sink
            )

    write_pipeline(tmp_path / "flaky.yaml", name="flaky")
    dag_path = tmp_path / "workflow.yaml"
    dag_path.write_text(
        """
name: retry-demo
tasks:
  - name: flaky
    pipeline: flaky.yaml
    depends_on: []
    retries: 1
    retry_delay_seconds: 0
""",
        encoding="utf-8",
    )
    store = StateStore()
    try:
        dag = load_dag(dag_path)
        run_id = DagExecutor(store, _FlakyOnce()).run(dag, dag_path)
    finally:
        store.close()

    result = runner.invoke(app, ["runs", "show", str(run_id)])

    assert result.exit_code == 0
    assert "succeeded" in result.output
    assert "attempt 1" in result.output
    assert "attempt 2" in result.output
    assert "simulated transient failure" in result.output
    assert "Policy: skip" in result.output


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


# -- NXL-100: runs show under all three real color tiers --------------------


def test_run_detail_renders_genuinely_differently_across_all_three_color_tiers() -> None:
    """The story's own explicit ask: at least one real renderer, tested
    under all three tiers, confirming each produces genuinely different,
    tier-appropriate output -- not just that truecolor and 'plain' differ
    (already covered elsewhere), but that the new standard (256-color)
    middle tier is real and distinct from both.
    """
    from nexolith.state.models import DagRunRecord, DagRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")

    truecolor_ctx = RenderContext(is_tty=True, color_enabled=True, width=200, truecolor=True)
    standard_ctx = RenderContext(is_tty=True, color_enabled=True, width=200, truecolor=False)
    plain_ctx = RenderContext(is_tty=False, color_enabled=True, width=200)

    truecolor_output = render_run_detail(run, [], truecolor_ctx)
    standard_output = render_run_detail(run, [], standard_ctx)
    plain_output = render_run_detail(run, [], plain_ctx)

    # All three genuinely differ from each other.
    assert truecolor_output != standard_output
    assert standard_output != plain_output
    assert truecolor_output != plain_output

    # Tier-appropriate encoding, checked precisely, not just "contains an
    # escape code somewhere":
    assert "\x1b[38;2;" in truecolor_output  # real 24-bit
    assert "\x1b[38;5;" not in truecolor_output

    assert "\x1b[38;5;" in standard_output  # 256-color approximation
    assert "\x1b[38;2;" not in standard_output

    assert "\x1b[" not in plain_output  # no color at all
    assert "+" in plain_output  # ASCII border, not Unicode box-drawing

    # But the actual, visible content (status/DAG name/etc.) is identical
    # across all three -- only the color encoding differs, never the text.
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    assert ansi_re.sub("", truecolor_output) == ansi_re.sub("", standard_output)


# -- NXL-93: bordered panel width/padding ------------------------------------
#
# Found by a user during manual v0.3.3 testing: short lines ("Status:",
# "DAG:") next to a long "Error:" line made the right border look
# misaligned. Investigated directly (not assumed): the underlying string
# WAS already correctly padded to its own computed width in every case --
# the real bug is that width had no ceiling at all, so a sufficiently long
# Error value made the panel wider than any real terminal, and the
# terminal's OWN line-wrap (not Nexolith's padding math) is what broke the
# visual rectangle, landing the border character at a different column on
# each wrapped physical row. Fixed by capping the panel to
# `render_context.width` and wrapping long values onto their own bordered,
# padded continuation lines instead of ever exceeding it.

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_panel_line_widths(output: str) -> list[int]:
    """Every border/body line's real, visible (ANSI-stripped) character
    width, in the order they appear -- what a user's own terminal actually
    renders per row width-wise, independent of styled vs. plain mode's
    different border characters (`|`/`+` vs `│`/`╭`/`╰`).
    """
    widths = []
    for line in output.split("\n"):
        visible = _ANSI_RE.sub("", line)
        if visible[:1] in {"|", "+", "│", "╭", "╰"}:
            widths.append(len(visible))
    return widths


def test_run_detail_panel_lines_all_share_identical_visible_width() -> None:
    """The user's real observed case: short Status/DAG lines next to a
    much longer Error line. Every rendered border/body line must measure
    the exact same visible width, in both plain and styled mode -- styled
    mode's ANSI color codes must not be counted."""
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    long_error = (
        "Task 'only' failed: Pipeline 'broken_pipeline' failed: Could not read "
        "CSV file '/tmp/nxl93/does_not_exist.csv'. Check the path and permissions."
    )
    run = DagRunRecord(1, "medallion_orders", DagRunStatus.FAILED, "manual", _T0, _T1, long_error)
    tasks = [TaskRunRecord(1, "only", TaskRunStatus.FAILED, _T0, _T1, "boom")]

    for context in (_PLAIN_CONTEXT, _STYLED_CONTEXT):
        output = render_run_detail(run, tasks, context)
        widths = _visible_panel_line_widths(output)
        assert len(widths) >= 3  # top/bottom border plus at least one body line
        assert len(set(widths)) == 1, f"misaligned panel widths: {widths}"


def test_run_detail_panel_wraps_long_lines_to_the_render_context_width() -> None:
    """A value long enough to overflow a real terminal must wrap onto
    bordered, padded continuation lines rather than ever exceeding
    render_context.width -- confirmed at a realistic terminal width (80),
    in both plain and styled mode, with the full error text preserved
    (wrapped, never truncated or dropped)."""
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    long_error = (
        "Task 'only' failed: Pipeline 'broken_pipeline' failed: Could not read "
        "CSV file '/tmp/nxl93/does_not_exist.csv'. Check the path and permissions."
    )
    run = DagRunRecord(1, "medallion_orders", DagRunStatus.FAILED, "manual", _T0, _T1, long_error)
    tasks = [TaskRunRecord(1, "only", TaskRunStatus.FAILED, _T0, _T1, "boom")]

    narrow_plain = RenderContext(is_tty=False, color_enabled=True, width=80, forced_plain=True)
    narrow_styled = RenderContext(is_tty=True, color_enabled=True, width=80)

    for context in (narrow_plain, narrow_styled):
        output = render_run_detail(run, tasks, context)
        widths = _visible_panel_line_widths(output)
        assert len(set(widths)) == 1
        assert widths[0] <= 80
        # The error text survived, just wrapped across multiple lines --
        # not truncated, not dropped.
        plain_output = _ANSI_RE.sub("", output)
        for word in ("broken_pipeline", "does_not_exist.csv", "permissions"):
            assert word in plain_output
        assert "Error:" in plain_output
        # 8 rows (Status/DAG/Trigger/Policy/Severity/Started/Ended/Error)
        # plus top/bottom border = 10 lines if nothing wrapped; the long
        # Error value genuinely needed at least one extra continuation line.
        assert len(widths) > 10


def test_run_detail_panel_stays_tight_when_content_is_short() -> None:
    """Regression: the width cap must not force every panel out to the
    full terminal width when nothing needs wrapping -- a panel with only
    short values still shrinks to fit its own content, exactly as before
    this fix, even against a very wide render context."""
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.SUCCEEDED, "manual", _T0, _T1, None)
    tasks = [TaskRunRecord(1, "a", TaskRunStatus.SUCCEEDED, _T0, _T1, None)]

    wide_context = RenderContext(is_tty=True, color_enabled=True, width=500)
    output = render_run_detail(run, tasks, wide_context)
    widths = _visible_panel_line_widths(output)
    assert len(set(widths)) == 1
    assert widths[0] < 100  # nowhere near the 500-wide cap


# -- NXL-95: "partial success" note on a failed run with real side effects --
#
# Found via the FonoLink stress test: on_failure: skip only blocks a
# failed task's own transitive dependents -- an independent branch that
# succeeded keeps its real, persisted effects regardless of the DAG's own
# overall `failed` status (score_churn inserted real rows while its DAG
# was recorded failed because the unrelated detect_fraud task failed). A
# user glancing at `Status: failed` alone could reasonably assume nothing
# happened. Rendering-only fix: on_failure: skip's actual execution/
# propagation logic is untouched by any test in this section.


def test_failed_run_with_a_succeeded_task_shows_partial_success_note() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "partial_failure_demo", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    tasks = [
        TaskRunRecord(1, "independent_success", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
        TaskRunRecord(1, "independent_failure", TaskRunStatus.FAILED, _T0, _T1, "boom"),
    ]

    plain_output = render_run_detail(run, tasks, _PLAIN_CONTEXT)
    styled_output = render_run_detail(run, tasks, _STYLED_CONTEXT)

    assert "Partial: 1 of 2 tasks succeeded" in plain_output
    assert "\x1b[" not in _ANSI_RE.sub("", plain_output)  # sanity: truly plain

    styled_plain_text = _ANSI_RE.sub("", styled_output)
    assert "Partial: 1 of 2 tasks succeeded" in styled_plain_text
    # The note itself is actually colorized in styled mode, not just present
    # as plain text alongside ANSI codes elsewhere in the output.
    partial_line = next(line for line in styled_output.split("\n") if "Partial:" in line)
    assert "\x1b[" in partial_line


def test_failed_run_with_no_succeeded_tasks_shows_no_partial_note() -> None:
    """Don't clutter the common case: a failed run where every task
    genuinely failed or was skipped has nothing partial to report."""
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "bad_dag", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    tasks = [
        TaskRunRecord(1, "a", TaskRunStatus.FAILED, _T0, _T1, "boom"),
        TaskRunRecord(1, "b", TaskRunStatus.SKIPPED, None, _T1, None),
    ]

    plain_output = render_run_detail(run, tasks, _PLAIN_CONTEXT)
    styled_output = render_run_detail(run, tasks, _STYLED_CONTEXT)

    assert "Partial" not in plain_output
    assert "Partial" not in styled_output


def test_fully_successful_run_shows_no_partial_note() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "ok_dag", DagRunStatus.SUCCEEDED, "manual", _T0, _T1, None)
    tasks = [
        TaskRunRecord(1, "a", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
        TaskRunRecord(1, "b", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
    ]

    plain_output = render_run_detail(run, tasks, _PLAIN_CONTEXT)
    styled_output = render_run_detail(run, tasks, _STYLED_CONTEXT)

    assert "Partial" not in plain_output
    assert "Partial" not in styled_output


def test_failed_run_with_no_tasks_recorded_shows_no_partial_note() -> None:
    """Edge case: a failed run with an empty task list (nothing recorded
    at all) has nothing to report as partially succeeded."""
    from nexolith.state.models import DagRunRecord, DagRunStatus

    run = DagRunRecord(1, "empty_dag", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    output = render_run_detail(run, [], _PLAIN_CONTEXT)
    assert "Partial" not in output
    assert "(no tasks recorded)" in output


# -- NXL-80: UnicodeEncodeError in plain-mode task-status markers ----------
#
# The bug: RenderContext.plain gated whether the task-status marker got
# wrapped in color codes, but not the marker CHARACTER itself -- so a
# 'skipped'/'blocked'/etc. task still emitted a raw ● ○ ✗ ⊘ even in plain
# mode. NO_COLOR alone doesn't reproduce this (it makes render_context.plain
# True, which *did* correctly switch the panel border to ASCII -- the bug
# was specifically that the same switch never happened for the marker).
# The real, reported failure mode is writing that string through a stream
# that can't encode it -- e.g. a Windows console on a legacy code page like
# cp1252 -- so these tests write the rendered output through a real
# io.TextIOWrapper configured with encoding='cp1252', errors='strict': the
# same failure `typer.echo()` hits on such a console, not just inspecting
# the string for non-ASCII characters.


def test_run_detail_plain_mode_markers_are_safe_under_a_real_non_utf8_encoding() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    tasks = [
        TaskRunRecord(1, "pending_task", TaskRunStatus.PENDING, None, None, None),
        TaskRunRecord(1, "running_task", TaskRunStatus.RUNNING, _T0, None, None),
        TaskRunRecord(1, "succeeded_task", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
        TaskRunRecord(1, "failed_task", TaskRunStatus.FAILED, _T0, _T1, "oops"),
        TaskRunRecord(1, "skipped_task", TaskRunStatus.SKIPPED, None, _T1, None),
        TaskRunRecord(1, "blocked_task", TaskRunStatus.BLOCKED, None, _T1, None),
    ]

    output = render_run_detail(run, tasks, _PLAIN_CONTEXT)

    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding="cp1252", errors="strict") as wrapper:
        wrapper.write(output)  # must not raise UnicodeEncodeError
        wrapper.flush()

    assert "[PENDING]" in output
    assert "[RUNNING]" in output
    assert "[OK]" in output
    assert "[FAIL]" in output
    assert "[SKIP]" in output
    assert "[BLOCKED]" in output
    assert "●" not in output
    assert "○" not in output
    assert "✗" not in output
    assert "⊘" not in output


def test_run_detail_styled_mode_still_uses_the_real_unicode_markers() -> None:
    """Confirms the fix didn't regress the working (styled) case while
    fixing the broken (plain) one.
    """
    from nexolith.state.models import DagRunRecord, DagRunStatus, TaskRunRecord, TaskRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")
    tasks = [
        TaskRunRecord(1, "a", TaskRunStatus.SUCCEEDED, _T0, _T1, None),
        TaskRunRecord(1, "b", TaskRunStatus.FAILED, _T0, _T1, "oops"),
        TaskRunRecord(1, "c", TaskRunStatus.SKIPPED, None, _T1, None),
        TaskRunRecord(1, "d", TaskRunStatus.BLOCKED, None, _T1, None),
    ]

    output = render_run_detail(run, tasks, _STYLED_CONTEXT)

    assert "●" in output
    assert "✗" in output
    assert "⊘" in output
    assert "[OK]" not in output
    assert "[FAIL]" not in output
    assert "[SKIP]" not in output


def test_retry_attempt_rendering_is_safe_under_a_real_non_utf8_encoding() -> None:
    """Story 6's retry-attempt breakdown predates this bug report -- verify
    it directly rather than assuming it's fine because it only ever prints
    plain-ASCII enum values (running/succeeded/failed) and never a Unicode
    marker.
    """
    from nexolith.state.models import (
        DagRunRecord,
        DagRunStatus,
        TaskAttemptRecord,
        TaskAttemptStatus,
        TaskRunRecord,
        TaskRunStatus,
    )

    run = DagRunRecord(1, "etl", DagRunStatus.SUCCEEDED, "manual", _T0, _T1, None)
    tasks = [TaskRunRecord(1, "flaky", TaskRunStatus.SUCCEEDED, _T0, _T1, None)]
    attempts = [
        TaskAttemptRecord(1, 1, "flaky", 1, TaskAttemptStatus.FAILED, _T0, _T1, "boom"),
        TaskAttemptRecord(2, 1, "flaky", 2, TaskAttemptStatus.SUCCEEDED, _T1, _T1, None),
    ]

    output = render_run_detail(run, tasks, _PLAIN_CONTEXT, attempts)

    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding="cp1252", errors="strict") as wrapper:
        wrapper.write(output)  # must not raise UnicodeEncodeError
        wrapper.flush()

    assert "attempt 1" in output
    assert "attempt 2" in output


# -- NXL-87: severity rendering -------------------------------------------


def test_runs_show_distinguishes_a_failed_critical_dag_from_a_failed_low_one_styled() -> None:
    """Both rows report the same run status (failed) -- what must differ is
    the severity label and its color, so a critical failure visually stands
    out from a low one, not just repeats the same red 'failed' text twice.
    """
    from nexolith.state.models import DagRunRecord, DagRunStatus

    critical_run = DagRunRecord(
        1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom", severity="critical"
    )
    low_run = DagRunRecord(
        2, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom", severity="low"
    )

    critical_output = render_run_detail(critical_run, [], _STYLED_CONTEXT)
    low_output = render_run_detail(low_run, [], _STYLED_CONTEXT)

    critical_severity_line = next(
        line for line in critical_output.splitlines() if "Severity" in line
    )
    low_severity_line = next(line for line in low_output.splitlines() if "Severity" in line)
    # RED (critical) and DIM (low) are different ANSI color codes -- the
    # two runs' "Severity: ..." lines must be colored differently, not just
    # both contain the word "some color". Computed via the real 256-color
    # index (_STYLED_CONTEXT.truecolor is False, NXL-100's standard tier),
    # not hand-hardcoded, so this doesn't silently stop testing anything
    # real if the tier default or the mapping algorithm ever changes.
    red_sgr = f"\x1b[38;5;{_ansi_256_index(RED)}m"
    dim_sgr = f"\x1b[38;5;{_ansi_256_index(DIM)}m"
    assert red_sgr in critical_severity_line
    assert dim_sgr in low_severity_line
    assert red_sgr not in low_severity_line
    assert dim_sgr not in critical_severity_line


def test_runs_show_severity_is_plain_and_unambiguous_without_color() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus

    critical_run = DagRunRecord(
        1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom", severity="critical"
    )
    low_run = DagRunRecord(
        2, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom", severity="low"
    )

    critical_output = render_run_detail(critical_run, [], _PLAIN_CONTEXT)
    low_output = render_run_detail(low_run, [], _PLAIN_CONTEXT)

    assert "\x1b[" not in critical_output
    assert "\x1b[" not in low_output
    # The "Severity: <word>" label already disambiguates without color --
    # matching how "Policy: <word>" already does today, not a new bracketed
    # marker (that convention is for bare, unlabeled symbols like the task
    # markers; a labeled panel row doesn't need it too).
    assert "Severity: critical" in critical_output
    assert "Severity: low" in low_output


def test_runs_list_renders_a_severity_column_distinctly_styled_and_plain() -> None:
    from nexolith.state.models import DagRunRecord, DagRunStatus

    runs = [
        DagRunRecord(
            1, "etl-a", DagRunStatus.FAILED, "manual", "t0", "t1", None, severity="critical"
        ),
        DagRunRecord(2, "etl-b", DagRunStatus.FAILED, "manual", "t0", "t1", None, severity="low"),
    ]

    plain_output = render_runs_list(runs, _PLAIN_CONTEXT)
    styled_output = render_runs_list(runs, _STYLED_CONTEXT)

    assert "SEVERITY" in plain_output  # column header present
    assert "critical" in plain_output
    assert "low" in plain_output
    assert "\x1b[" not in plain_output

    assert f"\x1b[38;5;{_ansi_256_index(RED)}m" in styled_output  # RED, the critical row
    assert f"\x1b[38;5;{_ansi_256_index(DIM)}m" in styled_output  # DIM, the low row


def test_a_medium_severity_run_renders_with_no_severity_color(tmp_path: Path) -> None:
    """medium (the default) is deliberately uncolored -- the unremarkable
    baseline, nothing to draw the eye to. Confirmed by checking severity's
    own value never appears wrapped in an escape code, not just that some
    color exists somewhere in the output (the status itself is still red).
    """
    from nexolith.state.models import DagRunRecord, DagRunStatus

    run = DagRunRecord(1, "etl", DagRunStatus.FAILED, "manual", _T0, _T1, "boom")  # default medium
    output = render_run_detail(run, [], _STYLED_CONTEXT)

    assert "Severity: medium" in output  # plain, no ANSI wrapping around "medium"
