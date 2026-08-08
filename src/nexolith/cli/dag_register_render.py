"""Shared rendering for `nexolith dag register` and the full-screen
session's `/register` (NXL-103) -- one function so both surfaces report an
identical outcome, the same convention already established for DAG runs
(`runs_render.py`) and scheduler status (`scheduler_render.py`).
"""

from nexolith.dag import DagRegistrationResult

_FORCE_HINT = "Use `nexolith dag register <path> --force` to update it from the file."


def render_dag_registration(result: DagRegistrationResult) -> str:
    schedule = result.record.schedule or "none"
    enabled = "enabled" if result.record.enabled else "disabled"

    if result.created:
        return f"DAG '{result.dag.name}' registered (schedule: {schedule}, {enabled})."
    if result.updated:
        return f"DAG '{result.dag.name}' registration updated (schedule: {schedule}, {enabled})."
    return (
        f"DAG '{result.dag.name}' is already registered "
        f"(schedule: {schedule}, {enabled}). {_FORCE_HINT}"
    )
