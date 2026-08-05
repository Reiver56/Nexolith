"""The scheduler daemon: a polling loop that reads registered, enabled DAGs
and their interval schedules from the state store and triggers story 3's
`execute_dag()` when one is due. The architectural exception the ADR
explicitly authorizes (a persistent background process) -- meant to be run
as the entire lifetime of a dedicated foreground process, not spawned as an
in-process background thread inside something else. No OS-service
registration (Windows Service, systemd unit, ...) here -- that's explicitly
out of scope for this story; `run()` is a long-lived blocking call a future
CLI command starts and stops.
"""

import contextlib
import logging
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from nexolith.dag.executor import execute_dag
from nexolith.dag.validator import read_dag_config
from nexolith.exceptions import ConfigurationError
from nexolith.scheduler.interval import parse_interval
from nexolith.state import DagRecord, DagRunStatus, StateStore

logger = logging.getLogger(__name__)

# priority (NXL-86): rank order for resolving contention when multiple DAGs
# are due in the same tick -- higher runs first. Lives here, not on
# DagConfig itself (see that model's own `priority` field comment), since
# it's specifically about scheduling order. "normal" is the default and
# sorts identically to every other DAG that never set a priority, which is
# what keeps unset priority equivalent to today's plain by-name ordering.
_PRIORITY_RANK: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "critical": 3}


class _DueDag(NamedTuple):
    dag_record: DagRecord
    priority: str
    satisfied_upstreams: list[tuple[str, int]]


def _default_execute(path: Path, store: StateStore) -> int:
    return execute_dag(path, store, trigger_reason="schedule")


