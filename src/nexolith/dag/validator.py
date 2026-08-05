from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nexolith.config import load_pipeline
from nexolith.dag.models import DagConfig, DagTaskConfig
from nexolith.exceptions import ConfigurationError


def _detect_cycle(tasks: list[DagTaskConfig]) -> list[str] | None:
    """DFS with a three-state (unvisited / visiting / done) marker per task.
    Returns the cycle as an ordered list of task names (first name repeated
    at the end to show the closed loop), or None if the graph is acyclic.
    Stops at the first cycle found -- one clear, actionable report is what
    validation needs, not an exhaustive list of every cycle in the graph.
    """
    by_name = {task.name: task for task in tasks}
    state: dict[str, str] = {}
    path: list[str] = []

    def visit(name: str) -> list[str] | None:
        state[name] = "visiting"
        path.append(name)
        for dependency in by_name[name].depends_on:
            if state.get(dependency) == "visiting":
                cycle_start = path.index(dependency)
                return [*path[cycle_start:], dependency]
            if state.get(dependency) != "done":
                found = visit(dependency)
                if found is not None:
                    return found
        path.pop()
        state[name] = "done"
        return None

    for task in tasks:
        if state.get(task.name) != "done":
            found = visit(task.name)
            if found is not None:
                return found
    return None


def read_dag_config(path: Path) -> DagConfig:
    """Parse and structurally validate a DAG file -- YAML syntax, DAG-level
    structure (non-empty, unique task names, no self-references, no
    dangling `depends_on` targets, no self-triggering `trigger:`), all via
    `DagConfig`'s own Pydantic validation -- WITHOUT `load_dag()`'s heavier
    checks (cycle detection, and validating every referenced pipeline/
    script, which can import a `python_job` file). Used by
    `nexolith.scheduler.daemon` to cheaply read a DAG's own `trigger:`
    declaration on every poll tick (NXL-85): a full `load_dag()` every few
    seconds for every registered DAG would repeat real, sometimes-expensive
    work (and side effects) that only actually matter once, at `nexolith
    validate`/`run` time.
    """
    if not path.is_file():
        raise ConfigurationError(f"DAG file not found: {path}. Check the path and try again.")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"Could not read DAG file: {path}. Check file permissions."
        ) from exc
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigurationError(f"Invalid YAML in {path}{location}.") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("DAG YAML must contain a mapping at its root")
    dag = _validate_structure(document)
    _validate_schedule(dag)
    return dag


def load_dag(path: Path) -> DagConfig:
    """Parse and fully validate a DAG file: everything `read_dag_config()`
    already checks, plus cycle detection and -- for every referenced
    pipeline -- that the file exists and is individually valid. That last
    check reuses `nexolith.config.load_pipeline` (the same function
    `PipelineApplication.validate_pipeline()` calls) rather than
    reimplementing pipeline validation; a referenced pipeline's own
    `ConfigurationError` is preserved as the cause and folded into a
    DAG-level message naming the task and file it came from.

    `pipeline:` paths are resolved relative to the DAG file's own directory
    (unless already absolute), so a DAG file and the pipelines it
    references can be moved together as a unit, independent of the
    caller's working directory.
    """
    dag = read_dag_config(path)
    _check_for_cycles(dag)
    _validate_referenced_pipelines(dag, path.parent)
    return dag


def _validate_structure(document: dict[str, Any]) -> DagConfig:
    try:
        return DagConfig.validate_document(document)
    except ValidationError as exc:
        errors = []
        for error in exc.errors(include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            prefix = f"{location}: " if location else ""
            errors.append(f"{prefix}{error['msg']}")
        raise ConfigurationError("Invalid DAG configuration:\n" + "\n".join(errors)) from exc


def _validate_schedule(dag: DagConfig) -> None:
    """Validate `schedule:` (NXL-90) with the scheduler daemon's own real
    interval parser -- not a second implementation of the same format --
    so a malformed value is a clear `nexolith validate`/`load_dag()` error,
    never a runtime scheduler failure discovered only once a poll tick
    actually tries to parse it.

    The import is deliberately deferred to call time, not this module's
    top level: `nexolith.scheduler.daemon` already imports
    `nexolith.dag.executor`/`nexolith.dag.validator` at its own module
    level (to call `execute_dag()`/`read_dag_config()`), so an eager
    top-level `from nexolith.scheduler.interval import parse_interval`
    here would make `nexolith.dag` and `nexolith.scheduler` import each
    other -- a real circular import, not a hypothetical one (confirmed by
    tracing the actual chain, not assumed). Deferring until this function
    actually runs sidesteps it: by then both packages have long finished
    their own top-level imports.
    """
    if dag.schedule is None:
        return
    from nexolith.scheduler.interval import parse_interval

    try:
        parse_interval(dag.schedule)
    except ConfigurationError as exc:
        raise ConfigurationError(f"Invalid DAG schedule: {exc}") from exc


def _check_for_cycles(dag: DagConfig) -> None:
    cycle = _detect_cycle(dag.tasks)
    if cycle is not None:
        raise ConfigurationError("Cycle detected in DAG '" + dag.name + "': " + " -> ".join(cycle))


def _validate_referenced_pipelines(dag: DagConfig, base_dir: Path) -> None:
    for task in dag.tasks:
        if task.script is not None:
            _validate_referenced_script(task, base_dir)
            continue
        assert task.pipeline is not None  # guaranteed by DagTaskConfig's own validator
        pipeline_path = Path(task.pipeline)
        if not pipeline_path.is_absolute():
            pipeline_path = base_dir / pipeline_path
        try:
            load_pipeline(pipeline_path, parameter_overrides=task.parameters)
        except ConfigurationError as exc:
            raise ConfigurationError(
                f"Task '{task.name}' references an invalid pipeline ({task.pipeline}): {exc}"
            ) from exc


def _validate_referenced_script(task: DagTaskConfig, base_dir: Path) -> None:
    """Model B (NXL-88): confirm only that the script file exists and is
    readable -- deliberately NOT importing it to check the entrypoint, the
    way Model A's `python_job` does. Importing here would run the script's
    own top-level code inside Nexolith's own interpreter during a nominally
    side-effect-free `validate`, and would fail for entirely expected
    reasons (e.g. `import pyspark` when Nexolith's own venv doesn't have
    it) -- precisely the coupling subprocess isolation exists to avoid. A
    missing/misnamed entrypoint is instead a real execution-time failure
    (a non-zero exit from the configured interpreter), not a validate-time
    one.
    """
    assert task.script is not None
    script_path = Path(task.script)
    if not script_path.is_absolute():
        script_path = base_dir / script_path
    if not script_path.is_file():
        raise ConfigurationError(
            f"Task '{task.name}' references a script that does not exist: {script_path}"
        )
