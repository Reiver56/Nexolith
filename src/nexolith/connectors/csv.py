import csv
from pathlib import Path

from nexolith.exceptions import ConnectorError
from nexolith.types import Rows


class CsvSource:
    def __init__(self, path: str, encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.encoding = encoding

    def read(self) -> Rows:
        try:
            with self.path.open(newline="", encoding=self.encoding) as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except (OSError, csv.Error) as exc:
            raise ConnectorError(f"Could not read CSV file '{self.path}': {exc}") from exc


class CsvDestination:
    def __init__(self, path: str, encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.encoding = encoding

    def write(self, rows: Rows) -> int:
        if not rows:
            raise ConnectorError("Cannot write an empty dataset to CSV")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            columns = list(rows[0])
            with self.path.open("w", newline="", encoding=self.encoding) as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        except (OSError, csv.Error) as exc:
            raise ConnectorError(f"Could not write CSV file '{self.path}': {exc}") from exc
        return len(rows)
