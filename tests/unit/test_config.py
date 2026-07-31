from pathlib import Path

import pytest

from nexolith.config import load_pipeline
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
