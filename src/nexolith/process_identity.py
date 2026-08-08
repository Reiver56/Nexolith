"""Cross-platform process identity based on PID and process creation time.

A PID alone is only a reusable slot.  Pairing it with the operating
system's process creation time makes it suitable for deciding whether a
persisted DAG-run owner is still the same process.
"""

import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

import psutil

_NANOSECONDS_PER_SECOND = 1_000_000_000
_WINDOWS_CREATE_TIME_TOLERANCE_NS = 1_000


class _WindowsApiFunction(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, *args: object) -> object: ...


class _Kernel32(Protocol):
    OpenProcess: _WindowsApiFunction
    GetProcessTimes: _WindowsApiFunction
    TerminateProcess: _WindowsApiFunction
    CloseHandle: _WindowsApiFunction


class _WinDllFactory(Protocol):
    def __call__(self, name: str, *, use_last_error: bool) -> _Kernel32: ...


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    create_time_ns: int


class ProcessIdentityLookupStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ProcessIdentityLookup:
    status: ProcessIdentityLookupStatus
    identity: ProcessIdentity | None = None

    def __post_init__(self) -> None:
        has_identity = self.identity is not None
        if (self.status is ProcessIdentityLookupStatus.FOUND) != has_identity:
            raise ValueError("A found process lookup must contain exactly one identity")

    @classmethod
    def found(cls, identity: ProcessIdentity) -> "ProcessIdentityLookup":
        return cls(ProcessIdentityLookupStatus.FOUND, identity)

    @classmethod
    def not_found(cls) -> "ProcessIdentityLookup":
        return cls(ProcessIdentityLookupStatus.NOT_FOUND)

    @classmethod
    def access_denied(cls) -> "ProcessIdentityLookup":
        return cls(ProcessIdentityLookupStatus.ACCESS_DENIED)

    @classmethod
    def unavailable(cls) -> "ProcessIdentityLookup":
        return cls(ProcessIdentityLookupStatus.UNAVAILABLE)


ProcessIdentityProvider = Callable[[int], ProcessIdentityLookup]


class ProcessIdentityUnavailable(RuntimeError):
    """The current process cannot safely record its durable identity."""


def lookup_process_identity(pid: int) -> ProcessIdentityLookup:
    """Read a process's creation-time identity without exposing process data.

    Only PID and creation time are observed. Command lines, environment,
    executable paths, and user data are never requested or persisted.
    """
    try:
        create_time = psutil.Process(pid).create_time()
    except psutil.AccessDenied:
        return ProcessIdentityLookup.access_denied()
    except psutil.NoSuchProcess:
        return ProcessIdentityLookup.not_found()
    except (OSError, ValueError, psutil.Error):
        return ProcessIdentityLookup.unavailable()

    return ProcessIdentityLookup.found(
        ProcessIdentity(pid=pid, create_time_ns=round(create_time * _NANOSECONDS_PER_SECOND))
    )


def current_process_identity() -> ProcessIdentity:
    """Return this process's identity, or fail before a DAG run can start."""
    result = lookup_process_identity(os.getpid())
    if result.status is ProcessIdentityLookupStatus.FOUND:
        assert result.identity is not None
        return result.identity
    raise ProcessIdentityUnavailable(
        f"Cannot determine current process identity ({result.status.value})"
    )


class ProcessTerminationStatus(StrEnum):
    SENT = "sent"
    NOT_FOUND = "not_found"
    IDENTITY_MISMATCH = "identity_mismatch"
    ACCESS_DENIED = "access_denied"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


def terminate_process_identity(identity: ProcessIdentity) -> ProcessTerminationStatus:
    """Terminate exactly ``identity``, never whichever process owns its PID later.

    Windows keeps one process handle from identity verification through
    ``TerminateProcess``. Linux opens a pidfd before rechecking identity and
    signals through that descriptor. Other POSIX platforms have no standard
    identity-bound signaling primitive, so remote termination fails closed.
    """
    if sys.platform == "win32":
        return _terminate_process_identity_windows(identity)
    if sys.platform.startswith("linux"):
        return _terminate_process_identity_linux(identity)
    return ProcessTerminationStatus.UNSUPPORTED


def _terminate_process_identity_windows(identity: ProcessIdentity) -> ProcessTerminationStatus:
    import ctypes
    from ctypes import wintypes

    process_terminate = 0x0001
    process_query_limited_information = 0x1000
    error_access_denied = 5
    error_invalid_parameter = 87
    windows_to_unix_ticks = 116_444_736_000_000_000

    ctypes_members = vars(ctypes)
    win_dll = cast(_WinDllFactory, ctypes_members["WinDLL"])
    get_last_error = cast(Callable[[], int], ctypes_members["get_last_error"])
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_terminate | process_query_limited_information, False, identity.pid
    )
    if not handle:
        error = get_last_error()
        if error == error_invalid_parameter:
            return ProcessTerminationStatus.NOT_FOUND
        if error == error_access_denied:
            return ProcessTerminationStatus.ACCESS_DENIED
        return ProcessTerminationStatus.UNAVAILABLE

    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return ProcessTerminationStatus.UNAVAILABLE
        filetime = (created.dwHighDateTime << 32) | created.dwLowDateTime
        create_time_ns = (filetime - windows_to_unix_ticks) * 100
        # ProcessIdentity intentionally uses psutil's cross-platform float
        # seconds. Around present-day epoch values that serialization can
        # differ from the exact 100 ns FILETIME by a few hundred nanoseconds.
        # One microsecond covers that representation loss while remaining far
        # below the time needed to exit, recycle a PID, and create a process.
        if abs(create_time_ns - identity.create_time_ns) > _WINDOWS_CREATE_TIME_TOLERANCE_NS:
            return ProcessTerminationStatus.IDENTITY_MISMATCH
        if not kernel32.TerminateProcess(handle, 1):
            error = get_last_error()
            if error == error_access_denied:
                return ProcessTerminationStatus.ACCESS_DENIED
            return ProcessTerminationStatus.UNAVAILABLE
        return ProcessTerminationStatus.SENT
    finally:
        kernel32.CloseHandle(handle)


def _terminate_process_identity_linux(identity: ProcessIdentity) -> ProcessTerminationStatus:
    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if pidfd_open is None or pidfd_send_signal is None:
        return ProcessTerminationStatus.UNSUPPORTED

    try:
        pidfd = pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return ProcessTerminationStatus.NOT_FOUND
    except PermissionError:
        return ProcessTerminationStatus.ACCESS_DENIED
    except OSError:
        return ProcessTerminationStatus.UNAVAILABLE

    try:
        lookup = lookup_process_identity(identity.pid)
        if lookup.status is ProcessIdentityLookupStatus.NOT_FOUND:
            return ProcessTerminationStatus.NOT_FOUND
        if lookup.status is ProcessIdentityLookupStatus.ACCESS_DENIED:
            return ProcessTerminationStatus.ACCESS_DENIED
        if lookup.status is ProcessIdentityLookupStatus.UNAVAILABLE:
            return ProcessTerminationStatus.UNAVAILABLE
        if lookup.identity != identity:
            return ProcessTerminationStatus.IDENTITY_MISMATCH
        try:
            pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            return ProcessTerminationStatus.NOT_FOUND
        except PermissionError:
            return ProcessTerminationStatus.ACCESS_DENIED
        except OSError:
            return ProcessTerminationStatus.UNAVAILABLE
        return ProcessTerminationStatus.SENT
    finally:
        os.close(pidfd)
