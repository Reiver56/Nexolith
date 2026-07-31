from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner
from yaml import YAMLError

from nexolith.cli import app
from nexolith.config import load_pipeline
from nexolith.connectors.csv import CsvSource
from nexolith.connectors.sql import SqlSource
from nexolith.exceptions import (
    ConfigurationError,
    ConnectorError,
    ExecutionError,
    NexolithError,
    TransformationError,
)

runner = CliRunner()


def assert_safe_cli_failure(
    output: str,
    *,
    category: str,
    useful_text: str,
    sensitive_values: tuple[str, ...] = (),
) -> None:
    lowered = output.lower()
    if any(value in output for value in sensitive_values):
        raise AssertionError("Sensitive information was exposed in CLI output")
    assert f"error [{category}]" in lowered
    assert useful_text.lower() in lowered
    assert "traceback" not in lowered


def test_domain_errors_share_single_base_class() -> None:
    assert issubclass(ConfigurationError, NexolithError)
    assert issubclass(ConnectorError, NexolithError)
    assert issubclass(TransformationError, NexolithError)
    assert issubclass(ExecutionError, NexolithError)


def test_invalid_yaml_preserves_parser_cause(tmp_path: Path) -> None:
    pipeline = tmp_path / "invalid.yaml"
    pipeline.write_text("name: [", encoding="utf-8")

    with pytest.raises(ConfigurationError) as captured:
        load_pipeline(pipeline)

    assert isinstance(captured.value.__cause__, YAMLError)
    assert "line" in str(captured.value)


def test_invalid_configuration_preserves_validation_cause(tmp_path: Path) -> None:
    pipeline = tmp_path / "invalid.yaml"
    pipeline.write_text("name: valid\nsource: {}\ndestination: {}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as captured:
        load_pipeline(pipeline)

    assert isinstance(captured.value.__cause__, ValidationError)


def test_missing_csv_preserves_io_cause(tmp_path: Path) -> None:
    with pytest.raises(ConnectorError) as captured:
        CsvSource(str(tmp_path / "missing.csv")).read()

    assert isinstance(captured.value.__cause__, OSError)
    assert "check the path" in str(captured.value).lower()


def test_malformed_sql_connector_preserves_driver_cause() -> None:
    with pytest.raises(ConnectorError) as captured:
        SqlSource("missing+dialect://user:secret@localhost/database", None, "items")

    assert captured.value.__cause__ is not None
    assert "check the connection url" in str(captured.value).lower()


def test_validate_invalid_configuration_has_stable_diagnostic(tmp_path: Path) -> None:
    pipeline = tmp_path / "invalid.yaml"
    pipeline.write_text("name: incomplete\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(pipeline)])

    assert result.exit_code == 2
    assert_safe_cli_failure(
        result.output,
        category="configuration",
        useful_text="invalid pipeline configuration",
    )


def test_validate_missing_file_has_stable_diagnostic(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "missing.yaml")])

    assert result.exit_code == 2
    assert_safe_cli_failure(
        result.output,
        category="configuration",
        useful_text="pipeline file not found",
    )


def test_run_missing_input_has_connector_diagnostic(tmp_path: Path) -> None:
    pipeline = tmp_path / "missing-input.yaml"
    pipeline.write_text(
        f"""
name: missing_input
source:
  type: csv
  path: {(tmp_path / "missing.csv").as_posix()}
destination:
  type: csv
  path: {(tmp_path / "output.csv").as_posix()}
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", str(pipeline)])

    assert result.exit_code == 3
    assert_safe_cli_failure(
        result.output,
        category="connector",
        useful_text="could not read csv file",
    )


def test_run_invalid_transformation_has_transformation_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    pipeline = tmp_path / "invalid-transformation.yaml"
    pipeline.write_text(
        f"""
name: invalid_transformation
source:
  type: csv
  path: {source.as_posix()}
transformations:
  - type: select
    columns: [missing_column]
destination:
  type: csv
  path: {(tmp_path / "output.csv").as_posix()}
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", str(pipeline)])

    assert result.exit_code == 3
    assert_safe_cli_failure(
        result.output,
        category="transformation",
        useful_text="missing columns",
    )


def test_run_redacts_misconfigured_connection_credentials(tmp_path: Path) -> None:
    secret = "nxl10_recognizable_secret"
    username = "nxl10_sensitive_user"
    connection_url = f"missing+dialect://{username}:{secret}@localhost/database"
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    pipeline = tmp_path / "secret.yaml"
    pipeline.write_text(
        f"""
name: safe_error
source:
  type: csv
  path: {source.as_posix()}
destination:
  type: postgresql
  connection_url: ${{NXL10_DATABASE_URL}}
  table: output
  mode: append
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["run", str(pipeline)],
        env={"NXL10_DATABASE_URL": connection_url},
    )

    assert result.exit_code == 3
    assert_safe_cli_failure(
        result.output,
        category="connector",
        useful_text="could not configure sql connector",
        sensitive_values=(secret, username, connection_url),
    )
