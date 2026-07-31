from pathlib import Path

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
