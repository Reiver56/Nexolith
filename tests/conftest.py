import os
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep service-dependent tests out of the standard test suite."""
    postgres_requested = "postgres" in config.getoption("-m")
    if postgres_requested:
        return
    skip_postgres = pytest.mark.skip(reason="run explicitly with: pytest -m postgres")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip_postgres)


@pytest.fixture
def postgres_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.fail(
            "DATABASE_URL is required when PostgreSQL tests are explicitly requested",
            pytrace=False,
        )
    return url


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
