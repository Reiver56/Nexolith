"""Run a Model B script (NXL-88, ADR-7) as a subprocess.

Deliberately not an in-process call, for two independent reasons:

1. A script's own engine (e.g. PySpark's session lifecycle, JVM gateway,
   global state) should not run inside Nexolith's own long-lived process
   (the interactive session, the scheduler daemon) -- a crash or hang in
   the script must not be able to take that process down.
2. The acceptance criteria's own framing -- "the script's own venv" -- is
   only satisfiable this way. An in-process call is permanently bound to
   whatever interpreter is already running Nexolith; only spawning a
   separate process can honor a different `interpreter:` path pointing at
   a completely separate virtual environment.

Parameters cross the process boundary as a JSON file, not stdin: a temp
file is simpler to reason about, never competes with anything the script's
own code might want to do with stdin, and matches the project's existing
preference for file-based inputs (`query_file`, `python_job`'s own `file:`)
over an ad hoc stream protocol.
"""

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from nexolith.exceptions import ExecutionError
from nexolith.types import Scalar

logger = logging.getLogger(__name__)

_BOOTSTRAP_PATH = Path(__file__).parent / "_bootstrap.py"


def run_script(
    script_path: Path,
    entrypoint: str,
    parameters: dict[str, Scalar],
    interpreter: str | None,
) -> None:
    """Raises `ExecutionError` on a non-zero exit (an exception inside the
    script, a missing/misnamed entrypoint, or the script's own explicit
    `sys.exit`) -- never lets a raw subprocess failure escape as anything
    else. The exception message stays generic (script name + exit code):
    unlike Model A's caught-and-typed Python exception, a subprocess's
    stderr is arbitrary text Nexolith cannot inspect or redact -- it is
    logged (not silently dropped) so real debugging is still possible, but
    it is honestly NOT covered by the same redaction guarantee Model A's
    `type(exc).__name__`-only approach provides. ADR-7's trusted-local-code,
    no-sandboxing stance extends to trusting what a script chooses to print.
    """
    python = interpreter or sys.executable
    with tempfile.TemporaryDirectory(prefix="nexolith_script_job_") as tmpdir:
        params_path = Path(tmpdir) / "parameters.json"
        params_path.write_text(json.dumps(parameters), encoding="utf-8")
        try:
            result = subprocess.run(
                [python, str(_BOOTSTRAP_PATH), str(script_path), entrypoint, str(params_path)],
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            # The configured interpreter itself could not even be launched
            # (e.g. a bogus `interpreter:` path) -- distinct from the
            # script failing once running, but still a clean, expected
            # failure, not a raw traceback.
            raise ExecutionError(
                f"Could not launch interpreter '{python}' for script job "
                f"'{script_path.name}': {type(exc).__name__}."
            ) from exc

    if result.returncode != 0:
        if result.stderr:
            logger.error("Script job '%s' stderr:\n%s", script_path.name, result.stderr)
        raise ExecutionError(
            f"Script job '{script_path.name}' exited with code {result.returncode}. "
            "See the process log for captured output."
        )
