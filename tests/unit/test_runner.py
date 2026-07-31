import pytest

from nexolith.config.models import CsvDestinationConfig, CsvSourceConfig, PipelineConfig
from nexolith.connectors.registry import ConnectorRegistry
from nexolith.exceptions import ConnectorError, ExecutionError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus
from nexolith.types import Rows


class BrokenSource:
    def read(self) -> Rows:
        raise RuntimeError("controlled failure")


class ExpectedBrokenSource:
    def read(self) -> Rows:
        try:
            raise OSError("low-level failure")
        except OSError as exc:
            raise ConnectorError("Could not read controlled source") from exc


class Sink:
    def write(self, rows: Rows) -> int:
        return len(rows)


def test_runner_does_not_hide_unexpected_errors() -> None:
    registry = ConnectorRegistry()
    registry.register_source("csv", lambda _: BrokenSource())
    registry.register_destination("csv", lambda _: Sink())
    config = PipelineConfig(
        name="broken",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
    )
    with pytest.raises(RuntimeError, match="controlled failure"):
        DefaultPipelineRunner(connectors=registry).run(config)


def test_runner_returns_failed_result_for_expected_errors() -> None:
    registry = ConnectorRegistry()
    registry.register_source("csv", lambda _: ExpectedBrokenSource())
    registry.register_destination("csv", lambda _: Sink())
    config = PipelineConfig(
        name="broken",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
    )
    result = DefaultPipelineRunner(connectors=registry).run(config)
    assert result.status is ExecutionStatus.FAILED
    assert result.error == "Pipeline 'broken' failed: Could not read controlled source"
    assert result.finished_at is not None


def test_runner_preserves_expected_error_chain() -> None:
    registry = ConnectorRegistry()
    registry.register_source("csv", lambda _: ExpectedBrokenSource())
    registry.register_destination("csv", lambda _: Sink())
    config = PipelineConfig(
        name="broken",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
    )

    with pytest.raises(ExecutionError) as captured:
        DefaultPipelineRunner(connectors=registry).run(config, raise_on_error=True)

    assert isinstance(captured.value.__cause__, ConnectorError)
    assert isinstance(captured.value.__cause__.__cause__, OSError)


def test_runner_success_status(tmp_path: object) -> None:
    registry = ConnectorRegistry()
    registry.register_source(
        "csv", lambda _: type("Source", (), {"read": lambda self: [{"id": 1}]})()
    )
    registry.register_destination("csv", lambda _: Sink())
    config = PipelineConfig(
        name="ok",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
    )
    result = DefaultPipelineRunner(connectors=registry).run(config)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_read == result.rows_written == 1
    assert result.duration_seconds is not None
