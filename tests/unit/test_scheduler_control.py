from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nexolith.process_identity import ProcessTerminationStatus
from nexolith.scheduler import (
    PidFileOwnerStatus,
    PidFileRecord,
    SchedulerControlError,
    SchedulerControlFailure,
    SchedulerQueryState,
    SchedulerStartState,
    SchedulerStatusSnapshot,
    SchedulerStopState,
    query_scheduler_status,
    start_scheduler_process,
    stop_scheduler_process,
)


class FakeChild:
    def __init__(self, pid: int, *, return_code: int | None = None) -> None:
        self.pid = pid
        self.return_code = return_code
        self.terminated = False
        self.waited = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waited = True
        if self.return_code is None:
            raise TimeoutError
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 1


def test_start_verifies_child_claim_and_registers_reaper() -> None:
    child = FakeChild(4242)
    snapshots = iter(
        [
            SchedulerStatusSnapshot(SchedulerQueryState.NOT_RUNNING),
            SchedulerStatusSnapshot(SchedulerQueryState.RUNNING, pid=4242),
        ]
    )
    reaped: list[int] = []

    result = start_scheduler_process(
        launcher=lambda: child,
        status_query=lambda: next(snapshots),
        reaper=lambda process: reaped.append(process.pid),
        sleep=lambda _seconds: None,
    )

    assert result.state is SchedulerStartState.STARTED
    assert result.pid == 4242
    assert reaped == [4242]


def test_start_already_running_does_not_launch() -> None:
    launched = False

    def launch() -> FakeChild:
        nonlocal launched
        launched = True
        return FakeChild(1)

    result = start_scheduler_process(
        launcher=launch,
        status_query=lambda: SchedulerStatusSnapshot(SchedulerQueryState.RUNNING, pid=9),
    )

    assert result.state is SchedulerStartState.ALREADY_RUNNING
    assert launched is False


def test_start_failure_and_timeout_clean_owned_child() -> None:
    failed = FakeChild(10, return_code=1)
    with pytest.raises(SchedulerControlError) as child_failure:
        start_scheduler_process(
            launcher=lambda: failed,
            status_query=lambda: SchedulerStatusSnapshot(SchedulerQueryState.NOT_RUNNING),
            sleep=lambda _seconds: None,
        )
    assert child_failure.value.reason is SchedulerControlFailure.CHILD_START_FAILED

    timed_out = FakeChild(11)
    ticks = iter([0.0, 0.0, 2.0])
    with pytest.raises(SchedulerControlError) as timeout:
        start_scheduler_process(
            timeout_seconds=1.0,
            launcher=lambda: timed_out,
            status_query=lambda: SchedulerStatusSnapshot(SchedulerQueryState.NOT_RUNNING),
            monotonic=lambda: next(ticks),
            sleep=lambda _seconds: None,
        )
    assert timeout.value.reason is SchedulerControlFailure.STARTUP_TIMEOUT
    assert timed_out.terminated is True
    assert timed_out.waited is True


@pytest.mark.parametrize(
    ("owner_status", "termination", "alive_values", "expected"),
    [
        (
            PidFileOwnerStatus.DEAD,
            ProcessTerminationStatus.SENT,
            [],
            SchedulerStopState.NOT_RUNNING,
        ),
        (
            PidFileOwnerStatus.REUSED,
            ProcessTerminationStatus.SENT,
            [],
            SchedulerStopState.NOT_RUNNING,
        ),
        (
            PidFileOwnerStatus.MATCHING,
            ProcessTerminationStatus.IDENTITY_MISMATCH,
            [],
            SchedulerStopState.NOT_RUNNING,
        ),
        (
            PidFileOwnerStatus.MATCHING,
            ProcessTerminationStatus.SENT,
            [False],
            SchedulerStopState.STOPPED,
        ),
        (
            PidFileOwnerStatus.MATCHING,
            ProcessTerminationStatus.SENT,
            [True] * 21,
            SchedulerStopState.UNCERTAIN,
        ),
    ],
)
def test_stop_identity_bound_outcomes(
    tmp_path: Path,
    owner_status: PidFileOwnerStatus,
    termination: ProcessTerminationStatus,
    alive_values: list[bool],
    expected: SchedulerStopState,
) -> None:
    marker = tmp_path / "scheduler.pid"
    marker.write_text("owned", encoding="utf-8")
    record = PidFileRecord(4242, "then", 10)
    signals: list[PidFileRecord] = []
    values = iter(alive_values)

    result = stop_scheduler_process(
        marker,
        read_record=lambda _path: record,
        owner_query=lambda _record: owner_status,
        terminate=lambda seen: signals.append(seen) or termination,
        alive=lambda _pid: next(values),
        sleep=lambda _seconds: None,
        platform="win32",
    )

    assert result.state is expected
    if owner_status is not PidFileOwnerStatus.MATCHING:
        assert signals == []


@pytest.mark.parametrize("status", [PidFileOwnerStatus.LEGACY, PidFileOwnerStatus.UNAVAILABLE])
def test_stop_fails_closed_for_unverifiable_owner(
    tmp_path: Path, status: PidFileOwnerStatus
) -> None:
    marker = tmp_path / "scheduler.pid"
    marker.write_text("owned", encoding="utf-8")
    signalled = False

    def terminate(_record: PidFileRecord) -> ProcessTerminationStatus:
        nonlocal signalled
        signalled = True
        return ProcessTerminationStatus.SENT

    with pytest.raises(SchedulerControlError) as caught:
        stop_scheduler_process(
            marker,
            read_record=lambda _path: PidFileRecord(4242, "then", 10),
            owner_query=lambda _record: status,
            terminate=terminate,
        )
    assert caught.value.reason is SchedulerControlFailure.IDENTITY_UNAVAILABLE
    assert signalled is False


@pytest.mark.skipif(sys.platform != "win32", reason="current-platform disposable lifecycle")
def test_two_real_api_style_starts_leave_one_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("NEXOLITH_STATE_DIR", str(state_dir))
    barrier = threading.Barrier(2)

    def start() -> SchedulerStartState:
        barrier.wait(timeout=5)
        return start_scheduler_process().state

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = [
                future.result(timeout=15) for future in [pool.submit(start), pool.submit(start)]
            ]
        assert sorted(states) == [SchedulerStartState.ALREADY_RUNNING, SchedulerStartState.STARTED]
        snapshot = query_scheduler_status()
        assert snapshot.state is SchedulerQueryState.RUNNING
        stopped = stop_scheduler_process()
        assert stopped.state in {SchedulerStopState.STOPPED, SchedulerStopState.UNCERTAIN}
    finally:
        snapshot = query_scheduler_status()
        if snapshot.state is SchedulerQueryState.RUNNING:
            stop_scheduler_process()
        os.environ.pop("NEXOLITH_STATE_DIR", None)