class Scheduler:
    """Not thread-shared: intended to run its whole life in one thread (the
    process's main thread, in real use). `threading.Event` is used purely
    as a correctly-interruptible sleep/wake primitive -- `stop()` can be
    called from a signal handler (which Python runs on the main thread's
    control flow, not a separate OS thread) or, in tests, from wherever the
    test chooses.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        execute: Callable[[Path, StateStore], int] | None = None,
        poll_interval_seconds: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._execute = execute or _default_execute
        self._poll_interval_seconds = poll_interval_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._stop_event = threading.Event()

    def tick(self) -> list[int]:
        """One evaluation pass over every registered DAG. Returns the
        dag_run ids newly triggered this pass, in the order they were
        actually executed (empty if nothing was due). A malformed schedule
        string, or a cross-DAG `trigger:`/`priority:` declaration that
        can't be read, on one DAG is logged and skipped -- neither may ever
        take down the whole daemon over one bad value.

        Two independent due-ness conditions (NXL-85): an interval schedule
        (`dag_record.schedule`, unchanged from v0.3.2 story 4) and cross-DAG
        triggers (declared in the DAG's own file, evaluated fresh every
        tick). Either alone is enough to trigger -- nothing about the
        interval check assumes it's the only way a DAG becomes due, and a
        DAG may use both at once. The existing running-DAG check applies
        before either condition is even evaluated, so it prevents duplicate
        concurrent execution the same way regardless of which condition (or
        both at once) made a DAG due.

        Two-phase (NXL-86): every due-ness decision for every registered
        DAG is made first, then the whole due set is sorted by priority
        (`_PRIORITY_RANK`, higher first, tied DAGs by name) before any of
        them actually execute -- resolving contention needs to see the
        full batch to order it, which a single interleaved
        evaluate-then-execute loop (the pre-NXL-86 shape) can't do. One
        real consequence, not papered over: a DAG whose cross-DAG trigger
        depends on another DAG that becomes due in this same tick no longer
        sees that other DAG's completion until the *next* tick -- due-ness
        for the whole batch is now decided before any execution happens,
        where before, an earlier-executed DAG's fresh completion was
        already visible to a later one evaluated further down the same
        loop. Accepted: the acceptance criteria asks for a real, whole-batch
        priority ordering, which requires exactly this.
        """
        due: list[_DueDag] = []
        running_dag_names = {run.dag_name for run in self._store.list_incomplete_dag_runs()}
        for dag_record in self._store.list_dags():
            if not dag_record.enabled or dag_record.name in running_dag_names:
                continue

            interval_due = False
            if dag_record.schedule is not None:
                try:
                    interval = parse_interval(dag_record.schedule)
                except ConfigurationError:
                    logger.warning(
                        "Skipping DAG '%s': invalid schedule %r",
                        dag_record.name,
                        dag_record.schedule,
                    )
                else:
                    interval_due = self._is_due(dag_record, interval)

            priority, cross_dag_due, satisfied_upstreams = self._read_priority_and_trigger_due(
                dag_record
            )

            if interval_due or cross_dag_due:
                due.append(_DueDag(dag_record, priority, satisfied_upstreams))

        due.sort(key=lambda item: (-_PRIORITY_RANK[item.priority], item.dag_record.name))

        triggered: list[int] = []
        for item in due:
            run_id = self._execute(Path(item.dag_record.source_path), self._store)
            triggered.append(run_id)
            for upstream_name, upstream_run_id in item.satisfied_upstreams:
                self._store.record_trigger_reaction(
                    item.dag_record.name, upstream_name, upstream_run_id
                )
        return triggered

    def _is_due(self, dag_record: DagRecord, interval: timedelta) -> bool:
        latest = self._store.latest_dag_run(dag_record.name)
        if latest is None:
            return True  # never run before -- due on its first eligible tick
        last_started = datetime.fromisoformat(latest.started_at)
        return self._now() - last_started >= interval

    def _read_priority_and_trigger_due(
        self, dag_record: DagRecord
    ) -> tuple[str, bool, list[tuple[str, int]]]:
        """Reads `dag_record`'s own file once for two purposes at once
        (NXL-85's `trigger.on_success_of` and NXL-86's `priority`, both
        purely declarative, read fresh every tick, never persisted to the
        store -- see DagConfig's own field comments): whether it has at
        least one upstream DAG with a completed, successful run this
        downstream hasn't reacted to yet, and its declared priority
        ("normal" if the file can't be read at all, same as an unset
        priority). Every (upstream_name, upstream_run_id) pair newly
        satisfied at this exact check is returned too -- `tick()` marks all
        of them reacted at once when it executes this DAG, rather than just
        the first, so two upstreams completing close together doesn't leave
        one still "unreacted" and force an immediate, redundant re-trigger
        on the very next tick.

        A DAG file that doesn't exist is silently treated as having no
        cross-DAG trigger and default priority -- most DAGs never declare
        either (including every interval-only DAG in this test suite's own
        fixtures, several of which use paths that were never meant to exist
        on disk), and warning about that on every tick would be pure noise.
        A file that exists but fails to parse/validate is a real
        misconfiguration and is logged, matching the malformed-interval-
        schedule precedent above.
        """
        source_path = Path(dag_record.source_path)
        if not source_path.is_file():
            return "normal", False, []
        try:
            dag_config = read_dag_config(source_path)
        except ConfigurationError as exc:
            logger.warning(
                "Skipping cross-DAG trigger check for DAG '%s': %s", dag_record.name, exc
            )
            return "normal", False, []
        if dag_config.trigger is None:
            return dag_config.priority, False, []

        satisfied: list[tuple[str, int]] = []
        for upstream_name in dag_config.trigger.on_success_of:
            latest_upstream = self._store.latest_dag_run(upstream_name)
            if latest_upstream is None or latest_upstream.status is not DagRunStatus.SUCCEEDED:
                continue
            last_reacted = self._store.get_last_reacted_upstream_run_id(
                dag_record.name, upstream_name
            )
            if last_reacted != latest_upstream.id:
                satisfied.append((upstream_name, latest_upstream.id))
        return dag_config.priority, bool(satisfied), satisfied

    def run(self, *, install_signal_handlers: bool = True, max_ticks: int | None = None) -> None:
        """Blocks until `stop()` is called (via a signal handler, another
        thread, or -- in tests -- `max_ticks` running out). `max_ticks` is a
        test-only escape hatch so tests never sleep for real wall-clock
        time waiting for the loop to end naturally.
        """
        self._stop_event.clear()
        if install_signal_handlers:
            self._install_signal_handlers()
        ticks = 0
        while not self._stop_event.is_set():
            self.tick()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            self._stop_event.wait(self._poll_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        """SIGINT (Ctrl+C in a foreground console process) is reliably
        deliverable and handleable via `signal.signal()` on Windows -- this
        is standard, documented CPython behavior, not the same concern that
        made `prompt_toolkit`'s own raw-terminal `handle_sigint` unreliable
        on Windows (that was about prompt_toolkit's internal keyboard-
        interrupt translation inside raw terminal mode, not plain SIGINT
        delivery to a normal console process, which is what this daemon
        is). SIGTERM is also registered where available, but on Windows it
        only reaches a registered handler for same-process
        `os.kill(os.getpid(), signal.SIGTERM)` calls -- there is no
        Windows equivalent of Unix's cross-process "send SIGTERM to
        gracefully ask another process to stop." A cross-process stop
        mechanism (e.g. a CLI `scheduler stop` command signaling an
        already-running daemon) is explicitly a later story's concern, not
        this one's.

        Either handler only sets the stop flag -- it does not raise or
        interrupt an in-flight `execute_dag()` call. A DAG run already in
        progress when shutdown is requested is allowed to finish on its
        own (success or failure) before the loop exits on its next check;
        aborting it mid-task would leave the state store in an ambiguous
        state indistinguishable from a real crash, which is exactly what
        story 2's crash-recovery design exists to avoid conflating with a
        deliberate, clean shutdown.
        """

        def handler(signum: int, frame: object) -> None:
            self.stop()

        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            # Not the main thread, or the platform doesn't support this
            # particular signal -- callers can still call stop() directly.
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, handler)
