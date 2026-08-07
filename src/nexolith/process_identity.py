"""Cross-platform process identity based on PID and process creation time.

A PID alone is only a reusable slot.  Pairing it with the operating
system's process creation time makes it suitable for deciding whether a
persisted DAG-run owner is still the same process.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import psutil

_NANOSECONDS_PER_SECOND = 1_000_000_000


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
