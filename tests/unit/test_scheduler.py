import signal
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexolith.dag import DagExecutor
from nexolith.dag.validator import read_dag_config
from nexolith.models import ExecutionResult
from nexolith.process_identity import ProcessIdentity, ProcessIdentityLookup
from nexolith.scheduler import Scheduler, parse_interval
from nexolith.scheduler.daemon import _default_execute
from nexolith.state import DagRunStatus, StateStore


def make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def make_fake_execute(
    store: StateStore, calls: list[str], *, leave_running: bool = False
) -> Callable[[Path, StateStore], int]:
    """A stand-in for execute_dag() that performs the same store
    side-effects (so the scheduler's own due-ness/incomplete-run checks see
    realistic state) without needing a real pipeline file for every test.
    """

    def fake_execute(path: Path, passed_store: StateStore) -> int:
        calls.append(str(path))
        dag = next(d for d in store.list_dags() if Path(d.source_path) == path)
        run_id = store.start_dag_run(dag.name, [], trigger_reason="schedule")
        if not leave_running:
            store.complete_dag_run(run_id, success=True)
        return run_id

    return fake_execute


class _UnexpectedTaskApplication:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_pipeline(
        self,
        path: Path,
        *,
        parameter_overrides: object = None,
        event_sink: object = None,
    ) -> ExecutionResult:
        self.calls.append(path.name)
        if path.name == "unexpected.yaml":
            raise ValueError("unexpected adapter failure")
        return ExecutionResult(path.stem)


def write_single_task_dag(path: Path, *, name: str, pipeline: str) -> None:
    path.write_text(
        f"""
name: {name}
tasks:
  - name: only
    pipeline: {pipeline}
    depends_on: []
""",
        encoding="utf-8",
    )


def execute_with_application(
    application: _UnexpectedTaskApplication,
) -> Callable[[Path, StateStore], int]:
    def execute(path: Path, store: StateStore) -> int:
        dag = read_dag_config(path)
        return DagExecutor(store, application).run(dag, path, trigger_reason="schedule")

    return execute


