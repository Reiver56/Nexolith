from pathlib import Path

import pytest


@pytest.fixture
def pipeline_document(tmp_path: Path) -> str:
    source = tmp_path / "input.csv"
    source.write_text("id,status\n1,completed\n2,pending\n", encoding="utf-8")
    return f"""
name: test_pipeline
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(tmp_path / "output.csv").as_posix()}
"""
