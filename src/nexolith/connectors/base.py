from typing import Protocol

from nexolith.types import Rows


class SourceConnector(Protocol):
    def read(self) -> Rows: ...


class DestinationConnector(Protocol):
    def write(self, rows: Rows) -> int: ...
