import os
import subprocess
import sys

import psutil
import pytest

from nexolith.process_identity import (
    ProcessIdentity,
    ProcessIdentityLookup,
    ProcessIdentityLookupStatus,
    current_process_identity,
    lookup_process_identity,
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
