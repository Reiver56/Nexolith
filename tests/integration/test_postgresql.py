import logging
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from typer.testing import CliRunner

from nexolith.cli import app
from nexolith.config import load_pipeline
from nexolith.config.models import (
    CsvSourceConfig,
    PipelineConfig,
    SqlDestinationConfig,
)
from nexolith.connectors.sql import SqlDestination, SqlSource
from nexolith.exceptions import ConnectorError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus
from nexolith.types import Rows

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_engine(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def table_name(request: pytest.FixtureRequest, postgres_engine: Engine) -> Iterator[str]:
    safe_name = re.sub(r"[^a-z0-9_]", "_", request.node.name.lower())
    name = f"nxl9_{safe_name}"
    metadata = MetaData()
    table = Table(name, metadata)
    table.drop(postgres_engine, checkfirst=True)
    try:
        yield name
    finally:
        table.drop(postgres_engine, checkfirst=True)


def fetch_rows(engine: Engine, table_name: str, order_by: str = "id") -> list[dict[str, object]]:
    with engine.connect() as connection:
        result = connection.execute(
            text(f'SELECT * FROM "{table_name}" ORDER BY "{order_by}"')
        ).mappings()
        return [dict(row) for row in result]


def column_data_types(engine: Engine, table_name: str) -> dict[str, str]:
    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :table_name"
            ),
            {"table_name": table_name},
        )
        return {row[0]: row[1] for row in result}


def assert_sensitive_values_absent(text_value: str, sensitive_values: list[str]) -> None:
    if any(value in text_value for value in sensitive_values):
        raise AssertionError("Sensitive connection information was exposed")


def test_postgresql_connection_write_and_read(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    rows: Rows = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]

    assert SqlDestination(postgres_url, table_name, "replace").write(rows) == 2
    assert fetch_rows(postgres_engine, table_name) == rows
    assert SqlSource(postgres_url, None, table_name).read() == rows

    # A disposed connector engine can reconnect, proving its checked-out connection was returned.
    assert SqlSource(postgres_url, "SELECT 1 AS value", None).read() == [{"value": 1}]


def test_postgresql_append_preserves_existing_rows(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    SqlDestination(postgres_url, table_name, "fail").write([{"id": 1, "name": "Ada"}])
    SqlDestination(postgres_url, table_name, "append").write([{"id": 2, "name": "Grace"}])

    assert fetch_rows(postgres_engine, table_name) == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Grace"},
    ]


