from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexolith.types import Scalar


class DagTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    pipeline: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    # parameters (NXL-82): DAG-run-time values for the task's own pipeline
    # `parameters:` block -- the DAG layer's answer to "where do runtime
    # parameter values come from," since there is no other trigger mechanism
    # today. Applied on top of (never replacing) the pipeline's own static
    # parameters at load time -- see nexolith.dag.validator and
    # nexolith.dag.executor, both of which pass this dict into
    # `load_pipeline`/`run_pipeline` as `parameter_overrides`.
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    # Retry policy (NXL-79) -- per-task, since retrying is inherently about
    # one task's own execution, not the DAG as a whole. Defaults preserve
    # today's exact behavior: retries=0 means exactly one attempt, same as
    # before this field existed. `retries` counts additional attempts
    # beyond the first (retries=2 -> up to 3 attempts total), matching
    # everyday "retries" phrasing rather than "max_attempts".
    retries: int = Field(default=0, ge=0)
    # Fixed delay before each retry; `retry_backoff_multiplier` (default
    # 1.0 = no backoff, a fixed delay every time) optionally scales it up
    # per subsequent retry for simple exponential backoff. Not a general
    # backoff-strategy plugin system -- this is the whole strategy.
    retry_delay_seconds: float = Field(default=0.0, ge=0.0)
    retry_backoff_multiplier: float = Field(default=1.0, ge=1.0)


class DagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    tasks: list[DagTaskConfig] = Field(min_length=1)
    # Failure-propagation policy (NXL-79) -- DAG-level, not per-task: this
    # is about how the whole run responds to an ultimate failure, not any
    # one task's own concern. 'skip' (default) preserves today's exact
    # behavior -- only tasks transitively depending on the failure are
    # skipped, independent branches proceed. 'block' stops the entire run,
    # including independent branches not yet started. No third option:
    # the acceptance criteria asks for documented justification before
    # adding one, and none has been needed yet.
    on_failure: Literal["skip", "block"] = "skip"

    @model_validator(mode="after")
    def validate_task_graph_shape(self) -> "DagConfig":
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.name] = counts.get(task.name, 0) + 1
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate task name(s): {', '.join(duplicates)}")

        known_names = {task.name for task in self.tasks}
        for task in self.tasks:
            if task.name in task.depends_on:
                raise ValueError(f"task '{task.name}' cannot depend on itself")
            unknown = sorted(dep for dep in task.depends_on if dep not in known_names)
            if unknown:
                raise ValueError(
                    f"task '{task.name}' depends_on unknown task(s): {', '.join(unknown)}"
                )
        return self

    @classmethod
    def validate_document(cls, document: Any) -> "DagConfig":
        return cls.model_validate(document)
