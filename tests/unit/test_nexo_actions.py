from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.application import (
    ActionCompleted,
    ActionFailed,
    ActionInvocationStarted,
    ActionNotMatched,
    ApplicationEvent,
    PipelineExecutionCompleted,
    WriteCompleted,
)
from nexolith.cli.app import app
from nexolith.config import load_pipeline
from nexolith.config.models import (
    CsvDestinationConfig,
    CsvSourceConfig,
    NexoActionConditionConfig,
    NexoActionConfig,
    NexoActionIdempotencyConfig,
    PipelineConfig,
)
from nexolith.connectors.registry import ConnectorRegistry
from nexolith.dag import DagExecutor, load_dag
from nexolith.exceptions import ConfigurationError, ExecutionError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus
from nexolith.nexoactions import (
    NexoActionDefinition,
    NexoActionExecutionResult,
    NexoActionExecutionStatus,
    NexoActionResult,
)
from nexolith.nexoactions.execution import derive_idempotency_key, execute_actions
from nexolith.nexoactions.loader import discover_local_nexo_actions, load_nexo_action_registry
from nexolith.nexoactions.registry import builtin_nexo_action_registry
from nexolith.state import DagRunStatus, StateStore, TaskAttemptStatus
from nexolith.types import Rows


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[ApplicationEvent] = []

    def handle(self, event: ApplicationEvent) -> None:
        self.events.append(event)


def _action_config(
    handler: object,
    *,
    operator: str = "equals",
    value: object = "alert",
    match: str = "any",
    idempotency_fields: list[str] | None = None,
) -> NexoActionConfig:
    definition = NexoActionDefinition("record_alert", handler)  # type: ignore[arg-type]
    config = NexoActionConfig(
        type="nexoaction.record_alert",
        condition=NexoActionConditionConfig(field="status", operator=operator, value=value),
        match=match,
        idempotency=NexoActionIdempotencyConfig(fields=idempotency_fields or ["id"]),
    )
    config.bind_action(definition)
    return config


def _assert_sensitive_absent(text: str, values: list[str]) -> None:
    if any(value in text for value in values):
        raise AssertionError("Sensitive Nexo Action information was exposed")


def _write_local_action(
    directory: Path,
    filename: str,
    *,
    name: str,
    body: str = "return NexoActionResult()",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        textwrap.dedent(
            f"""
            from nexolith.nexoactions import NexoActionDefinition, NexoActionResult

            def run(rows, context):
                {body}

            NEXO_ACTION = NexoActionDefinition(name={name!r}, execute=run)
            """
        ),
        encoding="utf-8",
    )
    return path


def test_action_definition_and_empty_builtin_registry_are_deterministic() -> None:
    definition = NexoActionDefinition("record_alert", lambda _rows, _context: NexoActionResult())
    registry = builtin_nexo_action_registry()
    registry.register(definition)

    assert definition.identifier == "nexoaction.record_alert"
    assert registry.names == ("record_alert",)
    assert registry.lookup(definition.identifier) is definition
    assert builtin_nexo_action_registry().names == ()


@pytest.mark.parametrize("name", ["", "Bad", "bad-name", "1bad"])
def test_action_definition_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="invalid action name"):
        NexoActionDefinition(name, lambda _rows, _context: NexoActionResult())


def test_unknown_action_uses_typed_configuration_error_with_chain() -> None:
    with pytest.raises(ConfigurationError, match="Unknown Nexo Action") as captured:
        builtin_nexo_action_registry().lookup("nexoaction.missing")
    assert isinstance(captured.value.__cause__, KeyError)


def test_local_discovery_is_sorted_and_cwd_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _write_local_action(project / "nexoactions", "z.py", name="z_last")
    _write_local_action(project / "nexoactions", "a.py", name="a_first")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    definitions = discover_local_nexo_actions(project)

    assert tuple(definition.name for definition in definitions) == ("a_first", "z_last")
    assert load_nexo_action_registry(project).lookup("nexoaction.a_first").name == "a_first"


