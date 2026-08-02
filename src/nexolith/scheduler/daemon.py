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

from nexolith.dag.executor import execute_dag
from nexolith.exceptions import ConfigurationError
from nexolith.scheduler.interval import parse_interval
from nexolith.state import DagRecord, StateStore

logger = logging.getLogger(__name__)


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
        dag_run ids newly triggered this pass (empty if nothing was due).
        A malformed schedule string on one DAG is logged and skipped --
        it must never take down the whole daemon over one bad value.
        """
        triggered: list[int] = []
        running_dag_names = {run.dag_name for run in self._store.list_incomplete_dag_runs()}
        for dag_record in self._store.list_dags():
            if not dag_record.enabled or dag_record.schedule is None:
                continue
            if dag_record.name in running_dag_names:
                continue
            try:
                interval = parse_interval(dag_record.schedule)
            except ConfigurationError:
                logger.warning(
                    "Skipping DAG '%s': invalid schedule %r", dag_record.name, dag_record.schedule
                )
                continue
            if self._is_due(dag_record, interval):
                run_id = self._execute(Path(dag_record.source_path), self._store)
                triggered.append(run_id)
        return triggered

    def _is_due(self, dag_record: DagRecord, interval: timedelta) -> bool:
        latest = self._store.latest_dag_run(dag_record.name)
        if latest is None:
            return True  # never run before -- due on its first eligible tick
        last_started = datetime.fromisoformat(latest.started_at)
        return self._now() - last_started >= interval

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
