from __future__ import annotations

import shutil
import sqlite3
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexolith.application import PipelineApplication
from nexolith.cli.app import app
from nexolith.config import load_pipeline
from nexolith.config.models import NexoFunctionDestinationConfig
from nexolith.connectors.sql import SqlDestination
from nexolith.exceptions import ConfigurationError, ConnectorError, ExecutionError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus
from nexolith.nexofunctions import NexoFunctionDefinition, NexoFunctionResult
from nexolith.nexofunctions.loader import (
    discover_local_nexo_functions,
    load_nexo_function_registry,
)
from nexolith.nexofunctions.registry import builtin_nexo_function_registry
from nexolith.types import Rows


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _write_source(project: Path, rows: str = "id,name\n1,new\n2,second\n") -> None:
    (project / "input.csv").write_text(rows, encoding="utf-8")


def _write_pipeline(
    project: Path,
    database: Path,
    *,
    function: str = "upsert",
    parameters: str = "  parameters:\n    conflict_keys: [id]\n",
) -> Path:
    path = project / "pipeline.yaml"
    path.write_text(
        f"""name: function_pipeline
source:
  type: csv
  path: input.csv
transformations: []
destination:
  type: nexofunction.{function}
  target:
    type: sqlite
    connection_url: {_sqlite_url(database)}
    table: items
{parameters}""",
        encoding="utf-8",
    )
    return path


def _write_local_function(
    directory: Path,
    filename: str,
    *,
    name: str,
    body: str = "return NexoFunctionResult(rows_written=len(rows))",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        textwrap.dedent(
            f"""
            from nexolith.nexofunctions import (
                NexoFunctionDefinition,
                NexoFunctionResult,
            )

            def run(rows, context):
                {body}

            NEXO_FUNCTION = NexoFunctionDefinition(name={name!r}, execute=run)
            """
        ),
        encoding="utf-8",
    )
    return path