def test_duplicate_local_actions_fail_clearly(tmp_path: Path) -> None:
    local = tmp_path / "nexoactions"
    _write_local_action(local, "a.py", name="duplicate")
    _write_local_action(local, "z.py", name="duplicate")

    with pytest.raises(ConfigurationError, match="Duplicate local Nexo Action.*a.py.*z.py"):
        discover_local_nexo_actions(tmp_path)


def test_malformed_local_export_fails_without_exposing_project_path(tmp_path: Path) -> None:
    project = tmp_path / "sensitive-project"
    local = project / "nexoactions"
    local.mkdir(parents=True)
    (local / "malformed.py").write_text("NEXO_ACTION = object()\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="must export one NEXO_ACTION") as captured:
        discover_local_nexo_actions(project)
    _assert_sensitive_absent(str(captured.value), [str(project.resolve())])


def test_local_import_failure_is_chained_and_redacted(tmp_path: Path) -> None:
    project = tmp_path / "sensitive-project"
    local = project / "nexoactions"
    local.mkdir(parents=True)
    secret = "recognizable_import_secret"
    (local / "broken.py").write_text(f"raise RuntimeError({secret!r})\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="broken.py.*RuntimeError") as captured:
        discover_local_nexo_actions(project)

    assert isinstance(captured.value.__cause__, RuntimeError)
    diagnostic = str(captured.value)
    _assert_sensitive_absent(diagnostic, [secret, str(project.resolve())])


def test_local_discovery_rejects_symlinked_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    link = project / "nexoactions"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ConfigurationError, match="must be a real directory"):
        discover_local_nexo_actions(project)


def test_local_discovery_rejects_symlinked_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    local = project / "nexoactions"
    local.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    _write_local_action(tmp_path, "outside.py", name="outside")
    link = local / "escaped.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    with pytest.raises(ConfigurationError, match="must stay inside"):
        discover_local_nexo_actions(project)


@pytest.mark.parametrize(
    ("operator", "value", "expected_ids"),
    [
        ("equals", 2, [2]),
        ("not_equals", 2, [3, 1]),
        ("less_than", 2, [1]),
        ("less_than_or_equal", 2, [1, 2]),
        ("greater_than", 2, [3]),
        ("greater_than_or_equal", 2, [3, 2]),
    ],
)
def test_condition_operators_receive_only_matched_rows(
    operator: str, value: object, expected_ids: list[int]
) -> None:
    received: list[list[int]] = []

    def handler(rows: Rows, _context: object) -> NexoActionResult:
        received.append([int(row["id"]) for row in rows])
        return NexoActionResult()

    action = _action_config(handler, operator=operator, value=value)
    action.condition.field = "id"

    outcomes: list[NexoActionExecutionResult] = []
    execute_actions(
        [action],
        [{"id": 3}, {"id": 1}, {"id": 2}],
        pipeline_name="orders",
        event_sink=None,
        outcomes=outcomes,
    )

    assert received == [expected_ids]
    assert outcomes[0].matched_rows == len(expected_ids)


def test_null_equality_is_explicitly_supported() -> None:
    received: list[Rows] = []

    def handler(rows: Rows, _context: object) -> NexoActionResult:
        received.append(rows)
        return NexoActionResult()

    action = _action_config(handler, value=None)
    execute_actions(
        [action],
        [{"id": 1, "status": None}, {"id": 2, "status": "ready"}],
        pipeline_name="orders",
        event_sink=None,
        outcomes=[],
    )
    assert received == [[{"id": 1, "status": None}]]


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": 1}],
        [{"id": 1, "status": 1}],
        [{"id": 1, "status": "alert"}, {"id": 2}],
    ],
)
def test_condition_validation_failure_never_calls_handler(rows: Rows) -> None:
    calls = 0

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        nonlocal calls
        calls += 1
        return NexoActionResult()

    action = _action_config(handler)
    with pytest.raises(ExecutionError):
        execute_actions([action], rows, pipeline_name="orders", event_sink=None, outcomes=[])
    assert calls == 0


