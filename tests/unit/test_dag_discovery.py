from __future__ import annotations

import time
from pathlib import Path

from nexolith.cli.dag_discovery import discover_dag_files


def write_dag(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
name: {name}
tasks:
  - name: only
    pipeline: does_not_need_to_exist.yaml
    depends_on: []
""",
        encoding="utf-8",
    )


def write_pipeline(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
name: {name}
source:
  type: csv
  path: input.csv
destination:
  type: csv
  path: output.csv
""",
        encoding="utf-8",
    )


def test_discovers_real_dag_files_and_excludes_pipelines_and_unrelated_yaml(
    tmp_path: Path,
) -> None:
    dag_path = tmp_path / "workflows" / "dag.yaml"
    write_dag(dag_path, "found_dag")
    pipeline_path = tmp_path / "workflows" / "pipeline.yaml"
    write_pipeline(pipeline_path, "excluded_pipeline")
    unrelated_path = tmp_path / "notes.yaml"
    unrelated_path.parent.mkdir(parents=True, exist_ok=True)
    unrelated_path.write_text("just: some_config\nother: 1\n", encoding="utf-8")

    found = discover_dag_files(tmp_path)

    assert found == [dag_path]


def test_discovers_dag_files_at_multiple_depths_sorted_deterministically(
    tmp_path: Path,
) -> None:
    shallow = tmp_path / "a_dag.yaml"
    write_dag(shallow, "shallow_dag")
    deep = tmp_path / "nested" / "deeper" / "z_dag.yaml"
    write_dag(deep, "deep_dag")

    found = discover_dag_files(tmp_path)

    assert found == sorted([shallow, deep])


def test_skips_excluded_directories_entirely(tmp_path: Path) -> None:
    """A `.git`-like directory (and every other name in `_SKIP_DIR_NAMES`)
    must never even be descended into -- a DAG-shaped YAML planted inside
    one must not appear in results, confirmed with a real fixture tree, not
    just that the skip-name set contains the right strings.
    """
    real_dag = tmp_path / "workflows" / "dag.yaml"
    write_dag(real_dag, "real_dag")

    for skipped_dir_name in (".git", ".venv", "__pycache__", "node_modules", "build"):
        write_dag(tmp_path / skipped_dir_name / "hidden_dag.yaml", "hidden_dag")

    found = discover_dag_files(tmp_path)

    assert found == [real_dag]


def test_yml_extension_is_also_recognized(tmp_path: Path) -> None:
    dag_path = tmp_path / "dag.yml"
    write_dag(dag_path, "yml_dag")

    assert discover_dag_files(tmp_path) == [dag_path]


def test_non_yaml_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("not yaml at all", encoding="utf-8")
    (tmp_path / "script.py").write_text("print('hi')", encoding="utf-8")

    assert discover_dag_files(tmp_path) == []


def test_empty_tree_returns_no_results(tmp_path: Path) -> None:
    assert discover_dag_files(tmp_path) == []


def test_discovery_across_the_real_repository_completes_quickly_and_finds_real_dags() -> None:
    """Step 4 (NXL-107): a real timing measurement against this actual
    repository's tree (examples/, src/, tests/, and everything else) --
    not a synthetic fixture. 2 seconds is a generous bound for a plain
    `os.walk()` over a project this size (a few thousand files at most,
    including venv-adjacent directories this function itself prunes) on
    any machine capable of running this test suite at all; a regression
    that made discovery e.g. re-read every file's content unconditionally,
    or stopped pruning skipped directories, would blow well past it.
    """
    repository_root = Path(__file__).parents[2]

    started = time.perf_counter()
    found = discover_dag_files(repository_root)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"discover_dag_files() took {elapsed:.3f}s -- too slow"
    # Real, known DAG files in this repository -- confirms the walk actually
    # reached examples/ and correctly classified real content, not just that
    # it returned quickly by finding nothing.
    found_names = {path.name for path in found}
    assert "dag.yaml" in found_names  # examples/medallion/dag.yaml
    assert all(path.is_file() for path in found)
