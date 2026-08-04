from pathlib import Path

import pytest

from nexolith.config import load_pipeline
from nexolith.config.models import SqlSourceConfig
from nexolith.exceptions import ConfigurationError


def test_valid_yaml(tmp_path: Path, pipeline_document: str) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(pipeline_document, encoding="utf-8")
    assert load_pipeline(path).name == "test_pipeline"


def test_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text("name: [", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_pipeline(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_pipeline(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("section", "replacement", "expected"),
    [
        ("source", "source:\n  type: unknown", "source"),
        ("transformations", "transformations:\n  - type: unknown", "transformations"),
    ],
)
def test_unknown_component(
    tmp_path: Path,
    pipeline_document: str,
    section: str,
    replacement: str,
    expected: str,
) -> None:
    lines = pipeline_document.splitlines()
    if section == "source":
        start, end = lines.index("source:"), lines.index("transformations: []")
    else:
        start, end = lines.index("transformations: []"), lines.index("destination:")
    lines[start:end] = replacement.splitlines()
    path = tmp_path / "bad.yaml"
    path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=expected):
        load_pipeline(path)


def test_missing_environment_variable(
    tmp_path: Path, pipeline_document: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEXOLITH_TEST_URL", raising=False)
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        pipeline_document.replace("path:", "path: ${NEXOLITH_TEST_URL} #", 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="NEXOLITH_TEST_URL"):
        load_pipeline(path)


def test_environment_variable_substitution(
    tmp_path: Path, pipeline_document: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXOLITH_TEST_PATH", "resolved.csv")
    path = tmp_path / "pipeline.yaml"
    prefix, suffix = pipeline_document.split("transformations:", 1)
    prefix_lines = prefix.splitlines()
    prefix_lines[-1] = "  path: ${NEXOLITH_TEST_PATH}"
    path.write_text("\n".join(prefix_lines) + "\ntransformations:" + suffix, encoding="utf-8")
    assert load_pipeline(path).source.path == "resolved.csv"  # type: ignore[union-attr]


def _make_sqlite_db(tmp_path: Path) -> Path:
    import sqlite3

    database = tmp_path / "source.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE items (id INTEGER, status TEXT)")
    connection.executemany(
        "INSERT INTO items VALUES (?, ?)",
        [(1, "active"), (2, "inactive"), (3, "active")],
    )
    connection.commit()
    connection.close()
    return database


def _sql_pipeline_yaml(name: str, database: Path, output: Path, source_block: str) -> str:
    return f"""
name: {name}
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
{source_block}
transformations: []
destination:
  type: csv
  path: {output.as_posix()}
"""


def test_query_file_resolves_and_executes_equivalently_to_inline_query(tmp_path: Path) -> None:
    """A `query_file`-based pipeline must produce byte-identical output to an
    equivalent inline-`query` pipeline reading the same SQL -- not just
    "doesn't crash"."""
    from nexolith.execution import DefaultPipelineRunner
    from nexolith.models import ExecutionStatus

    database = _make_sqlite_db(tmp_path)
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    sql = "SELECT id, status FROM items WHERE status = 'active'"
    (query_dir / "active_items.sql").write_text(sql, encoding="utf-8")

    inline_path = tmp_path / "inline.yaml"
    inline_path.write_text(
        _sql_pipeline_yaml("inline", database, tmp_path / "inline.csv", f'  query: "{sql}"'),
        encoding="utf-8",
    )
    file_path = tmp_path / "from_file.yaml"
    file_path.write_text(
        _sql_pipeline_yaml(
            "from_file",
            database,
            tmp_path / "from_file.csv",
            "  query_file: queries/active_items.sql",
        ),
        encoding="utf-8",
    )

    inline_config = load_pipeline(inline_path)
    file_config = load_pipeline(file_path)

    assert isinstance(file_config.source, SqlSourceConfig)
    assert file_config.source.query == sql
    assert file_config.source.query_file == "queries/active_items.sql"

    runner = DefaultPipelineRunner()
    inline_result = runner.run(inline_config)
    file_result = runner.run(file_config)

    assert inline_result.status is ExecutionStatus.SUCCEEDED
    assert file_result.status is ExecutionStatus.SUCCEEDED
    assert inline_result.rows_read == file_result.rows_read == 2
    assert inline_result.rows_written == file_result.rows_written == 2
    assert (tmp_path / "inline.csv").read_text(encoding="utf-8") == (
        tmp_path / "from_file.csv"
    ).read_text(encoding="utf-8")


def test_missing_query_file_caught_at_validation(tmp_path: Path) -> None:
    """`load_pipeline` is what both `validate` and `run` call to load a
    pipeline -- a missing query file must fail here, not only once execution
    tries to use the (never-populated) query."""
    database = _make_sqlite_db(tmp_path)
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        _sql_pipeline_yaml(
            "missing_query_file",
            database,
            tmp_path / "out.csv",
            "  query_file: queries/does_not_exist.sql",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Query file not found"):
        load_pipeline(path)


def test_query_and_query_file_together_is_rejected(tmp_path: Path) -> None:
    database = _make_sqlite_db(tmp_path)
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "active_items.sql").write_text("SELECT 1", encoding="utf-8")
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        _sql_pipeline_yaml(
            "both",
            database,
            tmp_path / "out.csv",
            '  query: "SELECT 1"\n  query_file: queries/active_items.sql',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        load_pipeline(path)


def test_query_file_resolves_relative_to_pipeline_directory_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    query_dir = project_dir / "queries"
    query_dir.mkdir(parents=True)
    database = _make_sqlite_db(project_dir)
    (query_dir / "active_items.sql").write_text(
        "SELECT id, status FROM items WHERE status = 'active'", encoding="utf-8"
    )
    pipeline_path = project_dir / "pipeline.yaml"
    pipeline_path.write_text(
        _sql_pipeline_yaml(
            "nested",
            database,
            project_dir / "out.csv",
            "  query_file: queries/active_items.sql",
        ),
        encoding="utf-8",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_pipeline(pipeline_path)

    assert isinstance(config.source, SqlSourceConfig)
    assert config.source.query == "SELECT id, status FROM items WHERE status = 'active'"


def test_existing_inline_query_pipeline_is_unaffected(tmp_path: Path) -> None:
    """Regression: a pipeline that never mentions `query_file` behaves
    exactly as before this story -- `query_file` defaults to None and the
    new validator branch never triggers."""
    database = _make_sqlite_db(tmp_path)
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        _sql_pipeline_yaml(
            "inline_only",
            database,
            tmp_path / "out.csv",
            "  query: \"SELECT id, status FROM items WHERE status = 'active'\"",
        ),
        encoding="utf-8",
    )
    config = load_pipeline(path)
    assert isinstance(config.source, SqlSourceConfig)
    assert config.source.query_file is None
    assert config.source.query == "SELECT id, status FROM items WHERE status = 'active'"


# -- NXL-82: parameterized SQL queries --------------------------------------


def test_static_parameter_executes_correctly_against_a_real_database(tmp_path: Path) -> None:
    """A date-range-filtered extract -- the acceptance criteria's own worked
    example -- using static YAML-declared parameter values, run against a
    real sqlite database, not mocked."""
    from nexolith.execution import DefaultPipelineRunner
    from nexolith.models import ExecutionStatus

    database = tmp_path / "orders.db"
    import sqlite3

    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE orders (id INTEGER, created_at TEXT)")
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?)",
        [(1, "2026-01-05"), (2, "2026-02-15"), (3, "2026-03-20")],
    )
    connection.commit()
    connection.close()

    path = tmp_path / "pipeline.yaml"
    path.write_text(
        f"""
name: date_range_extract
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  query: >-
    SELECT id, created_at FROM orders
    WHERE created_at >= :start_date AND created_at < :end_date
  parameters:
    start_date: "2026-01-01"
    end_date: "2026-03-01"
transformations: []
destination:
  type: csv
  path: {(tmp_path / "out.csv").as_posix()}
""",
        encoding="utf-8",
    )

    config = load_pipeline(path)
    assert isinstance(config.source, SqlSourceConfig)
    assert config.source.parameters == {"start_date": "2026-01-01", "end_date": "2026-03-01"}

    result = DefaultPipelineRunner().run(config)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_read == 2


def test_missing_required_parameter_caught_at_validation(tmp_path: Path) -> None:
    """A parameter declared with no static value (`null`) and no override
    supplied must fail at `load_pipeline` time, naming the parameter --
    never a runtime driver error."""
    database = _make_sqlite_db(tmp_path)
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        f"""
name: needs_param
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  query: "SELECT * FROM items WHERE status = :status"
  parameters:
    status: null
transformations: []
destination:
  type: csv
  path: {(tmp_path / "out.csv").as_posix()}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Missing required parameter.*status"):
        load_pipeline(path)


def test_missing_required_parameter_resolved_by_override(tmp_path: Path) -> None:
    """The same declared-but-unset parameter, supplied via an override (the
    mechanism `DagTaskConfig.parameters` uses), resolves cleanly."""
    database = _make_sqlite_db(tmp_path)
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        f"""
name: needs_param
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  query: "SELECT * FROM items WHERE status = :status"
  parameters:
    status: null
transformations: []
destination:
  type: csv
  path: {(tmp_path / "out.csv").as_posix()}
""",
        encoding="utf-8",
    )
    config = load_pipeline(path, parameter_overrides={"status": "active"})
    assert isinstance(config.source, SqlSourceConfig)
    assert config.source.parameters == {"status": "active"}


def test_unknown_parameter_override_is_rejected(tmp_path: Path) -> None:
    database = _make_sqlite_db(tmp_path)
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        f"""
name: no_params
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  query: "SELECT * FROM items"
transformations: []
destination:
  type: csv
  path: {(tmp_path / "out.csv").as_posix()}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Unknown parameter override"):
        load_pipeline(path, parameter_overrides={"status": "active"})


def test_parameters_work_with_query_file(tmp_path: Path) -> None:
    """Parameters apply identically whether the query is inline or
    file-based (story 1 + story 2 composing correctly)."""
    from nexolith.execution import DefaultPipelineRunner
    from nexolith.models import ExecutionStatus

    database = _make_sqlite_db(tmp_path)
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "by_status.sql").write_text(
        "SELECT id, status FROM items WHERE status = :status", encoding="utf-8"
    )
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        f"""
name: by_status
source:
  type: sqlite
  connection_url: sqlite:///{database.as_posix()}
  query_file: queries/by_status.sql
  parameters:
    status: "active"
transformations: []
destination:
  type: csv
  path: {(tmp_path / "out.csv").as_posix()}
""",
        encoding="utf-8",
    )

    config = load_pipeline(path)
    assert isinstance(config.source, SqlSourceConfig)
    assert config.source.query == "SELECT id, status FROM items WHERE status = :status"
    assert config.source.parameters == {"status": "active"}

    result = DefaultPipelineRunner().run(config)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_read == 2