class Clock:
    """Injectable, controllable time -- no real sleeping anywhere in these
    tests."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


# -- interval parsing ---------------------------------------------------


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
        (" 5m ", timedelta(minutes=5)),
    ],
)
def test_parse_interval_valid(schedule: str, expected: timedelta) -> None:
    assert parse_interval(schedule) == expected


@pytest.mark.parametrize(
    "schedule",
    [
        "0 2 * * *",  # cron, not supported by this format
        "5",  # missing unit
        "m",  # missing amount
        "5 m",  # space between amount and unit
        "0s",  # zero amount
        "-5m",  # negative
        "5x",  # unknown unit
        "",  # empty
        "5M",  # wrong case
    ],
)
def test_parse_interval_invalid(schedule: str) -> None:
    from nexolith.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Invalid schedule"):
        parse_interval(schedule)


# -- tick(): due / not due / running / disabled --------------------------


def test_due_schedule_triggers_a_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "5m", enabled=True)
        clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
        calls: list[str] = []
        scheduler = Scheduler(
            store,
            execute=make_fake_execute(store, calls),
            poll_interval_seconds=0.01,
            now=clock.now,
        )

        triggered = scheduler.tick()

        assert len(triggered) == 1
        assert calls == ["etl.yaml"]
        latest = store.latest_dag_run("etl")
        assert latest is not None
        assert latest.status is DagRunStatus.SUCCEEDED
    finally:
        store.close()


def test_not_yet_due_schedule_does_not_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "5m", enabled=True)
        clock = Clock(datetime.now(UTC))
        # StateStore stamps its own rows with real wall-clock time
        # internally; sync it to the same fake clock so "elapsed since the
        # real last-run timestamp" is computed against a consistent
        # timeline instead of drifting against actual real time.
        monkeypatch.setattr("nexolith.state.store._now", lambda: clock.now().isoformat())
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls), now=clock.now)
        scheduler.tick()  # first tick always fires (no prior run)
        assert len(calls) == 1

        clock.advance(timedelta(minutes=2))  # well under the 5m interval
        triggered = scheduler.tick()

        assert triggered == []
        assert len(calls) == 1
    finally:
        store.close()


def test_an_already_running_dag_is_not_retriggered_even_if_due(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
        calls: list[str] = []
        scheduler = Scheduler(
            store,
            execute=make_fake_execute(store, calls, leave_running=True),
            now=clock.now,
        )
        scheduler.tick()  # starts a run that never completes (simulates still in progress)
        assert len(calls) == 1

        clock.advance(timedelta(hours=1))  # schedule is now very overdue
        triggered = scheduler.tick()

        assert triggered == []
        assert len(calls) == 1  # not re-triggered while the prior run is incomplete
    finally:
        store.close()


def test_fresh_scheduler_interrupts_abandoned_run_and_schedules_dag_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
        monkeypatch.setattr("nexolith.state.store._now", lambda: clock.now().isoformat())
        owner = ProcessIdentity(pid=999999, create_time_ns=1_000_000_000)
        abandoned_id = store.start_dag_run(
            "etl", ["extract"], trigger_reason="schedule", owner_identity=owner
        )
        clock.advance(timedelta(seconds=2))
        calls: list[str] = []

        scheduler = Scheduler(
            store,
            execute=make_fake_execute(store, calls),
            now=clock.now,
            process_identity_lookup=lambda pid: ProcessIdentityLookup.not_found(),
        )
        triggered = scheduler.tick()

        abandoned = store.get_dag_run(abandoned_id)
        assert abandoned is not None
        assert abandoned.status is DagRunStatus.INTERRUPTED
        assert abandoned.ended_at == clock.now().isoformat()
        assert len(triggered) == 1
        assert calls == ["etl.yaml"]
        assert store.get_dag_run(triggered[0]).status is DagRunStatus.SUCCEEDED  # type: ignore[union-attr]
    finally:
        store.close()


def test_fresh_scheduler_interrupts_reused_owner_pid_and_schedules_dag_again(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        original_owner = ProcessIdentity(pid=4242, create_time_ns=1_000_000_000)
        replacement_process = ProcessIdentity(pid=4242, create_time_ns=2_000_000_000)
        abandoned_id = store.start_dag_run(
            "etl", ["extract"], trigger_reason="schedule", owner_identity=original_owner
        )
        started_at = datetime.fromisoformat(store.get_dag_run(abandoned_id).started_at)  # type: ignore[union-attr]
        calls: list[str] = []

        scheduler = Scheduler(
            store,
            execute=make_fake_execute(store, calls),
            now=lambda: started_at + timedelta(seconds=2),
            process_identity_lookup=lambda pid: ProcessIdentityLookup.found(replacement_process),
        )
        triggered = scheduler.tick()

        assert store.get_dag_run(abandoned_id).status is DagRunStatus.INTERRUPTED  # type: ignore[union-attr]
        assert len(triggered) == 1
        assert calls == ["etl.yaml"]
    finally:
        store.close()


def test_fresh_scheduler_preserves_genuinely_active_foreground_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        owner = ProcessIdentity(pid=4242, create_time_ns=1_000_000_000)
        active_id = store.start_dag_run(
            "etl", ["extract"], trigger_reason="manual", owner_identity=owner
        )
        calls: list[str] = []
        scheduler = Scheduler(
            store,
            execute=make_fake_execute(store, calls),
            process_identity_lookup=lambda pid: ProcessIdentityLookup.found(owner),
        )

        assert scheduler.tick() == []
        assert calls == []
        active = store.get_dag_run(active_id)
        assert active is not None
        assert active.status is DagRunStatus.RUNNING
    finally:
        store.close()


def test_each_new_scheduler_instance_reconciles_before_its_first_tick(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        owner = ProcessIdentity(pid=4242, create_time_ns=1_000_000_000)
        run_id = store.start_dag_run("etl", [], trigger_reason="manual", owner_identity=owner)

        first = Scheduler(
            store,
            process_identity_lookup=lambda pid: ProcessIdentityLookup.found(owner),
        )
        assert first.tick() == []
        assert store.get_dag_run(run_id).status is DagRunStatus.RUNNING  # type: ignore[union-attr]

        calls: list[str] = []
        started_at = datetime.fromisoformat(store.get_dag_run(run_id).started_at)  # type: ignore[union-attr]
        second = Scheduler(
            store,
            execute=make_fake_execute(store, calls),
            now=lambda: started_at + timedelta(seconds=2),
            process_identity_lookup=lambda pid: ProcessIdentityLookup.not_found(),
        )
        triggered = second.tick()

        assert store.get_dag_run(run_id).status is DagRunStatus.INTERRUPTED  # type: ignore[union-attr]
        assert len(triggered) == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "lookup",
    [
        lambda pid: ProcessIdentityLookup.access_denied(),
        lambda pid: ProcessIdentityLookup.unavailable(),
    ],
)
def test_unverifiable_owner_fails_closed_against_duplicate_execution(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    lookup: Callable[[int], ProcessIdentityLookup],
) -> None:
    owner = ProcessIdentity(pid=4242, create_time_ns=1_000_000_000)
    store = StateStore(tmp_path / "state.db", process_identity=lambda: owner)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        run_id = store.start_dag_run("etl", [], trigger_reason="manual")
        calls: list[str] = []
        scheduler = Scheduler(
            store,
            execute=make_fake_execute(store, calls),
            process_identity_lookup=lookup,
        )

        with caplog.at_level("WARNING", logger="nexolith.scheduler.daemon"):
            assert scheduler.tick() == []

        assert store.get_dag_run(run_id).status is DagRunStatus.RUNNING  # type: ignore[union-attr]
        assert calls == []
        assert "leaving it running to avoid duplicate execution" in caplog.text
    finally:
        store.close()


def test_process_identity_provider_failure_fails_closed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    owner = ProcessIdentity(pid=4242, create_time_ns=1_000_000_000)
    store = StateStore(tmp_path / "state.db", process_identity=lambda: owner)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        run_id = store.start_dag_run("etl", [], trigger_reason="manual")

        def lookup_failure(pid: int) -> ProcessIdentityLookup:
            raise OSError("simulated lookup failure")

        scheduler = Scheduler(store, process_identity_lookup=lookup_failure)
        with caplog.at_level("WARNING", logger="nexolith.scheduler.daemon"):
            assert scheduler.tick() == []

        assert store.get_dag_run(run_id).status is DagRunStatus.RUNNING  # type: ignore[union-attr]
        assert "(unavailable)" in caplog.text
    finally:
        store.close()


def test_legacy_owner_without_creation_time_fails_closed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    owner = ProcessIdentity(pid=4242, create_time_ns=1_000_000_000)
    store = StateStore(tmp_path / "state.db", process_identity=lambda: owner)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        run_id = store.start_dag_run("etl", [], trigger_reason="manual")
        conn = sqlite3.connect(str(store.database_path))
        conn.execute("UPDATE dag_runs SET owner_create_time_ns = NULL WHERE id = ?", (run_id,))
        conn.commit()
        conn.close()

        scheduler = Scheduler(store)
        with caplog.at_level("WARNING", logger="nexolith.scheduler.daemon"):
            assert scheduler.tick() == []

        assert store.get_dag_run(run_id).status is DagRunStatus.RUNNING  # type: ignore[union-attr]
        assert "(legacy_owner_identity)" in caplog.text
    finally:
        store.close()


def test_disabled_dag_is_never_triggered_regardless_of_schedule(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=False)
        clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls), now=clock.now)

        clock.advance(timedelta(hours=1))
        triggered = scheduler.tick()

        assert triggered == []
        assert calls == []
    finally:
        store.close()


def test_unscheduled_dag_is_never_triggered(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None, enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        assert scheduler.tick() == []
        assert calls == []
    finally:
        store.close()


def test_paused_dag_still_reacts_to_cross_dag_trigger(tmp_path: Path) -> None:
    downstream_path = tmp_path / "downstream.yaml"
    downstream_path.write_text(
        "name: downstream\ntrigger:\n  on_success_of: [upstream]\ntasks:\n"
        "  - name: publish\n    pipeline: unused.yaml\n",
        encoding="utf-8",
    )
    store = make_store(tmp_path)
    try:
        store.register_dag("upstream", Path("upstream.yaml"), None)
        store.register_dag("downstream", downstream_path, "1h", enabled=False)
        upstream_run = store.start_dag_run("upstream", [], trigger_reason="manual")
        store.complete_dag_run(upstream_run, success=True)
        calls: list[str] = []

        triggered = Scheduler(store, execute=make_fake_execute(store, calls)).tick()

        assert len(triggered) == 1
        assert calls == [str(downstream_path)]
    finally:
        store.close()


def test_a_malformed_schedule_is_skipped_without_crashing_the_scheduler(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag(
            "broken", Path("broken.yaml"), "0 2 * * *", enabled=True
        )  # cron, unsupported
        store.register_dag("good", Path("good.yaml"), "1s", enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        triggered = scheduler.tick()  # must not raise

        assert calls == ["good.yaml"]
        assert len(triggered) == 1
    finally:
        store.close()


def test_unexpected_task_failure_does_not_prevent_later_due_dag_execution(
    tmp_path: Path,
) -> None:
    failing_path = tmp_path / "a-failing.yaml"
    successful_path = tmp_path / "b-successful.yaml"
    write_single_task_dag(
        failing_path,
        name="a-failing",
        pipeline="unexpected.yaml",
    )
    write_single_task_dag(
        successful_path,
        name="b-successful",
        pipeline="success.yaml",
    )
    store = make_store(tmp_path)
    try:
        store.register_dag("a-failing", failing_path, "1s", enabled=True)
        store.register_dag("b-successful", successful_path, "1s", enabled=True)
        application = _UnexpectedTaskApplication()
        scheduler = Scheduler(store, execute=execute_with_application(application))

        triggered = scheduler.tick()

        assert len(triggered) == 2
        assert application.calls == ["unexpected.yaml", "success.yaml"]
        assert store.get_dag_run(triggered[0]).status is DagRunStatus.FAILED  # type: ignore[union-attr]
        assert store.get_dag_run(triggered[1]).status is DagRunStatus.SUCCEEDED  # type: ignore[union-attr]
    finally:
        store.close()


def test_polling_continues_after_unexpected_task_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_path = tmp_path / "failing.yaml"
    write_single_task_dag(
        failing_path,
        name="failing",
        pipeline="unexpected.yaml",
    )
    store = make_store(tmp_path)
    try:
        store.register_dag("failing", failing_path, "1h", enabled=True)
        application = _UnexpectedTaskApplication()
        scheduler = Scheduler(
            store,
            execute=execute_with_application(application),
            poll_interval_seconds=0,
        )
        tick_count = 0
        real_tick = scheduler.tick

        def counting_tick() -> list[int]:
            nonlocal tick_count
            tick_count += 1
            return real_tick()

        monkeypatch.setattr(scheduler, "tick", counting_tick)

        scheduler.run(install_signal_handlers=False, max_ticks=2)

        assert tick_count == 2
        assert application.calls == ["unexpected.yaml"]
        run = store.latest_dag_run("failing")
        assert run is not None
        assert run.status is DagRunStatus.FAILED
    finally:
        store.close()


def test_missed_run_catch_up_policy_skips_backlog_and_triggers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5-minute schedule, downtime spanning what would have been many
    missed occurrences (3 hours -- 36 of them). On restart, the store's
    real last-run time is the only thing consulted: exactly one run fires
    to catch up to 'now', not 36 backlogged ones. This is the deliberate,
    documented default (skip missed occurrences, don't burst-fire) from
    Step 3 -- and it falls out naturally from computing due-ness against
    the real last start time rather than an idealized schedule timeline.
    """
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "5m", enabled=True)
        clock = Clock(datetime.now(UTC))
        monkeypatch.setattr("nexolith.state.store._now", lambda: clock.now().isoformat())
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls), now=clock.now)
        scheduler.tick()
        assert len(calls) == 1

        clock.advance(timedelta(hours=3))  # daemon "was down" for 3 hours
        triggered = scheduler.tick()

        assert len(triggered) == 1  # exactly one catch-up run, not 36
        assert len(calls) == 2

        clock.advance(timedelta(seconds=1))  # immediately after: not due again yet
        triggered_again = scheduler.tick()
        assert triggered_again == []
        assert len(calls) == 2
    finally:
        store.close()