def _fetch_rows(database: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT id, name FROM items ORDER BY id").fetchall()
    finally:
        connection.close()


def _assert_sensitive_absent(text: str, values: list[str]) -> None:
    if any(value in text for value in values):
        raise AssertionError("Sensitive Nexo Function information was exposed")


def test_builtin_registry_is_deterministic_and_supports_public_lookup() -> None:
    first = builtin_nexo_function_registry()
    second = builtin_nexo_function_registry()

    assert first.names == second.names == ("upsert", "truncate_write")
    assert first.lookup("nexofunction.upsert").name == "upsert"
    assert first.lookup("nexofunction.truncate_write").name == "truncate_write"


@pytest.mark.parametrize(
    "identifier",
    ["upsert", "nexofunction.", "nexofunction.Bad", "nexofunction.bad-name"],
)
def test_registry_rejects_invalid_public_identifiers(identifier: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid Nexo Function identifier") as captured:
        builtin_nexo_function_registry().lookup(identifier)
    assert isinstance(captured.value.__cause__, ValueError)


def test_registry_reports_unknown_function_with_chaining() -> None:
    with pytest.raises(ConfigurationError, match="Unknown Nexo Function") as captured:
        builtin_nexo_function_registry().lookup("nexofunction.missing")
    assert isinstance(captured.value.__cause__, KeyError)


def test_local_discovery_is_sorted_and_independent_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    local = project / "nexofunctions"
    _write_local_function(local, "z_last.py", name="z_last")
    _write_local_function(local, "a_first.py", name="a_first")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    definitions = discover_local_nexo_functions(project)

    assert tuple(definition.name for definition in definitions) == ("a_first", "z_last")
    assert load_nexo_function_registry(project).lookup("nexofunction.a_first").name == "a_first"


def test_local_function_intentionally_overrides_builtin(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_source(project)
    _write_local_function(project / "nexofunctions", "override.py", name="upsert")
    pipeline = _write_pipeline(project, project / "unused.db", parameters="")

    config = load_pipeline(pipeline)
    result = DefaultPipelineRunner().run(config)

    assert isinstance(config.destination, NexoFunctionDestinationConfig)
    assert config.destination.resolved_function is not None
    assert config.destination.resolved_function.execute.__module__.startswith(
        "nexolith_local_function_"
    )
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_written == 2
    assert not (project / "unused.db").exists()


def test_duplicate_local_names_fail_independently_from_file_order(tmp_path: Path) -> None:
    local = tmp_path / "nexofunctions"
    _write_local_function(local, "a.py", name="duplicate")
    _write_local_function(local, "z.py", name="duplicate")

    with pytest.raises(ConfigurationError, match="Duplicate local Nexo Function.*a.py.*z.py"):
        discover_local_nexo_functions(tmp_path)


def test_malformed_and_unloadable_local_functions_are_actionable_and_redacted(
    tmp_path: Path,
) -> None:
    local = tmp_path / "sensitive-project-name" / "nexofunctions"
    local.mkdir(parents=True)
    malformed = local / "malformed.py"
    malformed.write_text("NEXO_FUNCTION = object()\n", encoding="utf-8")
    with pytest.raises(
        ConfigurationError, match="must export one NEXO_FUNCTION"
    ) as malformed_error:
        discover_local_nexo_functions(local.parent)
    _assert_sensitive_absent(str(malformed_error.value), [str(local.parent.resolve())])

    malformed.unlink()
    broken = local / "broken.py"
    broken.write_text("raise RuntimeError('recognizable_local_secret')\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="broken.py.*RuntimeError") as import_error:
        discover_local_nexo_functions(local.parent)
    assert isinstance(import_error.value.__cause__, RuntimeError)
    _assert_sensitive_absent(
        str(import_error.value),
        ["recognizable_local_secret", str(local.parent.resolve())],
    )


def test_local_import_failure_has_no_cli_traceback_or_sensitive_content(tmp_path: Path) -> None:
    project = tmp_path / "sensitive-project-directory"
    project.mkdir()
    _write_source(project)
    local = project / "nexofunctions"
    local.mkdir()
    secret = "recognizable_import_secret"
    (local / "broken.py").write_text(f"raise RuntimeError({secret!r})\n", encoding="utf-8")
    pipeline = _write_pipeline(project, project / "unused.db")

    result = CliRunner().invoke(app, ["validate", str(pipeline)])

    assert result.exit_code == 2
    _assert_sensitive_absent(
        result.output,
        [secret, str(project.resolve()), "Traceback"],
    )


def test_discovery_does_not_scan_outside_pipeline_project(tmp_path: Path) -> None:
    outside = tmp_path / "nexofunctions"
    _write_local_function(outside, "outside.py", name="outside")
    project = tmp_path / "nested" / "project"
    project.mkdir(parents=True)

    registry = load_nexo_function_registry(project)

    with pytest.raises(ConfigurationError, match="Unknown Nexo Function"):
        registry.lookup("nexofunction.outside")


def test_discovery_rejects_symlinked_local_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    link = project / "nexofunctions"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ConfigurationError, match="must be a real directory"):
        discover_local_nexo_functions(project)


def test_local_definition_declares_and_receives_only_validated_parameters(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_source(project, "id\n1\n")
    local = project / "nexofunctions"
    local.mkdir()
    (local / "count_with_label.py").write_text(
        textwrap.dedent(
            """
            from nexolith.nexofunctions import (
                NexoFunctionDefinition,
                NexoFunctionParameter,
                NexoFunctionParameterKind,
                NexoFunctionResult,
            )

            def run(rows, context):
                assert context.parameters == {"label": "trusted"}
                return NexoFunctionResult(rows_written=len(rows))

            NEXO_FUNCTION = NexoFunctionDefinition(
                name="count_with_label",
                execute=run,
                parameters=(
                    NexoFunctionParameter("label", NexoFunctionParameterKind.STRING),
                ),
            )
            """
        ),
        encoding="utf-8",
    )
    pipeline = _write_pipeline(
        project,
        project / "unused.db",
        function="count_with_label",
        parameters="  parameters:\n    label: trusted\n",
    )

    result = PipelineApplication().run_pipeline(pipeline)

    assert result.rows_written == 1
    assert not (project / "unused.db").exists()


def test_configuration_resolves_builtin_and_validates_parameters(tmp_path: Path) -> None:
    _write_source(tmp_path)
    pipeline = _write_pipeline(tmp_path, tmp_path / "items.db")

    config = load_pipeline(pipeline)

    assert isinstance(config.destination, NexoFunctionDestinationConfig)
    assert config.destination.type == "nexofunction.upsert"
    assert config.destination.target.type == "sqlite"
    assert config.destination.parameters == {"conflict_keys": ["id"]}
    assert config.destination.resolved_function is not None


@pytest.mark.parametrize(
    ("function", "parameters", "expected"),
    [
        ("upsert", "  parameters: {}\n", "missing parameter.*conflict_keys"),
        ("upsert", "  parameters:\n    conflict_keys: []\n", "requires at least 1"),
        (
            "upsert",
            "  parameters:\n    conflict_keys: [id, id]\n",
            "cannot contain duplicates",
        ),
        ("upsert", "  parameters:\n    conflict_keys: id\n", "must be string_list"),
        ("truncate_write", "  parameters:\n    source_code: never\n", "unknown parameter"),
    ],
)
def test_configuration_rejects_invalid_function_parameters_before_execution(
    tmp_path: Path, function: str, parameters: str, expected: str
) -> None:
    _write_source(tmp_path)
    pipeline = _write_pipeline(
        tmp_path,
        tmp_path / "must-not-exist.db",
        function=function,
        parameters=parameters,
    )

    with pytest.raises(ConfigurationError, match=expected):
        load_pipeline(pipeline)
    assert not (tmp_path / "must-not-exist.db").exists()


def test_upsert_pipeline_inserts_and_updates_rows(tmp_path: Path) -> None:
    database = tmp_path / "items.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    connection.execute("INSERT INTO items VALUES (1, 'old')")
    connection.commit()
    connection.close()
    _write_source(tmp_path)

    result = PipelineApplication().run_pipeline(_write_pipeline(tmp_path, database))

    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_written == 2
    assert _fetch_rows(database) == [(1, "new"), (2, "second")]


def test_upsert_supports_composite_keys(tmp_path: Path) -> None:
    database = tmp_path / "composite.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE items (tenant TEXT, id INTEGER, name TEXT NOT NULL, PRIMARY KEY (tenant, id))"
    )
    connection.execute("INSERT INTO items VALUES ('a', 1, 'old')")
    connection.commit()
    connection.close()

    rows: Rows = [
        {"tenant": "a", "id": 1, "name": "updated"},
        {"tenant": "b", "id": 1, "name": "inserted"},
    ]
    assert SqlDestination(_sqlite_url(database), "items", "append").upsert(
        rows, ("tenant", "id")
    ) == len(rows)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT tenant, id, name FROM items ORDER BY tenant, id"
        ).fetchall() == [("a", 1, "updated"), ("b", 1, "inserted")]
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("rows", "keys", "expected"),
    [
        ([], ("id",), "empty dataset"),
        ([{"name": "x"}], ("id",), "absent"),
        ([{"id": 1}], (), "at least one"),
        ([{"id": 1}], ("id", "id"), "unique"),
    ],
)
def test_upsert_rejects_invalid_rows_and_conflict_keys(
    tmp_path: Path, rows: Rows, keys: tuple[str, ...], expected: str
) -> None:
    with pytest.raises(ConnectorError, match=expected):
        SqlDestination(_sqlite_url(tmp_path / "unused.db"), "items", "append").upsert(rows, keys)


def test_upsert_requires_matching_unique_constraint(tmp_path: Path) -> None:
    database = tmp_path / "items.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(ConnectorError, match="primary key or unique constraint"):
        SqlDestination(_sqlite_url(database), "items", "append").upsert(
            [{"id": 1, "name": "x"}], ("id",)
        )


def test_upsert_rejects_columns_absent_from_destination_schema(tmp_path: Path) -> None:
    database = tmp_path / "items.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(ConnectorError, match="incoming column.*extra"):
        SqlDestination(_sqlite_url(database), "items", "append").upsert(
            [{"id": 1, "name": "x", "extra": "not-declared"}], ("id",)
        )


def test_upsert_batch_failure_rolls_back_updates_and_inserts(tmp_path: Path) -> None:
    database = tmp_path / "items.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    connection.execute("INSERT INTO items VALUES (1, 'original')")
    connection.commit()
    connection.close()

    with pytest.raises(ConnectorError, match="Could not upsert"):
        SqlDestination(_sqlite_url(database), "items", "append").upsert(
            [{"id": 1, "name": "changed"}, {"id": 2, "name": None}], ("id",)
        )
    assert _fetch_rows(database) == [(1, "original")]


def test_truncate_write_reuses_existing_schema_preserving_semantics(tmp_path: Path) -> None:
    database = tmp_path / "items.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL CHECK (length(name) > 1))"
    )
    connection.execute("INSERT INTO items VALUES (99, 'legacy')")
    connection.commit()
    connection.close()
    _write_source(tmp_path)
    pipeline = _write_pipeline(
        tmp_path,
        database,
        function="truncate_write",
        parameters="",
    )

    result = PipelineApplication().run_pipeline(pipeline)

    assert result.rows_written == 2
    assert _fetch_rows(database) == [(1, "new"), (2, "second")]
    connection = sqlite3.connect(database)
    try:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items'"
        ).fetchone()[0]
        if "CHECK (length(name) > 1)" not in schema or "PRIMARY KEY" not in schema:
            raise AssertionError("truncate_write did not preserve destination constraints")
    finally:
        connection.close()


