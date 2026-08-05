from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexolith.types import Scalar


class DagTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    pipeline: str | None = Field(default=None, min_length=1)
    # script (NXL-88, ADR-7, Model B): an alternative to `pipeline` -- a
    # self-contained script that manages its own I/O entirely (does not
    # receive/return Nexolith's in-flight Rows, unlike Model A's
    # `python_job` transform step). Mutually exclusive with `pipeline`, same
    # pattern as story 1's `query`/`query_file`. Run as a subprocess (see
    # nexolith.jobs.script_runner for why) via `interpreter` (defaults to
    # the interpreter running Nexolith itself) with the documented
    # entrypoint contract `def <entrypoint>(context) -> None`, `context`
    # exposing only `.parameters` (a plain object, not an importable
    # Nexolith type -- the subprocess may not have nexolith installed at
    # all, e.g. a dedicated PySpark venv).
    script: str | None = Field(default=None, min_length=1)
    entrypoint: str = "run"
    interpreter: str | None = None
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

    @model_validator(mode="after")
    def require_pipeline_or_script(self) -> "DagTaskConfig":
        if self.pipeline and self.script:
            raise ValueError("'pipeline' and 'script' are mutually exclusive; specify only one")
        if not self.pipeline and not self.script:
            raise ValueError("either 'pipeline' or 'script' is required")
        return self


class DagTriggerConfig(BaseModel):
    """trigger (NXL-85): cross-DAG triggers -- a DAG's completion starts
    another DAG, alongside or instead of interval scheduling. Only
    `on_success_of` exists: the acceptance criteria asks for
    success-triggers-downstream, the minimal set, not `on_failure_of`/
    `on_completion_of` -- not built speculatively. A list, since a DAG can
    have more than one upstream trigger source, each independently capable
    of triggering it (the scheduler evaluates each on its own -- see
    nexolith.scheduler.daemon).
    """

    model_config = ConfigDict(extra="forbid")
    on_success_of: list[str] = Field(min_length=1)


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
    # schedule (NXL-90): an interval string in the same format the
    # scheduler daemon's own parser (nexolith.scheduler.interval,
    # v0.3.2 story 4) already accepts, e.g. "5m". Format is validated with
    # that exact parser at DAG-validation time (nexolith.dag.validator --
    # not here, to avoid a nexolith.dag <-> nexolith.scheduler import cycle;
    # see that module for why). `None` (the default) preserves today's
    # behavior for any DAG that doesn't set it, cross-DAG-only DAGs (NXL-85)
    # included. Populated into `dags.schedule` on a DAG's first registration
    # only (nexolith.dag.executor.DagExecutor.run) -- editing `schedule:` in
    # the file after that first run does not retroactively change an
    # already-registered DAG's stored schedule, the same "never clobber"
    # behavior that already applied before this field existed.
    schedule: str | None = None
    # trigger (NXL-85): declared on the downstream DAG, naming upstream
    # DAG(s) by name -- not validated for existence here (a DAG file can't
    # know what other DAGs the system knows about; an unknown/never-run
    # upstream name is simply never satisfied, a benign no-op, not an
    # error). Read fresh from this file on every scheduler poll tick
    # (nexolith.scheduler.daemon._cross_dag_trigger_reactions), not
    # persisted as its own config in the state store, unlike `schedule`
    # (above).
    trigger: DagTriggerConfig | None = None
    # priority (NXL-86): resolves contention when multiple DAGs become due
    # in the same poll tick -- higher priority runs first; it says nothing
    # about how serious a failure is (that's severity, a separate later
    # story, deliberately not conflated with this one even though both will
    # live on DagConfig). An ordered Literal[str], not a Python enum.Enum:
    # matches this exact model's own `on_failure` field precedent (a small,
    # closed, YAML-declared set of options) rather than the runtime status
    # enums in nexolith.state.models, a different category of value (DB rows
    # with associated methods/comparisons, not a one-shot config choice).
    # The rank ordering itself lives in nexolith.scheduler.daemon, since
    # it's a scheduling-order concern, not a general config one. Default
    # "normal": with every due DAG at the same priority, sorting by
    # (priority, name) collapses to sorting by name alone -- today's exact
    # ordering (nexolith.state.store.StateStore.list_dags() already orders
    # by name), so a DAG that never sets this is completely unaffected.
    # Read fresh from the file every tick, the same as `trigger` and for
    # the same reason -- never persisted to the store.
    priority: Literal["low", "normal", "high", "critical"] = "normal"

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

        if self.trigger is not None and self.name in self.trigger.on_success_of:
            raise ValueError(f"DAG '{self.name}' cannot list itself as its own trigger source")
        return self

    @classmethod
    def validate_document(cls, document: Any) -> "DagConfig":
        return cls.model_validate(document)
