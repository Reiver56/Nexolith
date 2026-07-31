"""Process-local state for one interactive CLI session."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SelectedPipeline:
    """A user-facing path paired with its unambiguous resolved identity."""

    requested_path: Path
    resolved_path: Path


@dataclass(slots=True)
class SessionContext:
    """Mutable session state that never caches pipeline configuration."""

    pipeline: SelectedPipeline | None = None

    def select(self, requested_path: Path, resolved_path: Path) -> None:
        self.pipeline = SelectedPipeline(requested_path, resolved_path)

    def clear(self) -> bool:
        had_pipeline = self.pipeline is not None
        self.pipeline = None
        return had_pipeline
