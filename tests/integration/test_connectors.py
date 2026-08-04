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


def test_sqlite_read_with_bound_parameters(tmp_path: Path) -> None:
    """NXL-82: a named parameter reaches the real SQLAlchemy Core bound
    parameter call (`:name` syntax) and actually filters the result."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    rows = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]
    SqlDestination(url, "people", "fail").write(rows)
    result = SqlSource(url, "SELECT * FROM people WHERE name = :name", None, {"name": "Ada"}).read()
    assert result == [{"id": 1, "name": "Ada"}]


def test_sqlite_read_parameters_are_bound_not_interpolated(tmp_path: Path) -> None:
    """A deliberately malicious-looking parameter value must be treated as
    inert data, never executed as SQL -- proof that binding goes through the
    real driver parameter mechanism rather than string interpolation."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    rows = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]
    SqlDestination(url, "people", "fail").write(rows)
    malicious = "'; DROP TABLE people; --"

    result = SqlSource(
        url, "SELECT * FROM people WHERE name = :name", None, {"name": malicious}
    ).read()

    assert result == []
    assert SqlSource(url, None, "people").read() == rows
