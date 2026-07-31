from typing import Protocol

from nexolith.types import Rows


class Transformation(Protocol):
    def apply(self, rows: Rows) -> Rows: ...
