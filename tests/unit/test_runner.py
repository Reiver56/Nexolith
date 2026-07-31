from nexolith.config.models import CsvDestinationConfig, CsvSourceConfig, PipelineConfig
from nexolith.connectors.registry import ConnectorRegistry
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus
from nexolith.types import Rows


class BrokenSource:
    def read(self) -> Rows:
        raise RuntimeError("controlled failure")


class Sink:
    def write(self, rows: Rows) -> int:
        return len(rows)


def test_runner_handles_errors() -> None:
    registry = ConnectorRegistry()
    registry.register_source("csv", lambda _: BrokenSource())
    registry.register_destination("csv", lambda _: Sink())
    config = PipelineConfig(
        name="broken",
        source=CsvSourceConfig(type="csv", path="unused"),
        destination=CsvDestinationConfig(type="csv", path="unused"),
    )
    result = DefaultPipelineRunner(connectors=registry).run(config)
    assert result.status is ExecutionStatus.FAILED
    assert result.error == "controlled failure"
    assert result.finished_at is not None


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
