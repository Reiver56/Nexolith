import os
import subprocess
import sys

import psutil
import pytest

from nexolith.process_identity import (
    ProcessIdentity,
    ProcessIdentityLookup,
    ProcessIdentityLookupStatus,
    ProcessTerminationStatus,
    current_process_identity,
    lookup_process_identity,
    terminate_process_identity,
)


@pytest.mark.parametrize(
    ("status", "identity"),
    [
        (ProcessIdentityLookupStatus.FOUND, None),
        (ProcessIdentityLookupStatus.NOT_FOUND, ProcessIdentity(4242, 1)),
    ],
)
def test_lookup_result_rejects_inconsistent_identity(
    status: ProcessIdentityLookupStatus, identity: ProcessIdentity | None
) -> None:
    with pytest.raises(ValueError, match="must contain exactly one identity"):
        ProcessIdentityLookup(status, identity)


def test_real_current_process_identity_is_stable() -> None:
    current = current_process_identity()
    repeated = lookup_process_identity(os.getpid())

    assert current.pid == os.getpid()
    assert repeated.status is ProcessIdentityLookupStatus.FOUND
    assert repeated.identity == current


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific real-process coverage")
def test_real_windows_child_process_identity_is_stable() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        first = lookup_process_identity(process.pid)
        second = lookup_process_identity(process.pid)

        assert first.status is ProcessIdentityLookupStatus.FOUND
        assert first.identity is not None
        assert first.identity.pid == process.pid
        assert second == first
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_lookup_converts_creation_time_to_typed_nanoseconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        def create_time(self) -> float:
            return 1234.5

    monkeypatch.setattr(psutil, "Process", lambda pid: FakeProcess())

    result = lookup_process_identity(4242)

    assert result.status is ProcessIdentityLookupStatus.FOUND
    assert result.identity == ProcessIdentity(4242, 1_234_500_000_000)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (psutil.NoSuchProcess(4242), ProcessIdentityLookupStatus.NOT_FOUND),
        (psutil.AccessDenied(4242), ProcessIdentityLookupStatus.ACCESS_DENIED),
        (OSError("lookup failed"), ProcessIdentityLookupStatus.UNAVAILABLE),
    ],
)
def test_lookup_returns_typed_failure_status(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: ProcessIdentityLookupStatus,
) -> None:
    def fail(pid: int) -> object:
        raise error

    monkeypatch.setattr(psutil, "Process", fail)

    result = lookup_process_identity(4242)

    assert result.status is expected
    assert result.identity is None


@pytest.mark.skipif(
    sys.platform != "win32" and not sys.platform.startswith("linux"),
    reason="Identity-bound termination is implemented on Windows and Linux",
)
def test_real_identity_bound_termination_rejects_mismatch_then_stops_match() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lookup = lookup_process_identity(process.pid)
        assert lookup.identity is not None
        mismatch = ProcessIdentity(process.pid, lookup.identity.create_time_ns - 1_000_000_000)

        assert terminate_process_identity(mismatch) is ProcessTerminationStatus.IDENTITY_MISMATCH
        assert process.poll() is None

        assert terminate_process_identity(lookup.identity) is ProcessTerminationStatus.SENT
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle-specific coverage")
def test_windows_identity_bound_termination_never_calls_pid_only_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lookup = lookup_process_identity(process.pid)
        assert lookup.identity is not None

        def forbidden_pid_signal(pid: int, sig: int) -> None:
            raise AssertionError(f"PID-only signal attempted for {pid} with {sig}")

        monkeypatch.setattr(os, "kill", forbidden_pid_signal)

        assert terminate_process_identity(lookup.identity) is ProcessTerminationStatus.SENT
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_platform_without_identity_bound_signal_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    result = terminate_process_identity(ProcessIdentity(4242, 10))

    assert result is ProcessTerminationStatus.UNSUPPORTED