def test_unsupported_condition_material_never_calls_handler() -> None:
    calls = 0

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        nonlocal calls
        calls += 1
        return NexoActionResult()

    rows: Rows = [{"id": 1, "status": "alert"}]
    rows[0]["status"] = ["unsupported"]  # type: ignore[assignment]
    with pytest.raises(ExecutionError, match="incompatible type"):
        execute_actions(
            [_action_config(handler)],
            rows,
            pipeline_name="orders",
            event_sink=None,
            outcomes=[],
        )
    assert calls == 0


def test_all_actions_are_preflighted_before_any_handler_runs() -> None:
    calls = 0

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        nonlocal calls
        calls += 1
        return NexoActionResult()

    valid = _action_config(handler)
    invalid = NexoActionConfig(
        type="nexoaction.second_alert",
        condition=NexoActionConditionConfig(field="missing", operator="equals", value="alert"),
        idempotency=NexoActionIdempotencyConfig(fields=["id"]),
    )
    invalid.bind_action(NexoActionDefinition("second_alert", handler))

    with pytest.raises(ExecutionError, match="condition field"):
        execute_actions(
            [valid, invalid],
            [{"id": 1, "status": "alert"}],
            pipeline_name="orders",
            event_sink=None,
            outcomes=[],
        )

    assert calls == 0


def test_any_and_all_match_modes_are_distinct() -> None:
    calls: list[str] = []

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        calls.append("called")
        return NexoActionResult()

    rows = [{"id": 1, "status": "alert"}, {"id": 2, "status": "ok"}]
    any_action = _action_config(handler, match="any")
    all_action = _action_config(handler, match="all")
    any_outcomes: list[NexoActionExecutionResult] = []
    all_outcomes: list[NexoActionExecutionResult] = []

    execute_actions(
        [any_action], rows, pipeline_name="orders", event_sink=None, outcomes=any_outcomes
    )
    execute_actions(
        [all_action], rows, pipeline_name="orders", event_sink=None, outcomes=all_outcomes
    )

    assert calls == ["called"]
    assert any_outcomes[0].status is NexoActionExecutionStatus.COMPLETED
    assert all_outcomes[0].status is NexoActionExecutionStatus.NOT_MATCHED


def test_all_mode_invokes_with_every_matched_row_in_pipeline_order() -> None:
    received: list[Rows] = []

    def handler(rows: Rows, _context: object) -> NexoActionResult:
        received.append(rows)
        return NexoActionResult()

    rows = [{"id": 2, "status": "alert"}, {"id": 1, "status": "alert"}]
    outcomes: list[NexoActionExecutionResult] = []
    execute_actions(
        [_action_config(handler, match="all")],
        rows,
        pipeline_name="orders",
        event_sink=None,
        outcomes=outcomes,
    )
    assert received == [rows]
    assert outcomes[0].status is NexoActionExecutionStatus.COMPLETED


def test_no_match_emits_honest_result_and_event() -> None:
    action = _action_config(lambda _rows, _context: NexoActionResult())
    sink = RecordingSink()
    outcomes: list[NexoActionExecutionResult] = []

    execute_actions(
        [action],
        [{"id": 1, "status": "ok"}],
        pipeline_name="orders",
        event_sink=sink,
        outcomes=outcomes,
    )

    assert outcomes == [
        NexoActionExecutionResult(
            "nexoaction.record_alert", NexoActionExecutionStatus.NOT_MATCHED, 0
        )
    ]
    assert sink.events == [ActionNotMatched("nexoaction.record_alert", 0)]


def test_idempotency_key_is_stable_order_independent_and_opaque() -> None:
    secret = "raw-secret-value"
    rows = [{"id": 2, "token": secret}, {"token": "other", "id": 1}]
    first = derive_idempotency_key("orders", "nexoaction.notify", rows, ("token", "id"))
    second = derive_idempotency_key(
        "orders", "nexoaction.notify", list(reversed(rows)), ("id", "token")
    )
    changed = derive_idempotency_key(
        "orders", "nexoaction.notify", [{"id": 3, "token": secret}], ("id", "token")
    )

    assert first == second
    assert first != changed
    assert len(first) == 64
    _assert_sensitive_absent(first, [secret])


