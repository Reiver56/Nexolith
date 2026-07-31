from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from nexolith.exceptions import ConnectorError
from nexolith.types import Rows


class SqlSource:
    def __init__(self, connection_url: str, query: str | None, table: str | None) -> None:
        self.engine = _create_sql_engine(connection_url)
        self.query = query
        self.table = table

    def read(self) -> Rows:
        statement = self.query or f'SELECT * FROM "{self.table}"'
        try:
            with self.engine.connect() as connection:
                return [dict(row) for row in connection.execute(text(statement)).mappings()]
        except SQLAlchemyError as exc:
            raise ConnectorError(
                "Could not read from SQL source. Check the connection, table, or query."
            ) from exc
        finally:
            self.engine.dispose()


class SqlDestination:
    def __init__(self, connection_url: str, table: str, mode: str) -> None:
        self.engine = _create_sql_engine(connection_url)
        self.table = table
        self.mode = mode

    def _prepare_table(self, engine: Engine, rows: Rows) -> Table:
        metadata = MetaData()
        exists = inspect(engine).has_table(self.table)
        if exists and self.mode == "fail":
            raise ConnectorError(f"Destination table '{self.table}' already exists")
        if exists and self.mode == "replace":
            Table(self.table, metadata, autoload_with=engine).drop(engine)
            metadata.clear()
            exists = False
        if not exists:
            from sqlalchemy import Column, Float, Integer, String

            def column_type(value: Any) -> Any:
                if isinstance(value, bool):
                    return Integer()
                if isinstance(value, int):
                    return Integer()
                if isinstance(value, float):
                    return Float()
                return String()

            table = Table(
                self.table,
                metadata,
                *(Column(name, column_type(value)) for name, value in rows[0].items()),
            )
            metadata.create_all(engine)
            return table
        return Table(self.table, metadata, autoload_with=engine)

    def write(self, rows: Rows) -> int:
        if not rows:
            raise ConnectorError("Cannot write an empty dataset to SQL")
        try:
            table = self._prepare_table(self.engine, rows)
            with self.engine.begin() as connection:
                connection.execute(table.insert(), rows)
            return len(rows)
        except ConnectorError:
            raise
        except SQLAlchemyError as exc:
            raise ConnectorError(
                "Could not write to SQL destination. Check the connection, table, and write mode."
            ) from exc
        finally:
            self.engine.dispose()


def _create_sql_engine(connection_url: str) -> Engine:
    try:
        return create_engine(connection_url)
    except (ImportError, SQLAlchemyError) as exc:
        raise ConnectorError(
            "Could not configure SQL connector. Check the connection URL and database driver."
        ) from exc
