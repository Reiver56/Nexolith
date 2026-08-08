"""Dynamic import of a user-supplied Python job file (NXL-83, ADR-7).

`importlib.util.spec_from_file_location` + `exec_module` -- never `eval`/
`exec` on a string, never an inline expression. This is exactly what a
normal `import` statement does, applied to an arbitrary path: the module's
own top-level code (imports, decorators, any module-level statement) runs
exactly as it would for any Python import. That is an inherent, documented
limitation, not something this module contains or sandboxes -- confirming
an entrypoint function exists in the file necessarily means importing the
file first. Nexolith documents `python_job` as trusted-local-code execution
with no sandboxing (ADR-7); this loader does not change that boundary, it
implements it.
"""

import importlib.util
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from nexolith.exceptions import ConfigurationError
from nexolith.jobs.context import JobContext
from nexolith.types import Rows

JobEntrypoint = Callable[[Rows, JobContext], Rows]


def load_job_module(path: Path) -> ModuleType:
    """Import `path` as a fresh module. Raises `ConfigurationError` (never
    lets a raw traceback from the file's own top-level code escape as
    something other than a Nexolith error) if the file cannot be loaded as
    Python or raises while importing.
    """
    # A unique module name per call (rather than reusing `path.stem`) avoids
    # colliding with an already-imported module of the same name elsewhere,
    # and avoids `sys.modules` accumulating stale entries across repeated
    # validate-then-run imports of the same file.
    module_name = f"nexolith_python_job_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigurationError(f"Could not load Python job file: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ConfigurationError(
            f"Python job file raised {type(exc).__name__} while importing: {path}"
        ) from exc
    return module


def resolve_entrypoint(module: ModuleType, entrypoint: str, path: Path) -> JobEntrypoint:
    """Confirm `entrypoint` exists in `module` and is callable, without
    calling it. Raises `ConfigurationError` naming exactly what's wrong.
    """
    func = getattr(module, entrypoint, None)
    if func is None:
        raise ConfigurationError(f"Python job file {path} has no function named '{entrypoint}'")
    if not callable(func):
        raise ConfigurationError(f"Python job file {path}: '{entrypoint}' is not callable")
    return func  # type: ignore[no-any-return]
