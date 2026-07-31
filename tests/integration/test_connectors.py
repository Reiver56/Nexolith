from pathlib import Path

from nexolith.connectors.csv import CsvDestination, CsvSource
from nexolith.connectors.sql import SqlDestination, SqlSource


def test_csv_read_write(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data.csv"
    rows = [{"id": "1", "name": "Ada"}, {"id": "2", "name": "Grace"}]
    assert CsvDestination(str(path)).write(rows) == 2
    assert CsvSource(str(path)).read() == rows


def test_sqlite_read_write(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    rows = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]
    assert SqlDestination(url, "people", "fail").write(rows) == 2
    assert SqlSource(url, None, "people").read() == rows