def test_missing_idempotency_field_never_invokes_handler() -> None:
    calls = 0

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        nonlocal calls
        calls += 1
        return NexoActionResult()

    action = _action_config(handler, idempotency_fields=["missing"])
    with pytest.raises(ExecutionError, match="idempotency field"):
        execute_actions(
            [action],
            [{"id": 1, "status": "alert"}],
            pipeline_name="orders",
            event_sink=None,
            outcomes=[],
        )
    assert calls == 0


def test_unsupported_idempotency_material_never_invokes_handler() -> None:
    calls = 0

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        nonlocal calls
        calls += 1
        return NexoActionResult()

    rows: Rows = [{"id": 1, "status": "alert"}]
    rows[0]["id"] = {"nested": "unsupported"}  # type: ignore[assignment]
    with pytest.raises(ExecutionError, match="unsupported type"):
        execute_actions(
            [_action_config(handler)], rows, pipeline_name="orders", event_sink=None, outcomes=[]
        )
    assert calls == 0


def test_handler_failure_is_redacted_chained_and_emits_failure() -> None:
    secret = "raw-handler-secret"

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        raise RuntimeError(secret)

    action = _action_config(handler)
    sink = RecordingSink()
    outcomes: list[NexoActionExecutionResult] = []

    with pytest.raises(ExecutionError, match="failed with RuntimeError") as captured:
        execute_actions(
            [action],
            [{"id": 1, "status": "alert"}],
            pipeline_name="orders",
            event_sink=sink,
            outcomes=outcomes,
        )

    assert isinstance(captured.value.__cause__, RuntimeError)
    _assert_sensitive_absent(str(captured.value), [secret])
    assert [type(event) for event in sink.events] == [ActionInvocationStarted, ActionFailed]
    assert outcomes[0].status is NexoActionExecutionStatus.FAILED


def test_process_interruptions_are_not_converted_or_reported_failed() -> None:
    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        raise KeyboardInterrupt

    sink = RecordingSink()
    with pytest.raises(KeyboardInterrupt):
        execute_actions(
            [_action_config(handler)],
            [{"id": 1, "status": "alert"}],
            pipeline_name="orders",
            event_sink=sink,
            outcomes=[],
        )
    assert [type(event) for event in sink.events] == [ActionInvocationStarted]


class Source:
    def read(self) -> Rows:
        return [{"id": 1, "status": "alert"}]


class Destination:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def write(self, rows: Rows) -> int:
        self.order.append("write")
        return len(rows)


def test_pipeline_invokes_action_after_write_without_counting_it_as_rows() -> None:
    order: list[str] = []

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        order.append("action")
        return NexoActionResult()

    config = PipelineConfig(
        name="orders",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
        actions=[_action_config(handler)],
    )
    connectors = ConnectorRegistry()
    connectors.register_source("csv", lambda _config: Source())
    connectors.register_destination("csv", lambda _config: Destination(order))
    sink = RecordingSink()

    result = DefaultPipelineRunner(connectors=connectors).run(config, event_sink=sink)

    assert order == ["write", "action"]
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_written == 1
    assert result.actions[0].status is NexoActionExecutionStatus.COMPLETED
    event_types = [type(event) for event in sink.events]
    assert event_types.index(WriteCompleted) < event_types.index(ActionInvocationStarted)
    assert event_types[-2:] == [ActionCompleted, PipelineExecutionCompleted]


def test_action_failure_happens_after_write_and_fails_pipeline() -> None:
    order: list[str] = []

    def handler(_rows: Rows, _context: object) -> NexoActionResult:
        order.append("action")
        raise RuntimeError("controlled")

    config = PipelineConfig(
        name="orders",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
        actions=[_action_config(handler)],
    )
    connectors = ConnectorRegistry()
    connectors.register_source("csv", lambda _config: Source())
    connectors.register_destination("csv", lambda _config: Destination(order))

    result = DefaultPipelineRunner(connectors=connectors).run(config)

    assert order == ["write", "action"]
    assert result.status is ExecutionStatus.FAILED
    assert result.rows_written == 1
    assert result.actions[0].status is NexoActionExecutionStatus.FAILED


