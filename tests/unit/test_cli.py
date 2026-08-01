import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from nexolith.cli import app

runner = CliRunner()


def test_validate_command(tmp_path: Path, pipeline_document: str) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(pipeline_document, encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "is valid" in result.stdout
    assert result.stderr == ""


def test_validate_command_failure_reports_to_stderr_only(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error [configuration]" in result.stderr


def test_run_command(tmp_path: Path, pipeline_document: str) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(pipeline_document, encoding="utf-8")
    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 0
    assert "Status: succeeded" in result.stdout
    assert "Loading pipeline" not in result.stdout
    assert "Loading pipeline" not in result.stderr


def test_run_command_failure_reports_to_stderr_only(tmp_path: Path) -> None:
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
    assert result.stdout == ""
    assert "Error [connector]" in result.stderr


def test_run_command_logs_to_stderr_and_result_to_stdout(
    tmp_path: Path, pipeline_document: str
) -> None:
    """Pin the real-process stream contract; CliRunner cannot observe basicConfig logging."""
    path = tmp_path / "pipeline.yaml"
    path.write_text(pipeline_document, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", "from nexolith.cli.app import app; app()", "run", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.startswith(
        "Pipeline: test_pipeline\nStatus: succeeded\nRows read: 2\nRows written: 2\nDuration: "
    )
    assert "INFO" in result.stderr
    assert "started" in result.stderr
    assert "succeeded" in result.stderr


def test_diagnostics_command_reports_to_stdout_only() -> None:
    result = runner.invoke(app, ["diagnostics"])
    assert result.exit_code == 0
    assert result.stdout.startswith("Nexolith environment diagnostics")
    assert result.stderr == ""


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "Nexolith 0.3.0"
    assert result.stderr == ""


def test_version_does_not_start_interactive_session() -> None:
    """--version is eager and exits before the interactive session could start."""
    result = runner.invoke(app, ["--version"])
    assert "Nexo - Nexolith interactive session" not in result.stdout
