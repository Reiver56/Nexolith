import pytest

from nexolith.transformations.builtin import DropNulls, Filter, Rename, Select
from nexolith.types import Rows


def test_select() -> None:
    assert Select(["id"]).apply([{"id": 1, "name": "Ada"}]) == [{"id": 1}]


def test_rename() -> None:
    assert Rename({"name": "full_name"}).apply([{"id": 1, "name": "Ada"}]) == [
        {"id": 1, "full_name": "Ada"}
    ]


def test_drop_nulls() -> None:
    rows: Rows = [{"id": 1, "name": None}, {"id": 2, "name": "Ada"}, {"id": 3, "name": ""}]
    assert DropNulls(["name"]).apply(rows) == [{"id": 2, "name": "Ada"}]


@pytest.mark.parametrize(
    ("operation", "value", "expected_ids"),
    [
        ("equals", 10, [1]),
        ("not_equals", 10, [2, 3]),
        ("greater_than", 10, [2]),
        ("greater_than_or_equal", 10, [1, 2]),
        ("less_than", 10, [3]),
        ("less_than_or_equal", 10, [1, 3]),
        ("contains", "ell", [1]),
        ("is_null", None, [3]),
        ("is_not_null", None, [1, 2]),
    ],
)
def test_filter_operators(operation: str, value: object, expected_ids: list[int]) -> None:
    rows: Rows = [
        {"id": 1, "score": 10, "text": "hello"},
        {"id": 2, "score": 20, "text": "world"},
        {"id": 3, "score": 5, "text": None},
    ]
    column = "text" if operation in {"contains", "is_null", "is_not_null"} else "score"
    assert [row["id"] for row in Filter(column, operation, value).apply(rows)] == expected_ids  # type: ignore[arg-type]