def test_postgresql_replace_recreates_schema_and_rows(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    SqlDestination(postgres_url, table_name, "fail").write([{"id": 1, "legacy": "old"}])
    replacement: Rows = [{"id": 10, "amount": 12.5}, {"id": 11, "amount": 25.0}]

    assert SqlDestination(postgres_url, table_name, "replace").write(replacement) == 2

    assert [column["name"] for column in inspect(postgres_engine).get_columns(table_name)] == [
        "id",
        "amount",
    ]
    assert fetch_rows(postgres_engine, table_name) == replacement


def test_postgresql_fail_keeps_existing_data(
    postgres_url: str, postgres_engine: Engine, table_name: str
) -> None:
    original: Rows = [{"id": 1, "name": "unchanged"}]
    assert SqlDestination(postgres_url, table_name, "fail").write(original) == 1

    with pytest.raises(ConnectorError, match="already exists"):
        SqlDestination(postgres_url, table_name, "fail").write([{"id": 2, "name": "new"}])

    assert fetch_rows(postgres_engine, table_name) == original


# -- NXL-97: Decimal/datetime/date column type inference --------------------
#
# Before the fix, _prepare_table's type inference only recognized
# bool/int/float -- Decimal and datetime/date silently fell back to
# String(), so these columns were created as `character varying`. The write
# itself never failed; the damage only showed up later, the first time
# something tried to aggregate the column at the SQL level. Every test here
# checks information_schema.columns directly (not just that the write
# succeeded) and, for the aggregation test, actually runs SUM/MIN/MAX
# against the written columns -- that's the real, concrete failure this
# fix closes (found via the SQL enrichment chain example: SUM(text) raised
# psycopg.errors.UndefinedFunction).


def test_decimal_value_gets_a_real_numeric_column(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    rows: Rows = [{"id": 1, "total": Decimal("68557.51")}]
    SqlDestination(postgres_url, table_name, "replace").write(rows)

    assert column_data_types(postgres_engine, table_name)["total"] == "numeric"
    assert fetch_rows(postgres_engine, table_name) == [{"id": 1, "total": Decimal("68557.51")}]


def test_naive_datetime_gets_timestamp_without_time_zone(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    naive = datetime(2026, 7, 19, 0, 10, 17)
    rows: Rows = [{"id": 1, "occurred_at": naive}]
    SqlDestination(postgres_url, table_name, "replace").write(rows)

    assert column_data_types(postgres_engine, table_name)["occurred_at"] == (
        "timestamp without time zone"
    )
    assert fetch_rows(postgres_engine, table_name) == [{"id": 1, "occurred_at": naive}]


def test_timezone_aware_datetime_gets_timestamp_with_time_zone(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """A source value carrying real timezone info (e.g. a Postgres
    TIMESTAMPTZ column read back via psycopg) must not silently lose its
    offset by landing in a TIMESTAMP WITHOUT TIME ZONE column."""
    aware = datetime(2026, 7, 19, 0, 10, 17, tzinfo=UTC)
    rows: Rows = [{"id": 1, "occurred_at": aware}]
    SqlDestination(postgres_url, table_name, "replace").write(rows)

    assert column_data_types(postgres_engine, table_name)["occurred_at"] == (
        "timestamp with time zone"
    )
    assert fetch_rows(postgres_engine, table_name) == [{"id": 1, "occurred_at": aware}]


def test_date_value_gets_a_real_date_column(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    rows: Rows = [{"id": 1, "activated_on": date(2026, 7, 19)}]
    SqlDestination(postgres_url, table_name, "replace").write(rows)

    assert column_data_types(postgres_engine, table_name)["activated_on"] == "date"
    assert fetch_rows(postgres_engine, table_name) == [{"id": 1, "activated_on": date(2026, 7, 19)}]


def test_aggregation_works_on_decimal_and_datetime_columns(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """The real downstream consequence the bug caused: SUM/MIN/MAX against
    a Decimal/datetime column written by SqlDestination used to fail
    outright (`function sum(character varying) does not exist`), not just
    produce a wrong answer -- the column type itself was unusable for
    aggregation. This must now work, and produce the correct answer."""
    rows: Rows = [
        {"id": 1, "amount": Decimal("10.50"), "occurred_at": datetime(2026, 7, 19, 0, 10, 17)},
        {"id": 2, "amount": Decimal("20.00"), "occurred_at": datetime(2026, 7, 20, 15, 28, 0)},
        {"id": 3, "amount": Decimal("30.25"), "occurred_at": datetime(2026, 7, 18, 9, 10, 18)},
    ]
    SqlDestination(postgres_url, table_name, "replace").write(rows)

    with postgres_engine.connect() as connection:
        total, earliest, latest = connection.execute(
            text(f'SELECT SUM(amount), MIN(occurred_at), MAX(occurred_at) FROM "{table_name}"')
        ).one()

    assert total == Decimal("60.75")
    assert earliest == datetime(2026, 7, 18, 9, 10, 18)
    assert latest == datetime(2026, 7, 20, 15, 28, 0)


def test_existing_type_inference_unaffected(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """Regression: adding Decimal/datetime/date handling must not change
    inference for the types that already worked correctly."""
    rows: Rows = [
        {
            "id": 1,
            "is_active": True,
            "count": 3,
            "ratio": 1.5,
            "label": "unchanged",
        }
    ]
    SqlDestination(postgres_url, table_name, "replace").write(rows)

    types = column_data_types(postgres_engine, table_name)
    assert types["id"] == "integer"
    assert types["is_active"] == "integer"
    assert types["count"] == "integer"
    assert types["ratio"] == "double precision"
    assert types["label"] == "character varying"


# -- NXL-96: truncate mode ---------------------------------------------------
#
# truncate empties a table's rows in place (via DELETE FROM, not the SQL
# TRUNCATE statement -- see SqlDestination's own docstring for the real,
# investigated reason) without dropping/recreating the table, so real
# constraints (FK/PK/CHECK) from a pre-migrated schema survive a write that
# replace would otherwise destroy.


def test_truncate_preserves_constraints_and_replaces_rows(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                f'CREATE TABLE "{table_name}" ('
                "id INTEGER PRIMARY KEY, "
                "status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))"
                ")"
            )
        )
        connection.execute(text(f"INSERT INTO \"{table_name}\" (id, status) VALUES (1, 'active')"))

    SqlDestination(postgres_url, table_name, "truncate").write([{"id": 2, "status": "inactive"}])

    assert fetch_rows(postgres_engine, table_name) == [{"id": 2, "status": "inactive"}]
    assert inspect(postgres_engine).get_pk_constraint(table_name)["constrained_columns"] == ["id"]
    assert len(inspect(postgres_engine).get_check_constraints(table_name)) == 1

    # The CHECK constraint really did survive -- a value it forbids must
    # still fail, exactly as it would have before this write.
    with pytest.raises(ConnectorError):
        SqlDestination(postgres_url, table_name, "truncate").write(
            [{"id": 3, "status": "not-a-valid-status"}]
        )


def test_truncate_preserves_foreign_key_from_another_table(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """truncate on a table nothing else references -- the common case (a
    fact/staging table with an FK pointing OUT, not one pointed AT)."""
    parent = f"{table_name}_p"
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE TABLE "{parent}" (id INTEGER PRIMARY KEY)'))
        connection.execute(text(f'INSERT INTO "{parent}" (id) VALUES (1), (2)'))
        connection.execute(
            text(
                f'CREATE TABLE "{table_name}" ('
                "id INTEGER PRIMARY KEY, "
                f'parent_id INTEGER NOT NULL REFERENCES "{parent}"(id)'
                ")"
            )
        )
        connection.execute(text(f'INSERT INTO "{table_name}" (id, parent_id) VALUES (10, 1)'))

    try:
        SqlDestination(postgres_url, table_name, "truncate").write([{"id": 20, "parent_id": 2}])
        assert fetch_rows(postgres_engine, table_name) == [{"id": 20, "parent_id": 2}]
        assert inspect(postgres_engine).get_foreign_keys(table_name)[0]["referred_table"] == parent

        # The FK really did survive -- referencing a nonexistent parent
        # must still fail.
        with pytest.raises(ConnectorError):
            SqlDestination(postgres_url, table_name, "truncate").write(
                [{"id": 30, "parent_id": 999}]
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
            connection.execute(text(f'DROP TABLE IF EXISTS "{parent}"'))


def test_truncate_fk_referenced_table_no_conflict_succeeds(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """The real behavior when the table being truncated is itself
    referenced by another table's FK -- confirmed directly, not assumed.
    A raw SQL TRUNCATE refuses unconditionally in this situation (a
    schema-level check: the constraint merely being declared is enough,
    regardless of whether any row currently conflicts). DELETE FROM (what
    truncate mode actually runs) has no such schema-level restriction: the
    referencing child table here is empty -- the FK is declared but no row
    currently depends on the parent row being deleted -- so the write
    succeeds cleanly, something a plain TRUNCATE could not do at all
    without CASCADE (which would also delete rows from the child table)."""
    child = f"{table_name}_c"
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)'))
        connection.execute(text(f'INSERT INTO "{table_name}" (id) VALUES (1)'))
        connection.execute(
            text(
                f'CREATE TABLE "{child}" ('
                "id INTEGER PRIMARY KEY, "
                f'parent_id INTEGER NOT NULL REFERENCES "{table_name}"(id)'
                ")"
            )
        )
        # child stays empty -- no row currently references parent id 1.

    try:
        SqlDestination(postgres_url, table_name, "truncate").write([{"id": 2}])
        assert fetch_rows(postgres_engine, table_name) == [{"id": 2}]
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{child}"'))
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))


def test_truncate_fk_referenced_table_real_conflict_fails(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """Same setup, but the child table has a real row referencing the
    parent row about to be deleted. DELETE FROM raises a normal foreign
    key violation (wrapped as ConnectorError) -- the correct, expected
    relational-integrity behavior, not silently bypassed via CASCADE --
    and the original data is left intact, since the delete and insert
    share one transaction.

    Notably, this fails even though the new data reinserts the exact same
    id the child row depends on (`{"id": 1}`, identical to what's already
    there) -- confirmed directly, not assumed: Postgres checks a (default,
    non-deferrable) foreign key constraint immediately when the DELETE
    statement runs, not deferred until COMMIT, so a delete-then-reinsert
    of the same key within one transaction does not dodge the check. Only
    an explicitly `DEFERRABLE INITIALLY DEFERRED` constraint would allow
    that -- not something Nexolith can assume about a schema it didn't
    create.
    """
    child = f"{table_name}_c"
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)'))
        connection.execute(text(f'INSERT INTO "{table_name}" (id) VALUES (1)'))
        connection.execute(
            text(
                f'CREATE TABLE "{child}" ('
                "id INTEGER PRIMARY KEY, "
                f'parent_id INTEGER NOT NULL REFERENCES "{table_name}"(id)'
                ")"
            )
        )
        connection.execute(text(f'INSERT INTO "{child}" (id, parent_id) VALUES (100, 1)'))

    try:
        with pytest.raises(ConnectorError):
            SqlDestination(postgres_url, table_name, "truncate").write([{"id": 1}])

        # Original data untouched -- the failed delete rolled back.
        assert fetch_rows(postgres_engine, table_name) == [{"id": 1}]
        assert fetch_rows(postgres_engine, child) == [{"id": 100, "parent_id": 1}]
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{child}"'))
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))


def test_truncate_on_nonexistent_table_creates_it_like_replace(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """truncate against a table that doesn't exist yet has nothing to
    clear -- it creates the table fresh, same as replace/append do."""
    assert (
        SqlDestination(postgres_url, table_name, "truncate").write([{"id": 1, "name": "fresh"}])
        == 1
    )
    assert fetch_rows(postgres_engine, table_name) == [{"id": 1, "name": "fresh"}]


def test_other_modes_unaffected_by_truncate_addition(
    postgres_engine: Engine, table_name: str, postgres_url: str
) -> None:
    """Regression: adding `truncate` must not change append/replace/fail's
    own behavior -- exercise all three against the same table in sequence,
    same as the existing dedicated tests above, as one direct check that
    nothing about the shared _prepare_table/write path shifted."""
    SqlDestination(postgres_url, table_name, "fail").write([{"id": 1, "name": "first"}])
    with pytest.raises(ConnectorError, match="already exists"):
        SqlDestination(postgres_url, table_name, "fail").write([{"id": 2, "name": "second"}])

    SqlDestination(postgres_url, table_name, "append").write([{"id": 2, "name": "second"}])
    assert fetch_rows(postgres_engine, table_name) == [
        {"id": 1, "name": "first"},
        {"id": 2, "name": "second"},
    ]

    SqlDestination(postgres_url, table_name, "replace").write([{"id": 10, "other": "col"}])
    assert fetch_rows(postgres_engine, table_name) == [{"id": 10, "other": "col"}]


def test_csv_to_postgresql_pipeline_end_to_end(
    tmp_path: Path,
    postgres_url: str,
    postgres_engine: Engine,
    table_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "orders.csv"
    source.write_text(
        "id,status,total\n1,completed,10.50\n2,pending,20.00\n3,completed,30.25\n",
        encoding="utf-8",
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f"""
name: nxl9_postgresql_orders
source:
  type: csv
  path: {source.as_posix()}
transformations:
  - type: filter
    column: status
    operator: equals
    value: completed
  - type: select
    columns: [id, total]
  - type: rename
    columns:
      total: order_total
destination:
  type: postgresql
  connection_url: ${{DATABASE_URL}}
  table: {table_name}
  mode: replace
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", postgres_url)

    config = load_pipeline(pipeline)
    result = DefaultPipelineRunner().run(config)

    assert config.name == "nxl9_postgresql_orders"
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_read == 3
    assert result.rows_written == 2
    assert fetch_rows(postgres_engine, table_name) == [
        {"id": "1", "order_total": "10.50"},
        {"id": "3", "order_total": "30.25"},
    ]


def test_connection_credentials_are_not_exposed(
    tmp_path: Path,
    postgres_url: str,
    table_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "nxl9_recognizable_secret"
    bad_url = (
        make_url(postgres_url)
        .set(username="nxl9_missing_user", password=secret)
        .render_as_string(hide_password=False)
    )
    sensitive_values = [secret, "nxl9_missing_user", bad_url]

    with pytest.raises(ConnectorError) as captured:
        SqlDestination(bad_url, table_name, "append").write([{"id": 1}])
    assert_sensitive_values_absent(str(captured.value), sensitive_values)

    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    config = PipelineConfig(
        name="safe_failure",
        source=CsvSourceConfig(type="csv", path=str(source)),
        destination=SqlDestinationConfig(
            type="postgresql",
            connection_url=bad_url,
            table=table_name,
            mode="append",
        ),
    )
    with caplog.at_level(logging.INFO):
        result = DefaultPipelineRunner().run(config)
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert_sensitive_values_absent(result.error, sensitive_values)
    assert_sensitive_values_absent(caplog.text, sensitive_values)

    pipeline = tmp_path / "bad-credentials.yaml"
    pipeline.write_text(
        f"""
name: safe_cli_failure
source:
  type: csv
  path: {source.as_posix()}
destination:
  type: postgresql
  connection_url: ${{NXL9_BAD_DATABASE_URL}}
  table: {table_name}
  mode: append
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NXL9_BAD_DATABASE_URL", bad_url)
    cli_result = CliRunner().invoke(app, ["run", str(pipeline)])

    assert cli_result.exit_code == 3
    assert_sensitive_values_absent(cli_result.output, sensitive_values)
    assert_sensitive_values_absent(
        "\n".join(record.getMessage() for record in caplog.records),
        sensitive_values,
    )