def test_a_dag_with_no_prior_run_is_due_on_its_first_eligible_tick(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1h", enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        triggered = scheduler.tick()

        assert len(triggered) == 1  # doesn't wait a full hour before its very first run
    finally:
        store.close()


# -- run()/stop(): the real loop ------------------------------------------


def test_run_stops_after_max_ticks_without_installing_signal_handlers(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1h", enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(
            store, execute=make_fake_execute(store, calls), poll_interval_seconds=0.01
        )

        scheduler.run(install_signal_handlers=False, max_ticks=3)

        # First tick triggers (no prior run); the next two ticks are well
        # within the 1h interval, so only one call total.
        assert len(calls) == 1
    finally:
        store.close()


def test_stop_interrupts_a_waiting_run_loop_promptly(tmp_path: Path) -> None:
    """Proves shutdown is responsive, not just eventually-true: a long poll
    interval (30s) combined with a strict, short real-time budget means
    this can only pass if stop() actually interrupts Event.wait() rather
    than the loop exiting via the poll interval simply elapsing (which
    would take 30 real seconds).

    scheduler.run() itself runs in this test's own (main) thread -- all
    StateStore access stays on the thread that created the sqlite
    connection, matching story 2's deliberate check_same_thread=True
    design. Only stop() (pure in-memory Event.set(), no DB access at all)
    runs from a second thread, purely to simulate the concurrent request a
    signal handler would deliver in production. An earlier version of this
    test ran run() itself in a background thread and technically passed,
    but for the wrong reason: that thread crashed immediately on its first
    tick() with sqlite3's cross-thread ProgrammingError, which happens to
    look like prompt shutdown from the outside. Caught via pytest's
    unhandled-thread-exception warning, not the assertion.
    """
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1h", enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(
            store, execute=make_fake_execute(store, calls), poll_interval_seconds=30.0
        )

        def stop_soon() -> None:
            time.sleep(0.1)  # let the main thread's loop reach its first Event.wait()
            scheduler.stop()

        stopper = threading.Thread(target=stop_soon)
        started_at = time.monotonic()
        stopper.start()
        scheduler.run(install_signal_handlers=False)
        elapsed = time.monotonic() - started_at
        stopper.join(timeout=2.0)

        assert elapsed < 2.0, "stop() did not interrupt the waiting loop promptly"
    finally:
        store.close()


