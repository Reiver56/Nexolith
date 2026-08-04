"""The contract a `python_job` entrypoint receives (NXL-83, ADR-7).

Deliberately minimal -- not the whole `PipelineApplication`, not connector
internals, not the pipeline config. Just what a job genuinely needs: the
static parameters declared on its own step (same `dict[str, Scalar]` shape
story 2 uses for SQL parameters, reused as-is since it already fits).
"""

from dataclasses import dataclass, field

from nexolith.types import Scalar


@dataclass(frozen=True)
class JobContext:
    parameters: dict[str, Scalar] = field(default_factory=dict)
