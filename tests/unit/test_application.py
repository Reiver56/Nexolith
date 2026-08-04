from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from nexolith.application import (
    ApplicationEvent,
    EventSink,
    ExtractionCompleted,
    ExtractionStarted,
    FailureCategory,
    PipelineApplication,
    PipelineExecutionCompleted,
    PipelineExecutionStarted,
    PipelineFailed,
    PipelineLoaded,
    PipelineLoadStarted,
    PipelineOperation,
    PipelinePhase,
    TransformationsCompleted,
    TransformationsStarted,
    WriteCompleted,
    WriteStarted,
)
from nexolith.config.models import (
    CsvDestinationConfig,
    CsvSourceConfig,
    PipelineConfig,
    SelectConfig,
)
from nexolith.connectors.registry import ConnectorRegistry
from nexolith.events import emit_event
from nexolith.exceptions import ConfigurationError, ConnectorError, ExecutionError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionResult, ExecutionStatus
from nexolith.types import Rows, Scalar


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[ApplicationEvent] = []

    def handle(self, event: ApplicationEvent) -> None:
        self.events.append(event)


class Source:
    def read(self) -> Rows:
        return [{"id": 1}, {"id": 2}]


class BrokenSource:
    def read(self) -> Rows:
        raise ConnectorError("Could not read controlled source")


class UnexpectedBrokenSource:
    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    def read(self) -> Rows:
        raise self.error


class Destination:
    def write(self, rows: Rows) -> int:
        return len(rows)


class FakeRunner:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[PipelineConfig, bool, EventSink | None]] = []

    def run(
        self,
        config: PipelineConfig,
        *,
        raise_on_error: bool = False,
        event_sink: EventSink | None = None,
    ) -> ExecutionResult:
        self.calls.append((config, raise_on_error, event_sink))
        emit_event(event_sink, PipelineExecutionStarted(config.name))
        return self.result


def pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        name="shared_application",
        source=CsvSourceConfig(type="csv", path="unused"),
        transformations=[SelectConfig(type="select", columns=["id"])],
        destination=CsvDestinationConfig(type="csv", path="unused"),
    )


def runner_with_source(
    source: Source | BrokenSource | UnexpectedBrokenSource,
) -> DefaultPipelineRunner:
    connectors = ConnectorRegistry()
    connectors.register_source("csv", lambda _: source)
    connectors.register_destination("csv", lambda _: Destination())
    return DefaultPipelineRunner(connectors=connectors)


def test_application_runs_without_event_sink() -> None:
    config = pipeline_config()
    expected = ExecutionResult(pipeline_name=config.name, status=ExecutionStatus.SUCCEEDED)
    runner = FakeRunner(expected)
    application = PipelineApplication(loader=lambda _path, _overrides=None: config, runner=runner)

    result = application.run_pipeline(Path("pipeline.yaml"))

    assert result is expected
    assert runner.calls == [(config, True, None)]


def test_application_emits_events_in_success_order() -> None:
    config = pipeline_config()
    sink = RecordingSink()
    application = PipelineApplication(
        loader=lambda _path, _overrides=None: config,
        runner=runner_with_source(Source()),
    )

    result = application.run_pipeline(Path("pipeline.yaml"), event_sink=sink)

    assert result.status is ExecutionStatus.SUCCEEDED
    assert [type(event) for event in sink.events] == [
        PipelineLoadStarted,
        PipelineLoaded,
        PipelineExecutionStarted,
        ExtractionStarted,
        ExtractionCompleted,
        TransformationsStarted,
        TransformationsCompleted,
        WriteStarted,
        WriteCompleted,
        PipelineExecutionCompleted,
    ]
    assert sink.events[0] == PipelineLoadStarted(PipelineOperation.RUN)
    assert sink.events[4] == ExtractionCompleted(config.name, rows_read=2)
    assert sink.events[5] == TransformationsStarted(config.name, transformation_count=1)
    assert sink.events[-1] == PipelineExecutionCompleted(config.name, 2, 2)


def test_application_emits_failure_and_preserves_execution_error_chain() -> None:
    config = pipeline_config()
    sink = RecordingSink()
    application = PipelineApplication(
        loader=lambda _path, _overrides=None: config,
        runner=runner_with_source(BrokenSource()),
    )

    with pytest.raises(ExecutionError) as captured:
        application.run_pipeline(Path("pipeline.yaml"), event_sink=sink)

    assert isinstance(captured.value.__cause__, ConnectorError)
    assert sink.events[-1] == PipelineFailed(
        operation=PipelineOperation.RUN,
        pipeline_name=config.name,
        phase=PipelinePhase.EXTRACTION,
        category=FailureCategory.CONNECTOR,
    )
    assert ExtractionCompleted not in {type(event) for event in sink.events}
    assert PipelineExecutionCompleted not in {type(event) for event in sink.events}


def test_application_preserves_configuration_error() -> None:
    original = ConfigurationError("controlled configuration failure")
    sink = RecordingSink()

    def broken_loader(
        _path: Path, _overrides: Mapping[str, Scalar] | None = None
    ) -> PipelineConfig:
        raise original

    application = PipelineApplication(loader=broken_loader)

    with pytest.raises(ConfigurationError) as captured:
        application.validate_pipeline(Path("pipeline.yaml"), event_sink=sink)

    assert captured.value is original
    assert sink.events == [
        PipelineLoadStarted(PipelineOperation.VALIDATE),
        PipelineFailed(
            operation=PipelineOperation.VALIDATE,
            pipeline_name=None,
            phase=PipelinePhase.LOADING,
            category=FailureCategory.CONFIGURATION,
        ),
    ]


def test_application_reports_and_preserves_unexpected_error() -> None:
    config = pipeline_config()
    original = RuntimeError("controlled programming failure")
    sink = RecordingSink()
    application = PipelineApplication(
        loader=lambda _path, _overrides=None: config,
        runner=runner_with_source(UnexpectedBrokenSource(original)),
    )

    with pytest.raises(RuntimeError) as captured:
        application.run_pipeline(Path("pipeline.yaml"), event_sink=sink)

    assert captured.value is original
    assert sink.events[-1] == PipelineFailed(
        operation=PipelineOperation.RUN,
        pipeline_name=config.name,
        phase=PipelinePhase.EXTRACTION,
        category=FailureCategory.UNEXPECTED,
    )


def test_application_import_does_not_load_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import nexolith.application; "
            "assert not any(name.startswith('nexolith.cli') for name in sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, "application import unexpectedly loaded the CLI"