def test_sigint_handler_is_registered_and_calling_it_stops_the_loop(tmp_path: Path) -> None:
    """Verified concretely rather than assumed: `os.kill(os.getpid(),
    signal.SIGINT)` was tried against this exact mechanism in this exact
    Windows environment and does NOT reliably reach a registered Python
    signal handler here -- it appears to terminate the process directly
    (observed exit code matching SIGINT's signal number, no handler output
    ever printed), evidently because there is no real attached console
    control-event target in this shell setup. That is itself the
    Windows-specific finding this test exists to act on: simulating actual
    OS signal delivery is not a reliable thing to script in an automated
    test here, so this instead verifies what's real and controllable --
    that `signal.signal(SIGINT, ...)` is really called with a handler whose
    only job is to call stop(), by invoking that exact registered callable
    directly. `signal.SIGINT` is documented as one of the small set of
    signals `signal.signal()` actually supports on Windows (unlike
    SIGTERM's cross-process case, see `_install_signal_handlers`'s own
    docstring), so registration itself is expected to succeed here. The
    original handler is always restored so this test cannot leave the
    pytest process's own signal state altered.
    """
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1h", enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        original_handler = signal.getsignal(signal.SIGINT)
        try:
            scheduler._install_signal_handlers()
            installed_handler = signal.getsignal(signal.SIGINT)
            assert installed_handler is not original_handler
            assert callable(installed_handler)

            assert not scheduler._stop_event.is_set()
            installed_handler(signal.SIGINT, None)
            assert scheduler._stop_event.is_set()
        finally:
            signal.signal(signal.SIGINT, original_handler)
    finally:
        store.close()


