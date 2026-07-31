import csv
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.cli import app
from nexolith.config import load_pipeline
from nexolith.connectors.sql import SqlSource
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus


def test_csv_to_sqlite_end_to_end(tmp_path: Path) -> None:
    source = tmp_path / "orders.csv"
    source.write_text(
        "id,status,total\n1,completed,10\n2,pending,20\n3,completed,30\n", encoding="utf-8"
    )
    database = tmp_path / "pipeline.db"
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f"""
name: orders
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
destination:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  table: orders
  mode: replace
""",
        encoding="utf-8",
    )
    result = DefaultPipelineRunner().run(load_pipeline(pipeline))
    assert result.status is ExecutionStatus.SUCCEEDED
    assert SqlSource(f"sqlite:///{database}", None, "orders").read() == [
        {"id": "1", "total": "10"},
        {"id": "3", "total": "30"},
    ]


def test_first_pipeline_tutorial_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository_root = Path(__file__).parents[2]
    shutil.copytree(repository_root / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    pipeline = Path("examples/pipelines/completed_orders.yaml")
    runner = CliRunner()

    validation = runner.invoke(app, ["validate", str(pipeline)])
    assert validation.exit_code == 0
    assert validation.stdout.strip() == "Pipeline 'completed_orders' is valid."

    execution = runner.invoke(app, ["run", str(pipeline)])
    assert execution.exit_code == 0
    assert "Status: succeeded" in execution.stdout
    assert "Rows read: 5" in execution.stdout
    assert "Rows written: 3" in execution.stdout

    with Path("build/completed_orders.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [
            {"id": "1", "customer_id": "101", "order_total": "49.90"},
            {"id": "3", "customer_id": "103", "order_total": "125.00"},
            {"id": "5", "customer_id": "105", "order_total": "75.25"},
        ]
