import json
from pathlib import Path

from nexolith.nexoactions import (
    NexoActionContext,
    NexoActionDefinition,
    NexoActionParameter,
    NexoActionParameterKind,
    NexoActionResult,
)
from nexolith.types import Rows


def record_alert(rows: Rows, context: NexoActionContext) -> NexoActionResult:
    output = Path(__file__).resolve().parent.parent / str(context.parameters["output_path"])
    existing_keys: set[str] = set()
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            key = record.get("idempotency_key")
            if isinstance(key, str):
                existing_keys.add(key)
    if context.idempotency_key in existing_keys:
        return NexoActionResult()

    record = {
        "action": context.action_identifier,
        "idempotency_key": context.idempotency_key,
        "matched_rows": len(rows),
    }
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return NexoActionResult()


NEXO_ACTION = NexoActionDefinition(
    name="record_sensor_alert",
    execute=record_alert,
    parameters=(
        NexoActionParameter(
            "output_path",
            NexoActionParameterKind.STRING,
        ),
    ),
)
