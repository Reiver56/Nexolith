import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from typer.testing import CliRunner

from nexolith.cli import app
from nexolith.config import load_pipeline
from nexolith.config.models import (
    CsvSourceConfig,
    PipelineConfig,
    SqlDestinationConfig,
)
from nexolith.connectors.sql import SqlDestination, SqlSource
from nexolith.exceptions import ConnectorError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus
from nexolith.types import Rows

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_engine(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def table_name(request: pytest.FixtureRequest, postgres_engine: Engine) -> Iterator[str]:
    safe_name = re.sub(r"[^a-z0-9_]", "_", request.node.name.lower())
    name = f"nxl9_{safe_name}"
    metadata = MetaData()
    table = Table(name, metadata)
    table.drop(postgres_engine, checkfirst=True)
    try:
        yield name
    finally:
        table.drop(postgres_engine, checkfirst=True)


def fetch_rows(engine: Engine, table_name: str, order_by: str = "id") -> list[dict[str, object]]:
    with engine.connect() as connection:
        result = connection.execute(
            text(f'SELECT * FROM "{table_name}" ORDER BY "{order_by}"')
        ).mappings()
        return [dict(row) for row in result]


def assert_sensitive_values_absent(text_value: str, sensitive_values: list[str]) -> None:
    if any(value in text_value for value in sensitive_values):
        raise AssertionError("Sensitive connection information was exposed")


def test_postgresql_connection_write_and_read(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    rows: Rows = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]

    assert SqlDestination(postgres_url, table_name, "replace").write(rows) == 2
    assert fetch_rows(postgres_engine, table_name) == rows
    assert SqlSource(postgres_url, None, table_name).read() == rows

    # A disposed connector engine can reconnect, proving its checked-out connection was returned.
    assert SqlSource(postgres_url, "SELECT 1 AS value", None).read() == [{"value": 1}]


def test_postgresql_append_preserves_existing_rows(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    SqlDestination(postgres_url, table_name, "fail").write([{"id": 1, "name": "Ada"}])
    SqlDestination(postgres_url, table_name, "append").write([{"id": 2, "name": "Grace"}])

    assert fetch_rows(postgres_engine, table_name) == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Grace"},
    ]


def test_postgresql_replace_recreates_schema_and_rows(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    SqlDestination(postgres_url, table_name, "fail").write([{"id": 1, "legacy": "old"}])
    replacement: Rows = [{"id": 10, "amount": 12.5}, {"id": 11, "amount": 25.0}]

    assert SqlDestination(postgres_url, table_name, "replace").write(replacement) == 2

    assert [column["name"] for column in inspect(postgres_engine).get_columns(table_name)] == [
        "id",
        "amount",
    ]
    assert fetch_rows(postgres_engine, table_name) == replacement


def test_postgresql_fail_keeps_existing_data(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    original: Rows = [{"id": 1, "name": "unchanged"}]
    assert SqlDestination(postgres_url, table_name, "fail").write(original) == 1

    with pytest.raises(ConnectorError, match="already exists"):
        SqlDestination(postgres_url, table_name, "fail").write([{"id": 2, "name": "new"}])

    assert fetch_rows(postgres_engine, table_name) == original


def test_csv_to_postgresql_pipeline_end_to_end(
    tmp_path: Path,
    postgres_url: str,
    postgres_engine: Engine,
    table_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "orders.csv"
    source.write_text(
        "id,status,total\n1,completed,10.50\n2,pending,20.00\n3,completed,30.25\n",
        encoding="utf-8",
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f"""
name: nxl9_postgresql_orders
source:
  type: csv
  path: {source.as_posix()}
transformations:
  - type: filter
    column: status
    operator: equals
    value: completed
  - type: select
    columns: [id, total]
  - type: rename
    columns:
      total: order_total
destination:
  type: postgresql
  connection_url: ${{DATABASE_URL}}
  table: {table_name}
  mode: replace
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", postgres_url)

    config = load_pipeline(pipeline)
    result = DefaultPipelineRunner().run(config)

    assert config.name == "nxl9_postgresql_orders"
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_read == 3
    assert result.rows_written == 2
    assert fetch_rows(postgres_engine, table_name) == [
        {"id": "1", "order_total": "10.50"},
        {"id": "3", "order_total": "30.25"},
    ]


def test_connection_credentials_are_not_exposed(
    tmp_path: Path,
    postgres_url: str,
    table_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "nxl9_recognizable_secret"
    bad_url = (
        make_url(postgres_url)
        .set(username="nxl9_missing_user", password=secret)
        .render_as_string(hide_password=False)
    )
    sensitive_values = [secret, "nxl9_missing_user", bad_url]

    with pytest.raises(ConnectorError) as captured:
        SqlDestination(bad_url, table_name, "append").write([{"id": 1}])
    assert_sensitive_values_absent(str(captured.value), sensitive_values)

    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    config = PipelineConfig(
        name="safe_failure",
        source=CsvSourceConfig(type="csv", path=str(source)),
        destination=SqlDestinationConfig(
            type="postgresql",
            connection_url=bad_url,
            table=table_name,
            mode="append",
        ),
    )
    with caplog.at_level(logging.INFO):
        result = DefaultPipelineRunner().run(config)
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert_sensitive_values_absent(result.error, sensitive_values)
    assert_sensitive_values_absent(caplog.text, sensitive_values)

    pipeline = tmp_path / "bad-credentials.yaml"
    pipeline.write_text(
        f"""
name: safe_cli_failure
source:
  type: csv
  path: {source.as_posix()}
destination:
  type: postgresql
  connection_url: ${{NXL9_BAD_DATABASE_URL}}
  table: {table_name}
  mode: append
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NXL9_BAD_DATABASE_URL", bad_url)
    cli_result = CliRunner().invoke(app, ["run", str(pipeline)])

    assert cli_result.exit_code == 3
    assert_sensitive_values_absent(cli_result.output, sensitive_values)
    assert_sensitive_values_absent(
        "\n".join(record.getMessage() for record in caplog.records),
        sensitive_values,
    )
