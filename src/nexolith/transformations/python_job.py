"""The `python_job` transform step (NXL-83, ADR-7) -- Model A only:
in-process, receives and returns Nexolith's own in-flight `Rows`. Model B
(autonomous self-contained scripts, e.g. PySpark) is a separate, later
story; nothing here is aimed at subprocess isolation or external engines.
"""

from pathlib import Path

from nexolith.exceptions import TransformationError
from nexolith.jobs import JobContext, load_job_module, resolve_entrypoint
from nexolith.types import Rows, Scalar


class PythonJob:
    def __init__(self, file: str, entrypoint: str, parameters: dict[str, Scalar]) -> None:
        self.path = Path(file)
        self.entrypoint = entrypoint
        self.parameters = parameters

    def apply(self, rows: Rows) -> Rows:
        try:
            module = load_job_module(self.path)
            func = resolve_entrypoint(module, self.entrypoint, self.path)
            result = func(rows, JobContext(parameters=self.parameters))
        except Exception as exc:
            # Never interpolate the original exception's message -- it may
            # contain anything the job's own code happened to embed (a
            # connection string, a credential), same redaction posture
            # connectors/sql.py already takes with driver-level errors.
            # Only the exception's type name is safe to surface.
            raise TransformationError(
                f"Python job step failed: entrypoint '{self.entrypoint}' in "
                f"{self.path.name} raised {type(exc).__name__}."
            ) from exc
        if not isinstance(result, list):
            raise TransformationError(
                f"Python job step failed: entrypoint '{self.entrypoint}' in "
                f"{self.path.name} must return a list of rows, got {type(result).__name__}."
            )
        return result
