"""Atomic scheduler ownership recorded as PID plus process creation time.

A PID is reusable and is therefore never sufficient proof of ownership.
New markers persist the scheduler process creation time. Legacy, corrupt, or
unverifiable markers fail closed: observers never signal or unlink them, and
startup only recovers markers proven dead or owned by a different process.
Acquisition and release also share a persistent companion file locked by the
operating system for the scheduler's full lifetime. The lock is released on
normal shutdown or automatically by the OS after a crash, and the companion
file itself is intentionally harmless when left on disk.

Remote stop is identity-bound too. Windows verifies creation time and calls
``TerminateProcess`` through one stable process handle; Linux opens a pidfd
before verification and signals that descriptor. Platforms without an
equivalent primitive refuse remote termination rather than falling back to a
PID-only signal.
"""

import errno
import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Protocol, Self, cast

from nexolith.process_identity import (
    ProcessIdentity,
    ProcessIdentityLookupStatus,
    ProcessIdentityProvider,
    ProcessIdentityUnavailable,
    ProcessTerminationStatus,
    lookup_process_identity,
    terminate_process_identity,
)
from nexolith.state.paths import default_state_dir

_PIDFILE_NAME = "scheduler.pid"


class _WindowsLockModule(Protocol):
    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, nbytes: int) -> None: ...


class _PosixLockModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, fd: int, operation: int) -> None: ...


class _PidFilePid(int):
    """An int that carries identity into unchanged observer call sites."""

    owner_create_time_ns: int | None

    def __new__(cls, pid: int, owner_create_time_ns: int | None) -> Self:
        value = super().__new__(cls, pid)
        value.owner_create_time_ns = owner_create_time_ns
        return value


@dataclass(frozen=True, slots=True)
class PidFileRecord:
    pid: int
    started_at: str
    owner_create_time_ns: int | None = None

    @property
    def owner_identity(self) -> ProcessIdentity | None:
        if self.owner_create_time_ns is None:
            return None
        return ProcessIdentity(pid=int(self.pid), create_time_ns=self.owner_create_time_ns)


@dataclass(slots=True)
class PidFileLease:
    """OS-backed exclusive ownership held for one scheduler's full lifetime."""

    pidfile_path: Path
    _stream: IO[bytes]
    _released: bool = False

    @property
    def is_held(self) -> bool:
        return not self._released

    def release(self) -> None:
        if self._released:
            return
        try:
            _unlock_coordination_file(self._stream)
        finally:
            self._released = True
            self._stream.close()


@dataclass(frozen=True, slots=True)
class PidFileClaim:
    acquired: bool
    existing: PidFileRecord | None = None
    owned: PidFileRecord | None = None
    lease: PidFileLease | None = None


class PidFileOwnerStatus(StrEnum):
    MATCHING = "matching"
    DEAD = "dead"
    REUSED = "reused"
    LEGACY = "legacy"
    ACCESS_DENIED = "access_denied"
    UNAVAILABLE = "unavailable"


def default_pidfile_path() -> Path:
    return default_state_dir() / _PIDFILE_NAME


def _payload(record: PidFileRecord) -> str:
    data: dict[str, int | str] = {"pid": int(record.pid), "started_at": record.started_at}
    if record.owner_create_time_ns is not None:
        data["owner_create_time_ns"] = record.owner_create_time_ns
    return json.dumps(data)


def _record_for_pid(
    pid: int,
    started_at: str,
    identity_provider: ProcessIdentityProvider,
) -> PidFileRecord:
    lookup = identity_provider(pid)
    create_time_ns = (
        lookup.identity.create_time_ns
        if lookup.status is ProcessIdentityLookupStatus.FOUND and lookup.identity is not None
        else None
    )
    return PidFileRecord(pid=pid, started_at=started_at, owner_create_time_ns=create_time_ns)