def test_shutdown_does_not_corrupt_state_a_running_dag_is_left_to_finish(tmp_path: Path) -> None:
    """stop() only sets the flag the loop checks between ticks -- it does
    not abort an in-flight execute() call. Confirm a run triggered just
    before stop() completes normally and lands in a clean terminal state,
    not stuck 'running' as if the process had crashed.
    """
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), "1s", enabled=True)
        calls: list[str] = []
        scheduler = Scheduler(
            store, execute=make_fake_execute(store, calls), poll_interval_seconds=0.01
        )

        scheduler.run(install_signal_handlers=False, max_ticks=1)
        scheduler.stop()

        run = store.latest_dag_run("etl")
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
        assert run.ended_at is not None
    finally:
        store.close()


# -- real end-to-end integration with story 3's executor ------------------


def test_real_execute_dag_integration(tmp_path: Path) -> None:
    """At least one test uses the real execute_dag() (story 3), not the
    fake stand-in above, to prove the scheduler genuinely integrates with
    the real executor -- and through it, the real PipelineApplication.
    """
    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,ready\n", encoding="utf-8")
    destination = tmp_path / "output.csv"
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        f"""
name: real-pipeline
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {destination.as_posix()}
""",
        encoding="utf-8",
    )
    dag_path = tmp_path / "workflow.yaml"
    dag_path.write_text(
        """
name: real-dag
tasks:
  - name: only
    pipeline: pipeline.yaml
    depends_on: []
""",
        encoding="utf-8",
    )

    store = make_store(tmp_path)
    try:
        store.register_dag("real-dag", dag_path, "1h", enabled=True)
        scheduler = Scheduler(store)  # real _default_execute, no injection

        triggered = scheduler.tick()

        assert len(triggered) == 1
        run = store.get_dag_run(triggered[0])
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED
        assert destination.is_file()
        assert destination.read_text(encoding="utf-8").strip().splitlines() == [
            "id,status",
            "1,ready",
        ]
    finally:
        store.close()


