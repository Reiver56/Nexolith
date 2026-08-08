"""UI-independent scheduler process lifecycle controls."""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from nexolith.process_identity import ProcessTerminationStatus
from nexolith.scheduler.pidfile import (
    PidFileOwnerStatus,
    PidFileRecord,
    default_pidfile_path,
    pidfile_owner_status,
    read_pidfile,
    stop_pidfile_owner,
)
from nexolith.scheduler.status import (
    SchedulerQueryState,
    SchedulerStatusSnapshot,
    query_scheduler_status,
)


class SchedulerStartState(StrEnum):
    STARTED = "started"
    ALREADY_RUNNING = "already_running"


class SchedulerStopState(StrEnum):
    STOPPED = "stopped"
    NOT_RUNNING = "not_running"
    UNCERTAIN = "uncertain"


class SchedulerControlFailure(StrEnum):
    IDENTITY_UNAVAILABLE = "identity_unavailable"
    CHILD_START_FAILED = "child_start_failed"
    STARTUP_TIMEOUT = "startup_timeout"
    TERMINATION_UNAVAILABLE = "termination_unavailable"


class SchedulerControlError(RuntimeError):
    def __init__(self, reason: SchedulerControlFailure, detail: str | None = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SchedulerStartResult:
    state: SchedulerStartState
    pid: int


@dataclass(frozen=True, slots=True)
class SchedulerStopResult:
    state: SchedulerStopState
    pid: int | None = None
    forced: bool = False


class SchedulerChild(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...


SchedulerLauncher = Callable[[], SchedulerChild]
SchedulerStatusQuery = Callable[[], SchedulerStatusSnapshot]
PidFileReader = Callable[[Path], PidFileRecord | None]
PidFileOwnerQuery = Callable[[PidFileRecord], PidFileOwnerStatus]
PidFileTerminator = Callable[[PidFileRecord], ProcessTerminationStatus]
_START_LOCK = threading.Lock()


def _launch_scheduler_child() -> SchedulerChild:
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        start_new_session = True
    return subprocess.Popen(
        [sys.executable, "-m", "nexolith", "scheduler", "start"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )


def _wait_ignoring_errors(child: SchedulerChild) -> None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        child.wait()


def _reap_in_background(child: SchedulerChild) -> None:
    """Reap the direct child while leaving scheduler execution out-of-process."""
    threading.Thread(
        target=_wait_ignoring_errors,
        args=(child,),
        name="nexolith-scheduler-reaper",
        daemon=True,
    ).start()


def _stop_owned_child(child: SchedulerChild) -> None:
    try:
        child.terminate()
    except OSError:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError, TimeoutError):
        child.wait(timeout=2.0)


def _child_remains_running(child: SchedulerChild) -> bool:
    try:
        child.wait(timeout=0.5)
    except (subprocess.TimeoutExpired, TimeoutError):
        return True
    except OSError:
        return False
    return False


def _start_scheduler_process_unlocked(
    *,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
    launcher: SchedulerLauncher = _launch_scheduler_child,
    status_query: SchedulerStatusQuery = query_scheduler_status,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    reaper: Callable[[SchedulerChild], None] = _reap_in_background,
) -> SchedulerStartResult:
    """Spawn the existing foreground CLI and verify its identity-bound claim."""
    initial = status_query()
    if initial.state is SchedulerQueryState.RUNNING:
        assert initial.pid is not None
        return SchedulerStartResult(SchedulerStartState.ALREADY_RUNNING, initial.pid)
    if initial.state is SchedulerQueryState.UNKNOWN:
        raise SchedulerControlError(SchedulerControlFailure.IDENTITY_UNAVAILABLE)

    try:
        child = launcher()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchedulerControlError(SchedulerControlFailure.CHILD_START_FAILED) from exc

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        status = status_query()
        if status.state is SchedulerQueryState.RUNNING:
            assert status.pid is not None
            if status.pid == child.pid:
                reaper(child)
                return SchedulerStartResult(SchedulerStartState.STARTED, child.pid)
            if _child_remains_running(child):
                # Windows virtual-environment launchers may keep a wrapper
                # process around the real interpreter that owns the pidfile.
                reaper(child)
                return SchedulerStartResult(SchedulerStartState.STARTED, status.pid)
            return SchedulerStartResult(SchedulerStartState.ALREADY_RUNNING, status.pid)
        if child.poll() is not None:
            final = status_query()
            if final.state is SchedulerQueryState.RUNNING and final.pid is not None:
                return SchedulerStartResult(SchedulerStartState.ALREADY_RUNNING, final.pid)
            raise SchedulerControlError(SchedulerControlFailure.CHILD_START_FAILED)
        sleep(poll_interval_seconds)

    final = status_query()
    if final.state is SchedulerQueryState.RUNNING and final.pid is not None:
        if final.pid == child.pid or _child_remains_running(child):
            reaper(child)
            return SchedulerStartResult(SchedulerStartState.STARTED, final.pid)
        return SchedulerStartResult(SchedulerStartState.ALREADY_RUNNING, final.pid)
    _stop_owned_child(child)
    raise SchedulerControlError(SchedulerControlFailure.STARTUP_TIMEOUT)


def start_scheduler_process(
    *,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
    launcher: SchedulerLauncher = _launch_scheduler_child,
    status_query: SchedulerStatusQuery = query_scheduler_status,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    reaper: Callable[[SchedulerChild], None] = _reap_in_background,
) -> SchedulerStartResult:
    """Serialize local requests; the OS-backed pidfile still guards all processes."""
    with _START_LOCK:
        return _start_scheduler_process_unlocked(
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            launcher=launcher,
            status_query=status_query,
            monotonic=monotonic,
            sleep=sleep,
            reaper=reaper,
        )


def stop_scheduler_process(
    path: Path | None = None,
    *,
    read_record: PidFileReader = read_pidfile,
    owner_query: PidFileOwnerQuery = pidfile_owner_status,
    terminate: PidFileTerminator = stop_pidfile_owner,
    sleep: Callable[[float], None] = time.sleep,
    platform: str = sys.platform,
) -> SchedulerStopResult:
    """Stop only the exact PID-plus-creation-time owner; never unlink as observer."""
    pidfile_path = path or default_pidfile_path()
    record = read_record(pidfile_path)
    if record is None:
        if pidfile_path.exists():
            raise SchedulerControlError(SchedulerControlFailure.IDENTITY_UNAVAILABLE, "corrupt")
        return SchedulerStopResult(SchedulerStopState.NOT_RUNNING)

    owner_status = owner_query(record)
    if owner_status in {PidFileOwnerStatus.DEAD, PidFileOwnerStatus.REUSED}:
        return SchedulerStopResult(SchedulerStopState.NOT_RUNNING)
    if owner_status is not PidFileOwnerStatus.MATCHING:
        raise SchedulerControlError(
            SchedulerControlFailure.IDENTITY_UNAVAILABLE, owner_status.value
        )

    termination = terminate(record)
    if termination in {
        ProcessTerminationStatus.NOT_FOUND,
        ProcessTerminationStatus.IDENTITY_MISMATCH,
    }:
        return SchedulerStopResult(SchedulerStopState.NOT_RUNNING)
    if termination is not ProcessTerminationStatus.SENT:
        raise SchedulerControlError(
            SchedulerControlFailure.TERMINATION_UNAVAILABLE, termination.value
        )

    for _ in range(20):
        if owner_query(record) in {PidFileOwnerStatus.DEAD, PidFileOwnerStatus.REUSED}:
            return SchedulerStopResult(
                SchedulerStopState.STOPPED,
                int(record.pid),
                forced=platform == "win32",
            )
        sleep(0.1)
    final_owner = owner_query(record)
    if final_owner in {PidFileOwnerStatus.DEAD, PidFileOwnerStatus.REUSED}:
        state = SchedulerStopState.STOPPED
    else:
        state = SchedulerStopState.UNCERTAIN
    return SchedulerStopResult(
        state,
        int(record.pid),
        forced=platform == "win32",
    )
