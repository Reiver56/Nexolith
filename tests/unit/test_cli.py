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


def test_run_command(tmp_path: Path, pipeline_document: str) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(pipeline_document, encoding="utf-8")
    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 0
    assert "Status: succeeded" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "Nexolith 0.2.0"
