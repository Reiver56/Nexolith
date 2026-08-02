from pathlib import Path

import pytest

from nexolith.dag import load_dag
from nexolith.exceptions import ConfigurationError


def write_pipeline(path: Path, *, name: str = "p", valid: bool = True) -> None:
    source = path.with_suffix(".csv")
    source.write_text("id,status\n1,ready\n", encoding="utf-8")
    if valid:
        path.write_text(
            f"""
name: {name}
source:
  type: csv
  path: {source.as_posix()}
transformations: []
destination:
  type: csv
  path: {(path.parent / f"{name}_out.csv").as_posix()}
""",
            encoding="utf-8",
        )
    else:
        # Missing 'destination' -- fails PipelineConfig's own required-field
        # validation, exactly the kind of underlying error a DAG reference
        # must surface, not swallow.
        path.write_text(
            f"""
name: {name}
source:
  type: csv
  path: {source.as_posix()}
transformations: []
""",
            encoding="utf-8",
        )


def write_dag(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_valid_linear_dag(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    write_pipeline(tmp_path / "c.yaml", name="c")
    write_dag(
        tmp_path / "workflow.yaml",
        """
name: linear
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: b
    pipeline: b.yaml
    depends_on: [a]
  - name: c
    pipeline: c.yaml
    depends_on: [b]
""",
    )

    dag = load_dag(tmp_path / "workflow.yaml")

    assert dag.name == "linear"
    assert [task.name for task in dag.tasks] == ["a", "b", "c"]
    assert dag.tasks[1].depends_on == ["a"]
    assert dag.tasks[2].depends_on == ["b"]


def test_valid_branching_and_merging_dag(tmp_path: Path) -> None:
    """extract -> {transform_x, transform_y} -> merge (fan-out then fan-in)."""
    for name in ("extract", "transform_x", "transform_y", "merge"):
        write_pipeline(tmp_path / f"{name}.yaml", name=name)
    write_dag(
        tmp_path / "workflow.yaml",
        """
name: fan-out-fan-in
tasks:
  - name: extract
    pipeline: extract.yaml
    depends_on: []
  - name: transform_x
    pipeline: transform_x.yaml
    depends_on: [extract]
  - name: transform_y
    pipeline: transform_y.yaml
    depends_on: [extract]
  - name: merge
    pipeline: merge.yaml
    depends_on: [transform_x, transform_y]
""",
    )

    dag = load_dag(tmp_path / "workflow.yaml")

    merge = next(task for task in dag.tasks if task.name == "merge")
    assert set(merge.depends_on) == {"transform_x", "transform_y"}


def test_dag_paths_resolve_relative_to_the_dag_files_own_directory(tmp_path: Path) -> None:
    nested = tmp_path / "pipelines"
    nested.mkdir()
    write_pipeline(nested / "a.yaml", name="a")
    write_dag(
        tmp_path / "workflow.yaml",
        """
name: single
tasks:
  - name: a
    pipeline: pipelines/a.yaml
    depends_on: []
""",
    )

    dag = load_dag(tmp_path / "workflow.yaml")

    assert dag.tasks[0].name == "a"


def test_missing_dag_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="DAG file not found"):
        load_dag(tmp_path / "missing.yaml")


def test_malformed_dag_yaml(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    write_dag(path, "name: [")
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_dag(path)


def test_empty_dag_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    write_dag(path, "name: empty\ntasks: []\n")
    with pytest.raises(ConfigurationError, match="Invalid DAG configuration"):
        load_dag(path)


def test_duplicate_task_names_are_rejected(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: dupes
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: []
  - name: a
    pipeline: a.yaml
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError, match="duplicate task name"):
        load_dag(path)


def test_self_referencing_task_is_rejected(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: self-ref
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: [a]
""",
    )
    with pytest.raises(ConfigurationError, match="cannot depend on itself"):
        load_dag(path)


def test_unknown_depends_on_target_is_rejected(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: dangling
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: [ghost]
""",
    )
    with pytest.raises(ConfigurationError, match="unknown task"):
        load_dag(path)


def test_two_node_cycle_is_detected_and_reported(tmp_path: Path) -> None:
    write_pipeline(tmp_path / "a.yaml", name="a")
    write_pipeline(tmp_path / "b.yaml", name="b")
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: two-cycle
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: [b]
  - name: b
    pipeline: b.yaml
    depends_on: [a]
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_dag(path)

    message = str(excinfo.value)
    assert "Cycle detected" in message
    assert "a -> b -> a" in message


def test_longer_cycle_is_detected_and_reported(tmp_path: Path) -> None:
    for name in ("a", "b", "c", "d"):
        write_pipeline(tmp_path / f"{name}.yaml", name=name)
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: long-cycle
tasks:
  - name: a
    pipeline: a.yaml
    depends_on: [b]
  - name: b
    pipeline: b.yaml
    depends_on: [c]
  - name: c
    pipeline: c.yaml
    depends_on: [d]
  - name: d
    pipeline: d.yaml
    depends_on: [a]
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_dag(path)

    message = str(excinfo.value)
    assert "Cycle detected" in message
    assert "a -> b -> c -> d -> a" in message


def test_dag_referencing_a_nonexistent_pipeline_file(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: missing-pipeline
tasks:
  - name: a
    pipeline: does_not_exist.yaml
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_dag(path)

    message = str(excinfo.value)
    assert "Task 'a'" in message
    assert "does_not_exist.yaml" in message
    assert "not found" in message


def test_dag_referencing_an_individually_invalid_pipeline_surfaces_the_underlying_error(
    tmp_path: Path,
) -> None:
    write_pipeline(tmp_path / "bad.yaml", name="bad", valid=False)
    path = tmp_path / "workflow.yaml"
    write_dag(
        path,
        """
name: bad-pipeline
tasks:
  - name: bad
    pipeline: bad.yaml
    depends_on: []
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_dag(path)

    message = str(excinfo.value)
    assert "Task 'bad'" in message
    assert "bad.yaml" in message
    # The underlying PipelineConfig validation error (missing 'destination')
    # must actually surface, not be swallowed by the DAG-level wrapping.
    assert "destination" in message
