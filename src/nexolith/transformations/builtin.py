import operator
from collections.abc import Callable
from typing import Any

from nexolith.exceptions import TransformationError
from nexolith.types import Row, Rows, Scalar


class Select:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns

    def apply(self, rows: Rows) -> Rows:
        missing = set(self.columns) - set(rows[0] if rows else [])
        if missing:
            raise TransformationError(f"Select references missing columns: {sorted(missing)}")
        return [{column: row[column] for column in self.columns} for row in rows]


class Rename:
    def __init__(self, columns: dict[str, str]) -> None:
        self.columns = columns

    def apply(self, rows: Rows) -> Rows:
        missing = set(self.columns) - set(rows[0] if rows else [])
        if missing:
            raise TransformationError(f"Rename references missing columns: {sorted(missing)}")
        return [{self.columns.get(key, key): value for key, value in row.items()} for row in rows]


class DropNulls:
    def __init__(self, columns: list[str] | None) -> None:
        self.columns = columns

    def apply(self, rows: Rows) -> Rows:
        return [
            row
            for row in rows
            if all(row.get(column) not in (None, "") for column in (self.columns or list(row)))
        ]


def _comparable(value: Scalar, expected: Scalar) -> tuple[Any, Any]:
    if isinstance(expected, bool):
        normalized = str(value).lower() in {"true", "1", "yes"} if isinstance(value, str) else value
        return normalized, expected
    if isinstance(expected, (int, float)) and isinstance(value, str):
        try:
            return float(value), float(expected)
        except ValueError:
            return value, expected
    return value, expected


class Filter:
    COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
        "equals": operator.eq,
        "not_equals": operator.ne,
        "greater_than": operator.gt,
        "greater_than_or_equal": operator.ge,
        "less_than": operator.lt,
        "less_than_or_equal": operator.le,
    }

    def __init__(self, column: str, operation: str, value: Scalar) -> None:
        self.column = column
        self.operation = operation
        self.value = value

    def _matches(self, row: Row) -> bool:
        actual = row.get(self.column)
        if self.operation == "is_null":
            return actual in (None, "")
        if self.operation == "is_not_null":
            return actual not in (None, "")
        if self.operation == "contains":
            return str(self.value) in str(actual) if actual is not None else False
        left, right = _comparable(actual, self.value)
        try:
            return self.COMPARISONS[self.operation](left, right)
        except (KeyError, TypeError):
            return False

    def apply(self, rows: Rows) -> Rows:
        if rows and self.column not in rows[0]:
            raise TransformationError(f"Filter references missing column: {self.column}")
        return [row for row in rows if self._matches(row)]