def write_pidfile(
    path: Path,
    pid: int,
    started_at: str,
    *,
    identity_provider: ProcessIdentityProvider = lookup_process_identity,
) -> None:
    """Write a marker for tests and compatibility helpers.

    A live process gets a creation-time identity. A PID that cannot be
    identified produces a legacy-format marker, which all control paths
    intentionally treat as unverifiable and never signal.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_payload(_record_for_pid(pid, started_at, identity_provider)), encoding="utf-8")


def read_pidfile(path: Path) -> PidFileRecord | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return None
        pid = data["pid"]
        started_at = data["started_at"]
        raw_create_time = data.get("owner_create_time_ns")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        if not isinstance(started_at, str) or not started_at:
            return None
        if raw_create_time is not None and (
            isinstance(raw_create_time, bool)
            or not isinstance(raw_create_time, int)
            or raw_create_time <= 0
        ):
            return None
        create_time_ns = raw_create_time
        return PidFileRecord(
            pid=_PidFilePid(pid, create_time_ns),
            started_at=started_at,
            owner_create_time_ns=create_time_ns,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _windows_lock_module() -> _WindowsLockModule:
    return cast(_WindowsLockModule, importlib.import_module("msvcrt"))


def _posix_lock_module() -> _PosixLockModule:
    return cast(_PosixLockModule, importlib.import_module("fcntl"))


def _try_lock_coordination_file(stream: IO[bytes]) -> bool:
    stream.seek(0)
    try:
        if sys.platform == "win32":
            windows_lock = _windows_lock_module()
            windows_lock.locking(stream.fileno(), windows_lock.LK_NBLCK, 1)
        else:
            posix_lock = _posix_lock_module()
            posix_lock.flock(stream.fileno(), posix_lock.LOCK_EX | posix_lock.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise
    return True


def _unlock_coordination_file(stream: IO[bytes]) -> None:
    stream.seek(0)
    if sys.platform == "win32":
        windows_lock = _windows_lock_module()
        windows_lock.locking(stream.fileno(), windows_lock.LK_UNLCK, 1)
    else:
        posix_lock = _posix_lock_module()
        posix_lock.flock(stream.fileno(), posix_lock.LOCK_UN)


def _acquire_pidfile_lease(path: Path) -> PidFileLease | None:
    """Try to lock the persistent companion file without waiting.

    The file stays on disk and contains one inert byte. The operating system
    releases its lock automatically if the scheduler crashes.
    """
    lock_path = path.with_name(f"{path.name}.lock")
    stream = lock_path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        if not _try_lock_coordination_file(stream):
            stream.close()
            return None
    except BaseException:
        stream.close()
        raise
    return PidFileLease(pidfile_path=path, _stream=stream)


def release_pidfile(claim: PidFileClaim) -> bool:
    """Remove this claim while its full-lifetime ownership lease is held."""
    lease = claim.lease
    owned = claim.owned
    if not claim.acquired or lease is None or owned is None or not lease.is_held:
        return False
    try:
        current = read_pidfile(lease.pidfile_path)
        if current != owned:
            return False
        try:
            lease.pidfile_path.unlink()
        except FileNotFoundError:
            return False
        return True
    finally:
        lease.release()


def pidfile_owner_status(
    record: PidFileRecord,
    *,
    identity_provider: ProcessIdentityProvider = lookup_process_identity,
) -> PidFileOwnerStatus:
    expected = record.owner_identity
    if expected is None:
        return PidFileOwnerStatus.LEGACY
    lookup = identity_provider(int(record.pid))
    if lookup.status is ProcessIdentityLookupStatus.NOT_FOUND:
        return PidFileOwnerStatus.DEAD
    if lookup.status is ProcessIdentityLookupStatus.ACCESS_DENIED:
        return PidFileOwnerStatus.ACCESS_DENIED
    if lookup.status is ProcessIdentityLookupStatus.UNAVAILABLE:
        return PidFileOwnerStatus.UNAVAILABLE
    if lookup.identity != expected:
        return PidFileOwnerStatus.REUSED
    return PidFileOwnerStatus.MATCHING


def _raw_pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_process_alive(pid: int) -> bool:
    """Check identity for pidfile-derived values, liveness for plain PIDs."""
    if isinstance(pid, _PidFilePid):
        if pid.owner_create_time_ns is None:
            return False
        record = PidFileRecord(
            pid=pid,
            started_at="",
            owner_create_time_ns=pid.owner_create_time_ns,
        )
        return pidfile_owner_status(record) is PidFileOwnerStatus.MATCHING
    return _raw_pid_is_alive(pid)


def acquire_pidfile(
    path: Path,
    pid: int,
    started_at: str,
    *,
    identity_provider: ProcessIdentityProvider = lookup_process_identity,
) -> PidFileClaim:
    """Claim the lifetime lease, then atomically create the owner marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    owner_lookup = identity_provider(pid)
    if owner_lookup.status is not ProcessIdentityLookupStatus.FOUND:
        raise ProcessIdentityUnavailable(
            f"Cannot determine scheduler process identity ({owner_lookup.status.value})"
        )
    assert owner_lookup.identity is not None
    if owner_lookup.identity.pid != pid:
        raise ProcessIdentityUnavailable("Scheduler process identity returned the wrong PID")
    owned = PidFileRecord(pid, started_at, owner_lookup.identity.create_time_ns)
    payload = _payload(owned)
    lease = _acquire_pidfile_lease(path)
    if lease is None:
        return PidFileClaim(acquired=False, existing=read_pidfile(path))
    keep_lease = False

    def exclusive_create() -> bool:
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        except FileExistsError:
            return False
        return True

    try:
        if exclusive_create():
            keep_lease = True
            return PidFileClaim(acquired=True, owned=owned, lease=lease)

        existing = read_pidfile(path)
        if existing is None:
            return PidFileClaim(acquired=False)
        status = pidfile_owner_status(existing, identity_provider=identity_provider)
        if status not in {PidFileOwnerStatus.DEAD, PidFileOwnerStatus.REUSED}:
            return PidFileClaim(acquired=False, existing=existing)
        try:
            path.unlink()
        except FileNotFoundError:
            return PidFileClaim(acquired=False)
        if exclusive_create():
            keep_lease = True
            return PidFileClaim(acquired=True, owned=owned, lease=lease)
        return PidFileClaim(acquired=False, existing=read_pidfile(path))
    finally:
        if not keep_lease:
            lease.release()


def stop_process(pid: int) -> bool:
    """Signal only a verified process identity; never fall back to PID-only."""
    if isinstance(pid, _PidFilePid):
        if pid.owner_create_time_ns is None:
            return False
        identity = ProcessIdentity(int(pid), pid.owner_create_time_ns)
    else:
        lookup = lookup_process_identity(pid)
        if lookup.status is not ProcessIdentityLookupStatus.FOUND:
            return False
        assert lookup.identity is not None
        identity = lookup.identity
    return terminate_process_identity(identity) is ProcessTerminationStatus.SENT


def stop_pidfile_owner(record: PidFileRecord) -> ProcessTerminationStatus:
    """Terminate the exact owner recorded in ``record`` or fail closed."""
    identity = record.owner_identity
    if identity is None:
        return ProcessTerminationStatus.UNAVAILABLE
    return terminate_process_identity(identity)