def test_default_execute_uses_schedule_trigger_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def fake_execute_dag(
        path: Path, store: StateStore, application: object = None, *, trigger_reason: str = "manual"
    ) -> int:
        captured["trigger_reason"] = trigger_reason
        run_id = store.start_dag_run("etl", [], trigger_reason=trigger_reason)
        store.complete_dag_run(run_id, success=True)
        return run_id

    monkeypatch.setattr("nexolith.scheduler.daemon.execute_dag", fake_execute_dag)

    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        run_id = _default_execute(Path("etl.yaml"), store)
        assert captured["trigger_reason"] == "schedule"
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.trigger_reason == "schedule"
    finally:
        store.close()


# -- NXL-90: a DAG-file-declared schedule, through the real loading path ----
#
# Every test above (and v0.3.2 story 4's own) writes a schedule straight
# into the store via register_dag() -- often against a Path() that isn't
# even a real file on disk -- which is precisely how it went unnoticed that
# no real DAG YAML could ever declare a schedule at all (DagConfig had no
# `schedule` field, and register_dag() was only ever called with
# schedule=None). These tests instead write a real DAG file with a real
# `schedule:` field and go through execute_dag() -- the same load_dag() +
# DagExecutor.run() registration path production code uses -- to prove the
# file's own value is what actually lands in the store and drives the
# real, running scheduler.


def write_scheduled_dag(tmp_path: Path, *, name: str, schedule: str) -> Path:
    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,ready\n", encoding="utf-8")
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        f"""
name: scheduled_pipeline
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(tmp_path / "output.csv").as_posix()}
""",
        encoding="utf-8",
    )
    dag_path = tmp_path / "workflow.yaml"
    dag_path.write_text(
        f"""
name: {name}
schedule: "{schedule}"
tasks:
  - name: only
    pipeline: pipeline.yaml
    depends_on: []
""",
        encoding="utf-8",
    )
    return dag_path


def test_a_dag_file_declared_schedule_is_registered_and_triggers_a_real_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexolith.dag import execute_dag

    dag_path = write_scheduled_dag(tmp_path, name="scheduled-dag", schedule="5m")

    clock = Clock(datetime.now(UTC))
    # Sync the store's own timestamps to the fake clock (same technique as
    # test_not_yet_due_schedule_does_not_trigger) so the first real run's
    # started_at lands on a timeline this test can deterministically
    # advance, rather than needing to sleep 5 real minutes.
    monkeypatch.setattr("nexolith.state.store._now", lambda: clock.now().isoformat())

    store = make_store(tmp_path)
    try:
        # The real loading path: load_dag() validates `schedule: "5m"` with
        # the real interval parser, then DagExecutor.run() registers the
        # DAG using that file-declared value -- not one this test hands to
        # register_dag() directly.
        first_run_id = execute_dag(dag_path, store)
        first_run = store.get_dag_run(first_run_id)
        assert first_run is not None
        assert first_run.status is DagRunStatus.SUCCEEDED

        registered = store.get_dag("scheduled-dag")
        assert registered is not None
        assert registered.schedule == "5m"

        scheduler = Scheduler(store, now=clock.now)  # real execute_dag, no injection

        # Not due yet: the first run just happened.
        assert scheduler.tick() == []

        clock.advance(timedelta(minutes=5))
        triggered = scheduler.tick()

        assert len(triggered) == 1
        second_run = store.get_dag_run(triggered[0])
        assert second_run is not None
        assert second_run.dag_name == "scheduled-dag"
        assert second_run.status is DagRunStatus.SUCCEEDED
        assert second_run.trigger_reason == "schedule"
        assert len(store.list_dag_runs("scheduled-dag")) == 2
    finally:
        store.close()


