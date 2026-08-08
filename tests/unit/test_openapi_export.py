from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parents[2]
_EXPORTER = _ROOT / "scripts" / "export_openapi.py"


def _export() -> bytes:
    result = subprocess.run(
        [sys.executable, str(_EXPORTER)],
        cwd=_ROOT,
        check=True,
        capture_output=True,
    )
    assert result.stderr == b""
    return result.stdout


def test_openapi_export_is_deterministic_local_and_complete() -> None:
    first = _export()
    second = _export()

    assert first == second
    schema: dict[str, Any] = json.loads(first)
    assert schema["paths"]["/api/v1/dags"]["get"]["operationId"] == "list_dags"
    assert schema["paths"]["/api/v1/dags/{dag_name}/graph"]["get"]["operationId"] == "get_dag_graph"
    assert schema["paths"]["/api/v1/task-details"]["get"]["operationId"] == "get_dag_task_source"
    assert schema["paths"]["/api/v1/runs/{run_id}"]["get"]["operationId"] == "get_run"
    assert b"C:\\\\Users" not in first
    assert b"/home/" not in first
