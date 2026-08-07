"""A small marker file recording the scheduler daemon's process id and start
time, written by `scheduler start` and used by `scheduler status`/`stop` to
observe or control it from a different terminal/process.

Every mechanism here was verified directly against a real separate Windows
process during this story, not assumed:

- `os.kill(pid, 0)` (the POSIX existence-probe idiom) does NOT work on
  Windows -- it raises `OSError` (WinError 87, "the parameter is
  incorrect") even against a genuinely live process. Liveness on Windows is
  checked via `tasklist /FI "PID eq <pid>"` instead (a built-in Windows
  command, invoked through the standard library's `subprocess` -- not a new
  dependency), parsed for the PID as a quoted CSV field so the check is
  locale-independent (the "no matching task" message is localized prose
  that varies by system locale and was observed in Italian during this
  investigation; there is no reliable English substring to match against).

- Neither `os.kill(pid, signal.SIGTERM)` nor plain `taskkill /PID <pid>`
  (without `/F`) invokes a target Windows process's registered Python
  signal handler when sent from a different process -- both were tested
  directly against a real process with SIGINT/SIGTERM/SIGBREAK handlers
  registered, and in both cases the process was terminated (confirmed via
  `tasklist`) without the handler ever running (confirmed via the target's
  own log). This means a remote `stop` on Windows cannot rely on the
  target's own cleanup code (e.g. removing its own PID file, or letting an
  in-progress DAG run finish per story 4's own shutdown design) -- it is a
  forceful stop, not a graceful signal, despite `os.kill`/`SIGTERM`'s name.
  `stop_process()` here still uses `os.kill(pid, signal.SIGTERM)` (simplest
  standard-library call, and the one that behaves correctly -- genuinely
  gracefully -- on Unix) rather than shelling out to `taskkill`; the
  practical result on Windows is the same either way, and the caller (the
  `scheduler stop` CLI command) is responsible for its own PID-file cleanup
  afterward rather than trusting the target process to do it.
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexolith.state.paths import default_state_dir

_PIDFILE_NAME = "scheduler.pid"


@dataclass(frozen=True, slots=True)
class PidFileRecord:
    pid: int
    started_at: str


@dataclass(frozen=True, slots=True)
class PidFileClaim:
    acquired: bool
    existing: PidFileRecord | None = None


def default_pidfile_path() -> Path:
    return default_state_dir() / _PIDFILE_NAME


def write_pidfile(path: Path, pid: int, started_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "started_at": started_at}), encoding="utf-8")


def read_pidfile(path: Path) -> PidFileRecord | None:
    """None for a missing file, or one that isn't valid JSON with the
    expected shape (e.g. truncated by a crash mid-write) -- treated the
    same as "no marker" rather than raising, since a corrupt marker is not
    meaningfully different from a stale/absent one for status/stop's
    purposes.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(content)
        return PidFileRecord(pid=int(data["pid"]), started_at=str(data["started_at"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def remove_pidfile(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def is_process_alive(pid: int) -> bool:
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
        return True  # exists, just owned by another user
    return True


def acquire_pidfile(
    path: Path,
    pid: int,
    started_at: str,
    *,
    process_is_alive: Callable[[int], bool] = is_process_alive,
) -> PidFileClaim:
    """Atomically claim scheduler ownership with exclusive file creation.

    ``open(..., "x")`` maps to ``O_CREAT | O_EXCL`` in CPython on both
    Windows and POSIX. The filesystem decides creation atomically: only one
    concurrent starter can create the path.

    A fixed tombstone is exclusively created to serialize stale-file
    cleaners. The cleaner rechecks ownership while holding that claim,
    removes only the confirmed-dead marker, then retries creation once.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": pid, "started_at": started_at})

    def exclusive_create() -> bool:
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        except FileExistsError:
            return False
        return True

    tombstone = path.with_name(f"{path.name}.stale")
    if exclusive_create():
        remove_pidfile(tombstone)
        return PidFileClaim(acquired=True)

    existing = read_pidfile(path)
    if existing is None or process_is_alive(existing.pid):
        return PidFileClaim(acquired=False, existing=existing)

    try:
        with tombstone.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError:
        return PidFileClaim(acquired=False, existing=read_pidfile(path))

    try:
        existing = read_pidfile(path)
        if existing is None or process_is_alive(existing.pid):
            return PidFileClaim(acquired=False, existing=existing)
        remove_pidfile(path)
        if exclusive_create():
            return PidFileClaim(acquired=True)
        return PidFileClaim(acquired=False, existing=read_pidfile(path))
    finally:
        remove_pidfile(tombstone)


def stop_process(pid: int) -> bool:
    """Best-effort on Windows: see this module's own docstring. Returns
    True if a stop signal was sent (the process existed to receive it),
    False if it was already gone.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True