def test_a_dag_registered_via_register_dag_alone_triggers_on_the_schedulers_next_tick(
    tmp_path: Path,
) -> None:
    """NXL-103: `register_dag()` is the only other way (besides an actual
    manual run through `DagExecutor.run()`'s check-then-register side
    effect) a DAG's `dags` row gets created. This proves the scheduler
    treats a DAG registered that way identically to one that's already been
    run once: due immediately on its first eligible tick -- same as
    test_a_dag_with_no_prior_run_is_due_on_its_first_eligible_tick -- and
    actually executes when tick() runs, not merely marked due. The DAG is
    never run manually anywhere in this test; execute_dag()/DagExecutor.run()
    are never called directly, only through the scheduler's own tick().
    """
    from nexolith.dag import register_dag

    dag_path = write_scheduled_dag(tmp_path, name="never-run-dag", schedule="5m")

    store = make_store(tmp_path)
    try:
        result = register_dag(dag_path, store)
        assert result.created
        assert result.record.schedule == "5m"
        assert result.record.enabled is True
        assert store.list_dag_runs("never-run-dag") == []  # confirm: truly never run

        scheduler = Scheduler(store)
        triggered = scheduler.tick()

        assert len(triggered) == 1
        run = store.get_dag_run(triggered[0])
        assert run is not None
        assert run.dag_name == "never-run-dag"
        assert run.status is DagRunStatus.SUCCEEDED
        assert run.trigger_reason == "schedule"
    finally:
        store.close()


def test_a_malformed_dag_file_declared_schedule_is_a_clear_validation_error(
    tmp_path: Path,
) -> None:
    from nexolith.dag import execute_dag
    from nexolith.exceptions import ConfigurationError

    dag_path = write_scheduled_dag(tmp_path, name="broken-schedule-dag", schedule="5")

    store = make_store(tmp_path)
    try:
        with pytest.raises(ConfigurationError, match="Invalid DAG schedule"):
            execute_dag(dag_path, store)

        # Never even reached registration -- the bad file never got a
        # chance to poison the store with an unparseable schedule the
        # scheduler would only fail on later, mid-tick.
        assert store.get_dag("broken-schedule-dag") is None
    finally:
        store.close()


def test_a_dag_without_a_declared_schedule_is_unaffected_by_this_fix(tmp_path: Path) -> None:
    """A DAG that never sets `schedule:` (the cross-DAG-only case from the
    previous story included) registers with schedule=None and is never
    triggered by interval, exactly as before this fix -- schedule: is
    purely additive.
    """
    from nexolith.dag import execute_dag

    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,ready\n", encoding="utf-8")
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        f"""
name: unscheduled_pipeline
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(tmp_path / "output.csv").as_posix()}
""",
        encoding="utf-8",
    )
    dag_path = tmp_path / "workflow.yaml"
    dag_path.write_text(
        """
name: unscheduled-dag
tasks:
  - name: only
    pipeline: pipeline.yaml
    depends_on: []
""",
        encoding="utf-8",
    )

    store = make_store(tmp_path)
    try:
        execute_dag(dag_path, store)

        registered = store.get_dag("unscheduled-dag")
        assert registered is not None
        assert registered.schedule is None

        scheduler = Scheduler(store)
        assert scheduler.tick() == []  # no schedule and no trigger declared -- never due
    finally:
        store.close()


# -- NXL-86: DAG priority resolves contention within one tick's due set -----


def write_priority_dag(tmp_path: Path, *, name: str, priority: str | None = None) -> Path:
    """A DAG file whose only content that matters here is `priority:` --
    its task's `pipeline:` never has to point at a real file, since
    `tick()`'s own due-ness/priority read (`read_dag_config`) never
    validates referenced pipelines, only `load_dag()` does.
    """
    dag_path = tmp_path / f"{name}.yaml"
    priority_line = f"priority: {priority}\n" if priority is not None else ""
    dag_path.write_text(
        f"""
name: {name}
{priority_line}tasks:
  - name: only
    pipeline: unused.yaml
    depends_on: []
""",
        encoding="utf-8",
    )
    return dag_path


def test_multiple_due_dags_execute_in_priority_order(tmp_path: Path) -> None:
    """high before normal before low, proven via an explicit execution-
    order log (make_fake_execute's `calls`) -- not just "doesn't crash".
    Registered in an order that does not match priority order, so a
    passing result can't be an accident of registration/iteration order.
    """
    store = make_store(tmp_path)
    try:
        low_path = write_priority_dag(tmp_path, name="low-dag", priority="low")
        normal_path = write_priority_dag(tmp_path, name="normal-dag", priority="normal")
        high_path = write_priority_dag(tmp_path, name="high-dag", priority="high")
        critical_path = write_priority_dag(tmp_path, name="critical-dag", priority="critical")

        store.register_dag("low-dag", low_path, "1s", enabled=True)
        store.register_dag("critical-dag", critical_path, "1s", enabled=True)
        store.register_dag("normal-dag", normal_path, "1s", enabled=True)
        store.register_dag("high-dag", high_path, "1s", enabled=True)

        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        triggered = scheduler.tick()

        assert len(triggered) == 4
        assert calls == [str(critical_path), str(high_path), str(normal_path), str(low_path)]
    finally:
        store.close()


