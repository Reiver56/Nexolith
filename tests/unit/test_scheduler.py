import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
