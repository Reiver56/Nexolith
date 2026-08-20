from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from nexolith.exceptions import ConnectorError
from nexolith.types import Rows


class SqlSource:
    def __init__(
        self,
        connection_url: str,
        query: str | None,
        table: str | None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.engine = _create_sql_engine(connection_url)
        self.query = query
        self.table = table
        # Bound via SQLAlchemy Core's own `:name` parameter syntax --
        # `connection.execute(text(statement), parameters)` -- the real
        # parameter-binding call the underlying DBAPI driver (psycopg for
        # postgresql, sqlite3 for sqlite) executes against, never string
        # interpolation (NXL-82).
        self.parameters = parameters or {}

    def read(self) -> Rows:
        statement = self.query or f'SELECT * FROM "{self.table}"'
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(statement), self.parameters)
                return [dict(row) for row in result.mappings()]
        except SQLAlchemyError as exc:
            raise ConnectorError(
                "Could not read from SQL source. Check the connection, table, or query."
            ) from exc
        finally:
            self.engine.dispose()


class SqlDestination:
    """Four write modes, each a real tradeoff -- pick deliberately, not by
    default:

    - `append`: insert into an existing table, or create it (inferring
      column types from the incoming rows) when absent. Never touches
      existing rows or schema. Safe against a constrained schema, but
      never clears anything -- reruns accumulate rows unless the source
      itself is already deduplicated/idempotent.
    - `replace`: drop the existing table (if any) and recreate it from the
      incoming rows' inferred types, then insert. Destroys ANY real schema
      the table had -- foreign keys, primary keys, check constraints, even
      column types more precise than what's inferred from one batch of
      Python values. Only safe when Nexolith itself owns this table's
      schema outright (nothing else references it, nothing hand-migrated
      constraints onto it) -- e.g. a throwaway analytics/staging table.
      Never safe against a real, pre-migrated, constrained schema (NXL-96:
      found via FonoLink's schema, where this destroyed FKs/PKs/CHECKs).
    - `fail`: refuse if the table already exists at all -- a pure
      existence check, not a content or schema check. Unusable against a
      pre-migrated schema, where the (empty) table legitimately exists
      before the first run for reasons that have nothing to do with a
      previous Nexolith run having left it there.
    - `truncate` (NXL-96): clear the table's existing rows, in place, then
      insert -- schema, constraints, and column types all untouched. The
      real alternative to `replace` for a properly-migrated destination:
      "reset contents on each run" without "destroy the schema every run."
      Implemented as `DELETE FROM` the table (via SQLAlchemy Core's
      `table.delete()`), deliberately NOT the SQL `TRUNCATE` statement
      despite the mode's name -- investigated directly against real
      Postgres: `TRUNCATE` unconditionally refuses whenever ANY other
      table has a foreign key referencing this one, even if zero rows
      would actually be affected (a schema-level check, not a data-level
      one) -- `TRUNCATE ... CASCADE` would work, but silently deletes rows
      from those other tables too, an implicit multi-table blast radius no
      single destination's own write mode should ever have. `DELETE FROM`
      has no such restriction: it succeeds unconditionally when nothing
      references this table, and when something does, it only fails if a
      real conflicting row currently exists -- correct, expected
      relational-integrity behavior, surfaced as a normal `ConnectorError`
      like any other write failure, not silently bypassed. Confirmed
      directly: this check is immediate, not deferred to `COMMIT` --
      a default (non-`DEFERRABLE`) Postgres FK constraint is checked as
      soon as the `DELETE` statement runs, so deleting a row a live FK
      still points to fails right there even if the very next statement
      in the same transaction (the insert of new data) would have
      re-established an identical value. `truncate` mode cannot dodge
      this by reinserting the same key; it only ever works cleanly
      against a table nothing else currently references with a live row.
      One real, accepted side effect of choosing `DELETE` over `TRUNCATE`: a
      `SERIAL`/`IDENTITY` primary key's sequence is NOT reset (`TRUNCATE`
      resets it, `DELETE` doesn't) -- deliberately kept, since resetting
      it would let a freshly-inserted row reuse an id a completely
      unrelated table might still hold a historical reference to (e.g.
      FonoLink's own `fraud_flags.cdr_reference` pointing at a `cdr_raw.id`
      that a `truncate`-then-reinsert cycle could otherwise silently
      recycle onto different data). `DELETE FROM` is also the only one of
      the two that works at all against sqlite, which has no `TRUNCATE`
      statement -- consistent with this connector already supporting both
      dialects with the same code path.
    """

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
            from sqlalchemy import Column, Date, DateTime, Float, Integer, Numeric, String

            def column_type(value: Any) -> Any:
                if isinstance(value, bool):
                    return Integer()
                if isinstance(value, int):
                    return Integer()
                if isinstance(value, float):
                    return Float()
                if isinstance(value, Decimal):
                    # Unconstrained Numeric -- no fixed precision/scale.
                    # Against Postgres this creates a plain `numeric`
                    # column, which stores exact values at whatever
                    # precision/scale they arrive at (arbitrary, not
                    # rounded) -- the only choice that doesn't risk
                    # silently truncating a value we have no schema-level
                    # information to bound (this is inferred from live
                    # data, not a declared schema). SQLAlchemy's `Numeric`
                    # already returns `Decimal` on read back, matching the
                    # type being written.
                    return Numeric()
                if isinstance(value, datetime):
                    # datetime.datetime is itself a `date` subclass, so
                    # this check must come before the plain `date` one
                    # below -- same ordering principle as bool-before-int
                    # above. timezone=True/False is chosen from the actual
                    # value rather than always defaulting to naive: a
                    # source column with tz data (e.g. Postgres
                    # TIMESTAMPTZ) must not silently lose its offset by
                    # landing in a TIMESTAMP WITHOUT TIME ZONE column.
                    return DateTime(timezone=value.tzinfo is not None)
                if isinstance(value, date):
                    return Date()
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
                if self.mode == "truncate":
                    # Same transaction as the insert below: if the insert
                    # fails, the delete rolls back too -- the table is
                    # never left empty by a failed write.
                    connection.execute(table.delete())
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

    def upsert(self, rows: Rows, conflict_keys: tuple[str, ...]) -> int:
        """Atomically insert or update rows using a declared unique key."""
        if not rows:
            raise ConnectorError("Cannot upsert an empty dataset to SQL")
        if not conflict_keys:
            raise ConnectorError("SQL upsert requires at least one conflict key")
        if len(conflict_keys) != len(set(conflict_keys)) or any(not key for key in conflict_keys):
            raise ConnectorError("SQL upsert conflict keys must be non-empty and unique")
        missing = sorted(key for key in conflict_keys if any(key not in row for row in rows))
        if missing:
            raise ConnectorError(
                "SQL upsert conflict key(s) are absent from incoming rows: " + ", ".join(missing)
            )
        try:
            dialect = self.engine.dialect.name
            if dialect not in {"sqlite", "postgresql"}:
                raise ConnectorError(
                    "This SQL backend does not provide supported safe upsert semantics."
                )
            inspector = inspect(self.engine)
            if not inspector.has_table(self.table):
                raise ConnectorError(
                    "SQL upsert requires an existing table with a matching unique constraint."
                )
            table = Table(self.table, MetaData(), autoload_with=self.engine)
            unknown_keys = sorted(set(conflict_keys) - set(table.columns.keys()))
            if unknown_keys:
                raise ConnectorError(
                    "SQL upsert conflict key(s) do not exist in the destination table: "
                    + ", ".join(unknown_keys)
                )
            incoming_columns = set().union(*(row.keys() for row in rows))
            unknown_columns = sorted(incoming_columns - set(table.columns.keys()))
            if unknown_columns:
                raise ConnectorError(
                    "SQL upsert incoming column(s) do not exist in the destination table: "
                    + ", ".join(unknown_columns)
                )
            if not _has_unique_key(inspector, self.table, conflict_keys):
                raise ConnectorError(
                    "SQL upsert conflict keys must match a primary key or unique constraint."
                )

            insert_factory = postgresql_insert if dialect == "postgresql" else sqlite_insert
            statement = insert_factory(table)
            update_columns = [name for name in rows[0] if name not in conflict_keys]
            if update_columns:
                statement = statement.on_conflict_do_update(
                    index_elements=list(conflict_keys),
                    set_={name: statement.excluded[name] for name in update_columns},
                )
            else:
                statement = statement.on_conflict_do_nothing(index_elements=list(conflict_keys))
            with self.engine.begin() as connection:
                connection.execute(statement, rows)
            return len(rows)
        except ConnectorError:
            raise
        except SQLAlchemyError as exc:
            raise ConnectorError(
                "Could not upsert to SQL destination. Check the table, conflict keys, and data."
            ) from exc
        finally:
            self.engine.dispose()


def _has_unique_key(inspector: Any, table: str, conflict_keys: tuple[str, ...]) -> bool:
    expected = set(conflict_keys)
    primary = inspector.get_pk_constraint(table).get("constrained_columns") or []
    candidates = [primary]
    candidates.extend(
        constraint.get("column_names") or []
        for constraint in inspector.get_unique_constraints(table)
    )
    candidates.extend(
        index.get("column_names") or []
        for index in inspector.get_indexes(table)
        if index.get("unique")
    )
    return any(
        len(columns) == len(conflict_keys) and set(columns) == expected for columns in candidates
    )


def _create_sql_engine(connection_url: str) -> Engine:
    try:
        return create_engine(connection_url)
    except (ImportError, SQLAlchemyError) as exc:
        raise ConnectorError(
            "Could not configure SQL connector. Check the connection URL and database driver."
        ) from exc