def test_dag_retry_reuses_same_key_and_persists_honest_attempts(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,alert\n", encoding="utf-8")
    local = tmp_path / "nexoactions"
    local.mkdir()
    log = tmp_path / "keys.txt"
    (local / "flaky.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            from nexolith.nexoactions import NexoActionDefinition, NexoActionResult

            def run(rows, context):
                log = Path({str(log)!r})
                existing = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(context.idempotency_key + "\\n")
                if not existing:
                    raise RuntimeError("first attempt")
                return NexoActionResult()

            NEXO_ACTION = NexoActionDefinition("flaky", run)
            """
        ),
        encoding="utf-8",
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """name: retry_action
source:
  type: csv
  path: input.csv
destination:
  type: csv
  path: output.csv
actions:
  - type: nexoaction.flaky
    condition: {field: status, operator: equals, value: alert}
    idempotency: {fields: [id]}
""",
        encoding="utf-8",
    )
    dag_path = tmp_path / "dag.yaml"
    dag_path.write_text(
        """name: retry-action
tasks:
  - name: action
    pipeline: pipeline.yaml
    depends_on: []
    retries: 1
""",
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state.db")
    try:
        executor = DagExecutor(store, sleep=lambda _seconds: None)
        run_id = executor.run(load_dag(dag_path), dag_path)
        run = store.get_dag_run(run_id)
        attempts = store.list_task_attempts(run_id, "action")
    finally:
        store.close()

    keys = log.read_text(encoding="utf-8").splitlines()
    assert keys[0] == keys[1]
    assert run is not None and run.status is DagRunStatus.SUCCEEDED
    assert [attempt.status for attempt in attempts] == [
        TaskAttemptStatus.FAILED,
        TaskAttemptStatus.SUCCEEDED,
    ]


def test_action_configuration_resolves_local_definition_and_parameters(tmp_path: Path) -> None:
    (tmp_path / "input.csv").write_text("id,status\n1,alert\n", encoding="utf-8")
    local = tmp_path / "nexoactions"
    local.mkdir()
    (local / "parameterized.py").write_text(
        textwrap.dedent(
            """
            from nexolith.nexoactions import (
                NexoActionDefinition, NexoActionParameter,
                NexoActionParameterKind, NexoActionResult,
            )
            def run(rows, context):
                return NexoActionResult()
            NEXO_ACTION = NexoActionDefinition(
                "parameterized", run,
                (NexoActionParameter("label", NexoActionParameterKind.STRING),),
            )
            """
        ),
        encoding="utf-8",
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """name: configured
source: {type: csv, path: input.csv}
destination: {type: csv, path: output.csv}
actions:
  - type: nexoaction.parameterized
    condition: {field: status, operator: equals, value: alert}
    idempotency: {fields: [id]}
    parameters: {label: safe}
""",
        encoding="utf-8",
    )

    config = load_pipeline(pipeline)

    assert config.actions[0].resolved_action is not None
    assert config.actions[0].parameters == {"label": "safe"}


def test_cli_action_failure_has_expected_exit_and_no_sensitive_traceback(tmp_path: Path) -> None:
    (tmp_path / "input.csv").write_text("id,status\n1,alert\n", encoding="utf-8")
    secret = "recognizable-handler-secret https://credentials.invalid/token payload=raw"
    _write_local_action(
        tmp_path / "nexoactions",
        "broken.py",
        name="broken",
        body=f"raise RuntimeError({secret!r})",
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """name: broken_action
source: {type: csv, path: input.csv}
destination: {type: csv, path: output.csv}
actions:
  - type: nexoaction.broken
    condition: {field: status, operator: equals, value: alert}
    idempotency: {fields: [id]}
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["run", str(pipeline)])

    assert result.exit_code == 3
    assert "Nexo Action 'nexoaction.broken' failed with RuntimeError" in result.output
    _assert_sensitive_absent(
        result.output,
        [secret, str(tmp_path.resolve()), "Traceback"],
    )