def test_local_execution_failure_is_chained_and_cli_output_is_redacted(tmp_path: Path) -> None:
    project = tmp_path / "sensitive-project-directory"
    project.mkdir()
    _write_source(project, "id\n1\n")
    secret = "recognizable_nexo_function_secret"
    _write_local_function(
        project / "nexofunctions",
        "explode.py",
        name="explode",
        body=f"raise RuntimeError({secret!r})",
    )
    pipeline = _write_pipeline(
        project,
        project / "unused.db",
        function="explode",
        parameters="",
    )

    with pytest.raises(ExecutionError) as captured:
        PipelineApplication().run_pipeline(pipeline)
    function_error = captured.value.__cause__
    assert isinstance(function_error, ExecutionError)
    assert isinstance(function_error.__cause__, RuntimeError)
    _assert_sensitive_absent(str(captured.value), [secret, str(project.resolve())])

    result = CliRunner().invoke(app, ["run", str(pipeline)])
    assert result.exit_code == 3
    _assert_sensitive_absent(result.output, [secret, str(project.resolve()), "Traceback"])


def test_local_definition_must_return_stable_result_type(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_source(project, "id\n1\n")
    _write_local_function(
        project / "nexofunctions",
        "bad_result.py",
        name="bad_result",
        body="return rows",
    )
    pipeline = _write_pipeline(
        project,
        project / "unused.db",
        function="bad_result",
        parameters="",
    )

    with pytest.raises(ExecutionError, match="must return NexoFunctionResult") as captured:
        PipelineApplication().run_pipeline(pipeline)
    assert isinstance(captured.value.__cause__, ExecutionError)
    assert isinstance(captured.value.__cause__.__cause__, TypeError)


def test_repository_local_function_example_runs_service_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_example = Path(__file__).parents[2] / "examples" / "nexo-functions"
    copied_example = tmp_path / "examples" / "nexo-functions"
    shutil.copytree(repository_example, copied_example)
    monkeypatch.chdir(tmp_path)

    result = PipelineApplication().run_pipeline(copied_example / "pipeline.yaml")

    assert result.rows_written == 2
    assert (copied_example / "nexo-functions.db").is_file()


def test_definition_rejects_malformed_registration() -> None:
    with pytest.raises(ValueError, match="invalid function name"):
        NexoFunctionDefinition(
            name="Bad-Name", execute=lambda rows, context: NexoFunctionResult(rows_written=0)
        )