def test_equal_priority_dags_tie_break_by_name_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    try:
        paths = {}
        for name in ("charlie", "alpha", "bravo"):
            paths[name] = write_priority_dag(tmp_path, name=name, priority="normal")
            store.register_dag(name, paths[name], "1s", enabled=True)

        clock = Clock(datetime.now(UTC))
        monkeypatch.setattr("nexolith.state.store._now", lambda: clock.now().isoformat())
        expected = [str(paths["alpha"]), str(paths["bravo"]), str(paths["charlie"])]

        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls), now=clock.now)
        scheduler.tick()
        assert calls == expected

        # Repeated independently (a fresh due-set decision, not a cached
        # order) to confirm the tie-break is consistent, not incidental.
        calls.clear()
        clock.advance(timedelta(seconds=2))
        scheduler.tick()
        assert calls == expected
    finally:
        store.close()


def test_dag_with_no_declared_priority_sorts_exactly_like_normal_priority(
    tmp_path: Path,
) -> None:
    """Regression for Step 2's confirmed "today's behavior": before this
    story, `list_dags()`'s own `ORDER BY name` was the only ordering that
    ever existed, since tick() evaluated and executed each DAG inline, one
    at a time, in that query's order. A DAG that never declares `priority:`
    must default to exactly that -- proven here with an explicit
    execution-order log across DAGs that mix "no priority: line at all"
    with an explicit "priority: normal", which must be indistinguishable.
    """
    store = make_store(tmp_path)
    try:
        paths = {}
        for name in ("bravo", "alpha"):
            paths[name] = write_priority_dag(tmp_path, name=name, priority=None)
            store.register_dag(name, paths[name], "1s", enabled=True)
        paths["charlie"] = write_priority_dag(tmp_path, name="charlie", priority="normal")
        store.register_dag("charlie", paths["charlie"], "1s", enabled=True)

        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        scheduler.tick()

        assert calls == [str(paths["alpha"]), str(paths["bravo"]), str(paths["charlie"])]
    finally:
        store.close()


def test_priority_ordering_applies_across_interval_and_cross_dag_due_dags(
    tmp_path: Path,
) -> None:
    """Two DAGs become due in the same tick through two different
    mechanisms (NXL-85's cross-DAG trigger and plain interval scheduling)
    with priorities that invert what plain alphabetical order would give --
    proving priority ordering is applied uniformly to the whole due set,
    regardless of which condition made each member of it due.
    """
    from nexolith.dag import execute_dag

    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,ready\n", encoding="utf-8")
    upstream_pipeline = tmp_path / "upstream_pipeline.yaml"
    upstream_pipeline.write_text(
        f"""
name: upstream_pipeline
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(tmp_path / "upstream_out.csv").as_posix()}
""",
        encoding="utf-8",
    )
    upstream_dag_path = tmp_path / "upstream_dag.yaml"
    upstream_dag_path.write_text(
        """
name: upstream
tasks:
  - name: only
    pipeline: upstream_pipeline.yaml
    depends_on: []
""",
        encoding="utf-8",
    )

    # "a-interval" would sort before "z-downstream" by name alone, and it
    # becomes due first (interval, no prior run) -- but it's low priority
    # against the downstream's critical, so priority must still win.
    interval_path = tmp_path / "a-interval.yaml"
    interval_path.write_text(
        """
name: a-interval
priority: low
schedule: "1h"
tasks:
  - name: only
    pipeline: unused.yaml
    depends_on: []
""",
        encoding="utf-8",
    )
    downstream_path = tmp_path / "z-downstream.yaml"
    downstream_path.write_text(
        """
name: z-downstream
priority: critical
trigger:
  on_success_of: [upstream]
tasks:
  - name: only
    pipeline: unused.yaml
    depends_on: []
""",
        encoding="utf-8",
    )

    store = make_store(tmp_path)
    try:
        store.register_dag("a-interval", interval_path, "1h", enabled=True)
        store.register_dag("z-downstream", downstream_path, None, enabled=True)

        execute_dag(upstream_dag_path, store)  # real, outside the scheduler

        calls: list[str] = []
        scheduler = Scheduler(store, execute=make_fake_execute(store, calls))

        triggered = scheduler.tick()

        assert len(triggered) == 2
        assert calls == [str(downstream_path), str(interval_path)]
    finally:
        store.close()
